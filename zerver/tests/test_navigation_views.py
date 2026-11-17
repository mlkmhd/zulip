import os
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from urllib.parse import quote, urlencode, urlsplit

import orjson
import pika
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.test import LiveServerTestCase

from zerver.actions.navigation_views import do_add_navigation_view
from zerver.lib.navigation_views import get_navigation_views_for_user
from zerver.lib.test_classes import ZulipTestCase, ZulipTransactionTestCase
from zerver.lib.test_rust_http import request_test_http, rust_http_service
from zerver.models import NavigationView, RealmAuditLog, UserProfile
from zerver.models.realm_audit_logs import AuditLogEventType


class NavigationViewReadHTTPTest(ZulipTransactionTestCase, LiveServerTestCase):
    def test_get_navigation_views_parity_with_rust(self) -> None:
        user = self.example_user("hamlet")
        other_user = self.example_user("othello")
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None

        def api_auth(profile: UserProfile) -> dict[str, str]:
            token = b64encode(f"{profile.delivery_email}:{profile.api_key}".encode()).decode()
            return {"Authorization": f"Basic {token}"}

        with TemporaryDirectory() as directory, ExitStack() as cleanup:
            session = SessionStore()
            session.update(
                {
                    "_auth_user_id": str(user.id),
                    "_auth_user_backend": "zproject.backends.DevAuthBackend",
                    "_auth_user_hash": user.get_session_auth_hash(),
                }
            )
            session.create()
            cleanup.callback(session.delete)
            other_view = NavigationView.objects.create(
                user=other_user, fragment="narrow/is/mentioned", is_pinned=True
            )
            cleanup.callback(other_view.delete)
            secret = Path(directory) / "secret"
            secret.write_text(settings.SECRET_KEY)
            service = (
                rust_http_service(
                    {
                        "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
                        "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                            settings.AUTHENTICATION_BACKENDS
                        ).decode(),
                    }
                )
                if os.environ.get("ZULIP_RUST_HTTP_BIN")
                else nullcontext(None)
            )
            with service as rust_port:
                api = "/api/v1/navigation_views"
                web = "/json/navigation_views"
                cookie = {"Cookie": f"{settings.SESSION_COOKIE_NAME}={session.session_key}"}

                def check(method: str, path: str, headers: dict[str, str], status: int) -> bytes:
                    django = request_test_http(django_port, method, user.realm.host, headers, path)
                    self.assertEqual(django[0], status, django[1])
                    if rust_port is not None:
                        rust = request_test_http(rust_port, method, user.realm.host, headers, path)
                        self.assertEqual((rust[0], rust[2]), (django[0], django[2]))
                        if method == "GET" and status == 200:
                            rust_body, django_body = orjson.loads(rust[1]), orjson.loads(django[1])
                            self.assertCountEqual(
                                rust_body.pop("navigation_views"),
                                django_body.pop("navigation_views"),
                            )
                            self.assertEqual(rust_body, django_body)
                        else:
                            self.assertEqual(rust[1], django[1])
                    return django[1]

                self.assertEqual(
                    orjson.loads(check("GET", api, api_auth(user), 200))["navigation_views"], []
                )
                built_in = NavigationView.objects.create(
                    user=user, fragment="inbox", is_pinned=True
                )
                cleanup.callback(built_in.delete)
                custom = NavigationView.objects.create(
                    user=user, fragment="narrow/is/alerted", name="یادآوری‌ها", is_pinned=False
                )
                cleanup.callback(custom.delete)
                for path, credentials in ((api, api_auth(user)), (web, cookie)):
                    with self.subTest(path=path):
                        data = orjson.loads(check("GET", path, credentials, 200))
                        self.assertCountEqual(
                            data["navigation_views"],
                            [
                                {"fragment": "inbox", "name": None, "is_pinned": True},
                                {
                                    "fragment": custom.fragment,
                                    "name": custom.name,
                                    "is_pinned": False,
                                },
                            ],
                        )
                        self.assertEqual(check("HEAD", path, credentials, 200), b"")
                        check("GET", path, {}, 401)

                data = orjson.loads(check("GET", api, api_auth(other_user), 200))
                self.assertEqual(
                    data["navigation_views"],
                    [{"fragment": other_view.fragment, "name": None, "is_pinned": True}],
                )

                custom.is_pinned = True
                custom.save(update_fields=["is_pinned"])
                data = orjson.loads(check("GET", web, cookie, 200))
                self.assertIn(
                    {"fragment": custom.fragment, "name": custom.name, "is_pinned": True},
                    data["navigation_views"],
                )


@skipUnless(os.environ.get("ZULIP_RUST_ACTIVITY_TEST_VHOST"), "RabbitMQ test vhost is unavailable")
class NavigationViewWriteHTTPTest(ZulipTransactionTestCase, LiveServerTestCase):
    def test_create_navigation_view_parity_with_rust(self) -> None:
        vhost = os.environ["ZULIP_RUST_ACTIVITY_TEST_VHOST"]
        assert vhost.startswith("zulip-rust-test-")
        password = settings.RABBITMQ_PASSWORD
        assert password is not None
        amqp_url = (
            f"amqp://{quote(settings.RABBITMQ_USERNAME, safe='')}:{quote(password, safe='')}"
            f"@{settings.RABBITMQ_HOST}:{settings.RABBITMQ_PORT}/{quote(vhost, safe='')}"
        )
        user = self.example_user("hamlet")
        other = self.example_user("othello")
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None
        api = "/api/v1/navigation_views"
        web = "/json/navigation_views"
        token = "a" * 32
        basic = {
            "Authorization": "Basic "
            + b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode(),
        }
        views = NavigationView.objects.filter(user=user)
        audits = RealmAuditLog.objects.filter(
            acting_user=user, event_type=AuditLogEventType.NAVIGATION_VIEW_CREATED
        )
        session = SessionStore()
        session.update(
            {
                "_auth_user_id": str(user.id),
                "_auth_user_backend": "zproject.backends.DevAuthBackend",
                "_auth_user_hash": user.get_session_auth_hash(),
            }
        )
        session.create()
        with (
            ExitStack() as cleanup,
            self.settings(
                USING_RABBITMQ=True,
                RABBITMQ_VHOST=vhost,
                TORNADO_PROCESSES=1,
                SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
            ),
            pika.BlockingConnection(pika.URLParameters(amqp_url)) as broker,
            TemporaryDirectory() as directory,
        ):
            cleanup.callback(session.delete)
            cleanup.callback(views.delete)
            cleanup.callback(audits.delete)
            private = NavigationView.objects.create(
                user=other, fragment="narrow/is/alerted", is_pinned=False, name="یادآوری‌ها"
            )
            cleanup.callback(private.delete)
            channel = broker.channel()
            for queue in ("notify_tornado", "user_activity"):
                channel.queue_declare(queue, durable=True)
                channel.queue_purge(queue)
            secret = Path(directory) / "secret"
            secret.write_text(settings.SECRET_KEY)
            environment = {
                "ZULIP_RUST_NAVIGATION_VIEW_POSTS": "1",
                "ZULIP_RUST_AMQP_URL": amqp_url,
                "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
                "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                    settings.AUTHENTICATION_BACKENDS
                ).decode(),
            }
            cookie = {
                "Cookie": f"{settings.SESSION_COOKIE_NAME}={session.session_key}; {settings.CSRF_COOKIE_NAME}={token}",
            }
            form_header = {"Content-Type": "application/x-www-form-urlencoded"}

            def check(
                port: int, path: str, auth: dict[str, str]
            ) -> list[tuple[int, bytes, dict[str, str | None]]]:
                views.delete()
                audits.delete()
                results = []
                count = 0

                def send(
                    form: dict[str, str],
                    status: int,
                    expected_view: dict[str, str | bool | None] | None = None,
                    *,
                    headers: dict[str, str] = auth,
                    query: str = "",
                ) -> None:
                    nonlocal count
                    result = request_test_http(
                        port,
                        "POST",
                        user.realm.host,
                        {**headers, **form_header},
                        path + query,
                        body=urlencode(form),
                    )
                    self.assertEqual(result[0], status, result[1])
                    results.append(result[:3])
                    event = channel.basic_get("notify_tornado", auto_ack=True)[2]
                    if expected_view is None:
                        self.assertIsNone(event)
                    else:
                        count += 1
                        fragment = expected_view["fragment"]
                        self.assertEqual(
                            views.values("fragment", "is_pinned", "name").get(fragment=fragment),
                            expected_view,
                        )
                        audit = audits.get(extra_data={"fragment": fragment})
                        self.assertEqual(audit.realm_id, user.realm_id)
                        self.assertEqual(audit.modified_user_id, user.id)
                        self.assertFalse(audit.backfilled)
                        assert event is not None
                        self.assertEqual(
                            orjson.loads(event),
                            {
                                "event": {
                                    "type": "navigation_view",
                                    "op": "add",
                                    "navigation_view": expected_view,
                                },
                                "users": [user.id],
                            },
                        )
                        self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[2])
                    self.assertEqual(views.count(), count)
                    self.assertEqual(audits.count(), count)

                if path == web:
                    send(
                        {"fragment": "inbox", "is_pinned": "true"},
                        403,
                        headers={key: value for key, value in auth.items() if key != "X-CSRFToken"},
                    )
                else:
                    send({"fragment": "inbox", "is_pinned": "true"}, 401, headers={})
                send({}, 400)
                send({"fragment": " \u2003 "}, 400)
                send({"fragment": "inbox"}, 400)
                send({"fragment": "inbox", "is_pinned": "True"}, 400)
                send({"fragment": "inbox", "is_pinned": "1"}, 400)
                send({"fragment": "inbox", "is_pinned": "true", "name": "Inbox"}, 400)
                send({"fragment": "search", "is_pinned": "false"}, 400)
                send({"fragment": "search", "is_pinned": "false", "name": " "}, 400)
                send(
                    {"fragment": " inbox ", "is_pinned": "true", "extra": "ignored"},
                    200,
                    {"fragment": "inbox", "is_pinned": True, "name": None},
                    query="?is_pinned=false",
                )
                send({"fragment": "inbox", "is_pinned": "false"}, 400)
                send(
                    {"name": " یادآوری‌ها ", "is_pinned": "false"},
                    200,
                    {"fragment": private.fragment, "is_pinned": False, "name": private.name},
                    query="?fragment=narrow%2Fis%2Falerted",
                )
                send({"fragment": "other", "is_pinned": "false", "name": "یادآوری‌ها"}, 400)
                send({"method": "GET"}, 200)
                send({"method": "DELETE"}, 405)
                return results

            enabled = os.environ.get("ZULIP_RUST_HTTP_BIN")
            for scheme in ("http", "https"):
                web_auth = {**cookie, "X-CSRFToken": token}
                if scheme == "https":
                    web_auth.update(
                        {"X-Forwarded-Proto": "https", "Referer": f"https://{user.realm.host}/"}
                    )
                service = (
                    rust_http_service({**environment, "ZULIP_RUST_WEB_POST_SCHEME": scheme})
                    if enabled
                    else nullcontext(None)
                )
                with service as rust_port:
                    routes = (
                        [(api, basic), (web, web_auth)] if scheme == "http" else [(web, web_auth)]
                    )
                    for path, headers in routes:
                        with self.subTest(path=path, scheme=scheme):
                            django = check(django_port, path, headers)
                            if rust_port is not None:
                                self.assertEqual(check(rust_port, path, headers), django)

            if enabled:
                views.delete()
                audits.delete()
                with (
                    rust_http_service(environment) as first,
                    rust_http_service(environment) as second,
                    ThreadPoolExecutor(max_workers=2) as requests,
                ):
                    for names, fragments in [
                        (("Same", "Same"), ("first", "second")),
                        (("One", "Two"), ("same", "same")),
                    ]:
                        responses = [
                            requests.submit(
                                request_test_http,
                                port,
                                "POST",
                                user.realm.host,
                                {**basic, **form_header},
                                api,
                                body=urlencode(
                                    {"fragment": fragment, "name": name, "is_pinned": "true"}
                                ),
                            )
                            for port, fragment, name in zip(
                                (first, second), fragments, names, strict=True
                            )
                        ]
                        self.assertCountEqual(
                            [response.result()[0] for response in responses], [200, 400]
                        )
                        self.assertEqual(views.count(), 1)
                        self.assertEqual(audits.count(), 1)
                        self.assertIsNotNone(channel.basic_get("notify_tornado", auto_ack=True)[2])
                        self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[2])
                        views.delete()
                        audits.delete()
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "ALTER TABLE zerver_realmauditlog ADD CONSTRAINT reject_navigation_view_test "
                            "CHECK (event_type <> 850) NOT VALID"
                        )
                        try:
                            result = request_test_http(
                                first,
                                "POST",
                                user.realm.host,
                                {**basic, **form_header},
                                api,
                                body=urlencode({"fragment": "inbox", "is_pinned": "true"}),
                            )
                            self.assertEqual(result[0], 503, result[1])
                            self.assertFalse(views.exists())
                            self.assertFalse(audits.exists())
                            self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[2])
                        finally:
                            cursor.execute(
                                "ALTER TABLE zerver_realmauditlog DROP CONSTRAINT reject_navigation_view_test"
                            )
            channel.queue_purge("user_activity")

    def test_navigation_view_detail_parity_with_rust(self) -> None:
        vhost = os.environ["ZULIP_RUST_ACTIVITY_TEST_VHOST"]
        assert vhost.startswith("zulip-rust-test-")
        password = settings.RABBITMQ_PASSWORD
        assert password is not None
        amqp_url = (
            f"amqp://{quote(settings.RABBITMQ_USERNAME, safe='')}:{quote(password, safe='')}"
            f"@{settings.RABBITMQ_HOST}:{settings.RABBITMQ_PORT}/{quote(vhost, safe='')}"
        )
        user = self.example_user("hamlet")
        other = self.example_user("othello")
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None
        api = "/api/v1/navigation_views"
        web = "/json/navigation_views"
        fragment = "narrow/search/دو views"
        literal_slashes = quote(fragment)
        encoded_slashes = quote(fragment, safe="")
        basic = {
            "Authorization": "Basic "
            + b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode(),
        }
        session = SessionStore()
        session.update(
            {
                "_auth_user_id": str(user.id),
                "_auth_user_backend": "zproject.backends.DevAuthBackend",
                "_auth_user_hash": user.get_session_auth_hash(),
            }
        )
        session.create()
        token = "a" * 32
        browser = {
            "Cookie": f"{settings.SESSION_COOKIE_NAME}={session.session_key}; {settings.CSRF_COOKIE_NAME}={token}",
            "X-CSRFToken": token,
        }
        views = NavigationView.objects.filter(user=user)
        audits = RealmAuditLog.objects.filter(acting_user=user)
        with (
            ExitStack() as cleanup,
            self.settings(
                USING_RABBITMQ=True,
                RABBITMQ_VHOST=vhost,
                TORNADO_PROCESSES=1,
                SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
            ),
            pika.BlockingConnection(pika.URLParameters(amqp_url)) as broker,
            TemporaryDirectory() as directory,
        ):
            cleanup.callback(session.delete)
            cleanup.callback(views.delete)
            cleanup.callback(audits.delete)
            private = NavigationView.objects.create(user=other, fragment="recent", is_pinned=True)
            cleanup.callback(private.delete)
            channel = broker.channel()
            for queue in ("notify_tornado", "user_activity"):
                channel.queue_declare(queue, durable=True)
                channel.queue_purge(queue)
            secret = Path(directory) / "secret"
            secret.write_text(settings.SECRET_KEY)
            environment = {
                "ZULIP_RUST_NAVIGATION_VIEW_DETAILS": "1",
                "ZULIP_RUST_AMQP_URL": amqp_url,
                "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
                "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                    settings.AUTHENTICATION_BACKENDS
                ).decode(),
            }

            def assert_event(event: dict[str, object] | None) -> None:
                payload = channel.basic_get("notify_tornado", auto_ack=True)[2]
                if event is None:
                    self.assertIsNone(payload)
                    return
                assert payload is not None
                self.assertEqual(
                    orjson.loads(payload),
                    {"users": [user.id], "event": {"type": "navigation_view", **event}},
                )
                self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[2])

            def check(
                port: int, path: str, auth: dict[str, str]
            ) -> list[tuple[int, bytes, dict[str, str | None]]]:
                views.delete()
                audits.delete()
                view = NavigationView.objects.create(
                    user=user, fragment=fragment, name="Original", is_pinned=False
                )
                NavigationView.objects.create(user=user, fragment="inbox", is_pinned=True)
                results = []

                def send(
                    method: str,
                    suffix: str,
                    status: int,
                    form: dict[str, str] | None = None,
                    *,
                    headers: dict[str, str] = auth,
                    event: dict[str, object] | None = None,
                ) -> None:
                    result = request_test_http(
                        port,
                        method,
                        user.realm.host,
                        {**headers, "Content-Type": "application/x-www-form-urlencoded"},
                        f"{path}/{suffix}",
                        body=urlencode(form or {}),
                    )
                    self.assertEqual(result[0], status, result[1])
                    results.append(result[:3])
                    assert_event(event)

                if path == web:
                    missing_token = {
                        key: value for key, value in auth.items() if key != "X-CSRFToken"
                    }
                    send(
                        "PATCH", literal_slashes, 403, {"is_pinned": "true"}, headers=missing_token
                    )
                    send("DELETE", literal_slashes, 403, headers=missing_token)
                send("PATCH", private.fragment, 404, {"is_pinned": "false"})
                send("DELETE", private.fragment, 404)
                send("PATCH", "inbox", 400, {"name": "Custom"})
                send("PATCH", literal_slashes, 400, {"name": " "})
                send("PATCH", literal_slashes, 400, {"is_pinned": "null"})
                send("PATCH", "missing", 400, {"name": "Original"})
                send(
                    "PATCH",
                    literal_slashes,
                    200,
                    event={"op": "update", "fragment": fragment, "data": {}},
                )
                self.assertFalse(audits.exists())
                send(
                    "PATCH",
                    encoded_slashes + "?is_pinned=true",
                    200,
                    {"name": " تازه ", "fragment": private.fragment, "unknown": "ignored"},
                    event={
                        "op": "update",
                        "fragment": fragment,
                        "data": {"name": "تازه", "is_pinned": True},
                    },
                )
                view.refresh_from_db()
                self.assertEqual((view.name, view.is_pinned), ("تازه", True))
                entries = list(audits.order_by("id"))
                self.assert_length(entries, 2)
                self.assertEqual(entries[0].event_time, entries[1].event_time)
                for entry, field, previous, value in zip(
                    entries, ("name", "is_pinned"), ("Original", False), ("تازه", True), strict=True
                ):
                    self.assertEqual(entry.event_type, AuditLogEventType.NAVIGATION_VIEW_UPDATED)
                    self.assertEqual(entry.realm_id, user.realm_id)
                    self.assertEqual(entry.modified_user_id, user.id)
                    self.assertFalse(entry.backfilled)
                    self.assertEqual(
                        entry.extra_data,
                        {
                            "fragment": fragment,
                            "property": field,
                            RealmAuditLog.OLD_VALUE: previous,
                            RealmAuditLog.NEW_VALUE: value,
                        },
                    )
                send("PATCH", literal_slashes, 400, {"name": "تازه"})
                send(
                    "DELETE",
                    encoded_slashes,
                    200,
                    {"extra": "ignored"},
                    event={"op": "remove", "fragment": fragment},
                )
                self.assertFalse(views.filter(id=view.id).exists())
                self.assertTrue(
                    NavigationView.objects.filter(id=private.id, is_pinned=True).exists()
                )
                entry = audits.get(event_type=AuditLogEventType.NAVIGATION_VIEW_DELETED)
                self.assertEqual(entry.realm_id, user.realm_id)
                self.assertEqual(entry.modified_user_id, user.id)
                self.assertEqual(entry.extra_data, {"fragment": fragment})
                send("DELETE", literal_slashes, 404)
                self.assertEqual(audits.count(), 3)
                return results

            enabled = os.environ.get("ZULIP_RUST_HTTP_BIN")
            for scheme in ("http", "https"):
                auth = (
                    browser
                    if scheme == "http"
                    else {
                        **browser,
                        "X-Forwarded-Proto": "https",
                        "Referer": f"https://{user.realm.host}/",
                    }
                )
                service = (
                    rust_http_service({**environment, "ZULIP_RUST_WEB_POST_SCHEME": scheme})
                    if enabled
                    else nullcontext(None)
                )
                with service as rust_port:
                    routes = [(api, basic), (web, auth)] if scheme == "http" else [(web, auth)]
                    for path, headers in routes:
                        with self.subTest(path=path, scheme=scheme):
                            django = check(django_port, path, headers)
                            if rust_port is not None:
                                self.assertEqual(check(rust_port, path, headers), django)
            if enabled:
                NavigationView.objects.create(
                    user=user, fragment=fragment, name="Original", is_pinned=False
                )
                audits.delete()
                with (
                    rust_http_service(environment) as first,
                    rust_http_service(environment) as second,
                    ThreadPoolExecutor(max_workers=2) as requests,
                ):
                    responses = [
                        requests.submit(
                            request_test_http,
                            port,
                            "DELETE",
                            user.realm.host,
                            basic,
                            f"{api}/{encoded_slashes}",
                        )
                        for port in (first, second)
                    ]
                    self.assertCountEqual(
                        [response.result()[0] for response in responses], [200, 404]
                    )
                    self.assertFalse(views.filter(fragment=fragment).exists())
                    self.assertEqual(audits.count(), 1)
                    assert_event({"op": "remove", "fragment": fragment})
            channel.queue_purge("user_activity")


class NavigationViewTests(ZulipTestCase):
    def create_example_navigation_view(
        self, user: UserProfile, fragment: str, is_pinned: bool, name: str | None = None
    ) -> str:
        do_add_navigation_view(
            user,
            fragment,
            is_pinned,
            name,
        )
        return fragment

    def test_get_navigation_views(self) -> None:
        """Tests fetching navigation views."""
        user = self.example_user("hamlet")
        self.login_user(user)

        result = self.client_get("/json/navigation_views")
        response_dict = self.assert_json_success(result)
        self.assert_length(response_dict["navigation_views"], 0)

        # Test adding a built-in view and fetching it
        self.create_example_navigation_view(user, fragment="inbox", is_pinned=True)
        result = self.client_get("/json/navigation_views")
        response_dict = self.assert_json_success(result)
        self.assert_length(response_dict["navigation_views"], 1)
        self.assertEqual(response_dict["navigation_views"][0]["fragment"], "inbox")
        self.assertEqual(response_dict["navigation_views"][0]["is_pinned"], True)

    def test_add_navigation_view(self) -> None:
        """Tests creation of navigation views."""
        user = self.example_user("hamlet")
        self.login_user(user)

        # Test successful creation
        params = {
            "fragment": "recent",
            "is_pinned": "true",
        }
        result = self.client_post("/json/navigation_views", params)
        self.assert_json_success(result)

        # Test name value for builtin views
        params["name"] = "foo"
        result = self.client_post("/json/navigation_views", params)
        self.assert_json_error(result, "Built-in views cannot have a custom name.")

        # Test empty fragment
        params["fragment"] = ""
        result = self.client_post("/json/navigation_views", params)
        self.assert_json_error(result, "fragment cannot be blank")

        # Test no name value for custom views
        params = {
            "fragment": "narrow/view",
            "is_pinned": "true",
        }
        result = self.client_post("/json/navigation_views", params)
        self.assert_json_error(result, "Custom views must have a valid name.")

        # Test custom view with name value
        params["name"] = "foo"
        result = self.client_post("/json/navigation_views", params)
        self.assert_json_success(result)

        # Test duplicate view
        new_params = {
            "fragment": "recent",
            "is_pinned": "true",
        }
        result = self.client_post("/json/navigation_views", new_params)
        self.assert_json_error(result, "Navigation view already exists.")

        # Test duplicate view by name
        new_params = {
            "fragment": "narorw/is/attachment",
            "is_pinned": "true",
            "name": "foo",
        }
        result = self.client_post("/json/navigation_views", new_params)
        self.assert_json_error(result, "Navigation view already exists.")

    def test_update_navigation_view(self) -> None:
        """Tests updating navigation views."""
        user = self.example_user("hamlet")
        self.login_user(user)
        self.create_example_navigation_view(user, fragment="inbox", is_pinned=True)

        # Test successful update
        params = {
            "is_pinned": "false",
        }
        result = self.client_patch("/json/navigation_views/inbox", params)
        self.assert_json_success(result)

        navigation_views = get_navigation_views_for_user(user)
        self.assertEqual(navigation_views[0]["is_pinned"], False)

        params = {
            "is_pinned": "true",
            "name": "Inbox View",
        }
        result = self.client_patch("/json/navigation_views/inbox", params)
        self.assert_json_error(result, "Built-in views cannot have a custom name.")

        # Test with the name for custom views
        self.create_example_navigation_view(
            user, fragment="narrow/is/alerted", is_pinned=True, name="Alert Words"
        )
        params = {
            "is_pinned": "false",
            "name": "Watched Phrases",
        }
        result = self.client_patch("/json/navigation_views/narrow/is/alerted", params)
        self.assert_json_success(result)

        self.create_example_navigation_view(
            user, fragment="narrow/is/attachment", is_pinned=True, name="Attachments"
        )
        params = {
            "is_pinned": "false",
            "name": "Watched Phrases",
        }
        result = self.client_patch("/json/navigation_views/narrow/is/attachment", params)
        self.assert_json_error(result, "Navigation view already exists.")

        params = {
            "is_pinned": "false",
            "name": "New view",
        }
        # Test nonexistent view
        result = self.client_patch("/json/navigation_views/nonexistent", params)
        self.assert_json_error(result, "Navigation view does not exist.", status_code=404)

    def test_remove_navigation_view(self) -> None:
        """Tests removing navigation views."""
        user = self.example_user("hamlet")
        self.login_user(user)
        self.create_example_navigation_view(user, fragment="recent", is_pinned=True)

        # Test successful removal
        result = self.client_delete("/json/navigation_views/recent")
        self.assert_json_success(result)

        navigation_views = get_navigation_views_for_user(user)
        self.assert_length(navigation_views, 0)

        # Test nonexistent view
        result = self.client_delete("/json/navigation_views/nonexistent")
        self.assert_json_error(result, "Navigation view does not exist.", status_code=404)

    def test_navigation_view_permissions(self) -> None:
        """Tests permissions for navigation view operations."""
        hamlet = self.example_user("hamlet")
        othello = self.example_user("othello")
        self.login_user(othello)

        self.create_example_navigation_view(hamlet, fragment="recent", is_pinned=True)

        params = {
            "is_pinned": "false",
        }
        result = self.client_patch("/json/navigation_views/recent", params)
        self.assert_json_error(result, "Navigation view does not exist.", status_code=404)

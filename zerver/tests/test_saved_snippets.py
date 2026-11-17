import os
import re
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from tempfile import NamedTemporaryFile
from time import monotonic, sleep
from unittest import skipUnless
from urllib.parse import quote, urlencode, urlsplit

import orjson
import pika
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import connection
from django.http import HttpRequest
from django.middleware.csrf import _get_new_csrf_string, get_token
from django.test import LiveServerTestCase
from django.utils.timezone import now as timezone_now

from zerver.actions.saved_snippets import do_create_saved_snippet
from zerver.lib.test_classes import ZulipTestCase, ZulipTransactionTestCase
from zerver.lib.test_rust_http import request_test_http, rust_http_service
from zerver.models import Realm, RealmAuditLog, SavedSnippet, UserProfile
from zerver.models.realm_audit_logs import AuditLogEventType


class SavedSnippetTests(ZulipTestCase):
    def create_example_saved_snippet(self, user: UserProfile) -> int:
        saved_snippet = do_create_saved_snippet(
            "Welcome message", "**Welcome** to the organization.", user
        )
        return saved_snippet.id

    def test_create_saved_snippet(self) -> None:
        """Tests creation of saved snippets."""

        user = self.example_user("hamlet")
        self.login_user(user)

        result = self.client_get(
            "/json/saved_snippets",
        )
        response_dict = self.assert_json_success(result)
        self.assert_length(response_dict["saved_snippets"], 0)

        result = self.client_post(
            "/json/saved_snippets",
            {"title": "Welcome message", "content": "**Welcome** to the organization."},
        )
        response_dict = self.assert_json_success(result)
        saved_snippet_id = response_dict["saved_snippet_id"]

        result = self.client_get(
            "/json/saved_snippets",
        )
        response_dict = self.assert_json_success(result)
        self.assert_length(response_dict["saved_snippets"], 1)
        self.assertEqual(saved_snippet_id, response_dict["saved_snippets"][0]["id"])

        result = self.client_post(
            "/json/saved_snippets",
            {
                "title": "A" * (SavedSnippet.MAX_TITLE_LENGTH + 60),
                "content": "**Welcome** to the organization.",
            },
        )
        self.assert_json_error(
            result,
            status_code=400,
            msg=f"title is too long (limit: {SavedSnippet.MAX_TITLE_LENGTH} characters)",
        )

    def test_edit_saved_snippet(self) -> None:
        """Tests updation of saved snippets."""

        user = self.example_user("hamlet")
        self.login_user(user)
        saved_snippet_id = self.create_example_saved_snippet(user)

        result = self.client_patch(
            f"/json/saved_snippets/{saved_snippet_id}",
            {"title": "New title"},
        )
        self.assert_json_success(result)

        result = self.client_patch(
            f"/json/saved_snippets/{saved_snippet_id}", {"content": "New content"}
        )
        self.assert_json_success(result)

        # No-op requests succeed.
        result = self.client_patch(
            f"/json/saved_snippets/{saved_snippet_id}",
        )
        self.assert_json_success(result)

        # Tests if error is thrown when the provided ID does not exist.
        result = self.client_patch(
            "/json/saved_snippets/10",
            {"content": "New content"},
        )
        self.assert_json_error(result, "Saved snippet does not exist.", status_code=404)

    def test_delete_saved_snippet(self) -> None:
        """Tests deletion of saved snippets."""

        user = self.example_user("hamlet")
        self.login_user(user)
        saved_snippet_id = self.create_example_saved_snippet(user)

        result = self.client_get(
            "/json/saved_snippets",
        )
        response_dict = self.assert_json_success(result)
        self.assert_length(response_dict["saved_snippets"], 1)

        result = self.client_delete(
            f"/json/saved_snippets/{saved_snippet_id}",
        )
        self.assert_json_success(result)

        result = self.client_get(
            "/json/saved_snippets",
        )
        response_dict = self.assert_json_success(result)
        self.assert_length(response_dict["saved_snippets"], 0)

        # Tests if error is thrown when the provided ID does not exist.
        result = self.client_delete(
            "/json/saved_snippets/10",
        )
        self.assert_json_error(result, "Saved snippet does not exist.", status_code=404)


class SavedSnippetReadHTTPTest(ZulipTransactionTestCase, LiveServerTestCase):
    def test_saved_snippet_session_read_contract(self) -> None:
        user = self.example_user("hamlet")
        other = self.example_user("othello")
        empty = self.example_user("iago")
        own, foreign = SavedSnippet.objects.bulk_create(
            [
                SavedSnippet(
                    user_profile=user, realm=user.realm, title="سلام ☃", content="**Mine**"
                ),
                SavedSnippet(
                    user_profile=other, realm=other.realm, title="Private", content="**Other**"
                ),
            ]
        )
        keys: list[str] = []

        def cookie(profile: UserProfile, **overrides: str) -> dict[str, str]:
            store = SessionStore()
            store.update(
                {
                    "_auth_user_id": str(profile.id),
                    "_auth_user_backend": "zproject.backends.DevAuthBackend",
                    "_auth_user_hash": profile.get_session_auth_hash(),
                    **overrides,
                }
            )
            store.create()
            assert store.session_key is not None
            keys.append(store.session_key)
            return {"Cookie": f"{settings.SESSION_COOKIE_NAME}={store.session_key}"}

        try:
            valid = cookie(user)
            expired = cookie(user)
            Session.objects.filter(session_key=keys[-1]).update(expire_date=timezone_now())
            tampered = cookie(user)
            session = Session.objects.get(session_key=keys[-1])
            session.session_data += "x"
            session.save(update_fields=["session_data"])
            authorization = (
                "Basic " + b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
            )
            host = user.realm.host
            cases = [
                ("own", "GET", host, valid, 200, [own.to_api_dict()]),
                ("other user", "GET", host, cookie(other), 200, [foreign.to_api_dict()]),
                ("empty", "GET", host, cookie(empty), 200, []),
                ("head", "HEAD", host, valid, 200, None),
                ("missing", "GET", host, {}, 401, None),
                ("Basic only", "GET", host, {"Authorization": authorization}, 401, None),
                ("login redirect", "GET", host, {"Accept": "text/html"}, 302, None),
                ("expired", "GET", host, expired, 401, None),
                ("tampered", "GET", host, tampered, 401, None),
                (
                    "wrong session hash",
                    "GET",
                    host,
                    cookie(user, _auth_user_hash="0" * 64),
                    401,
                    None,
                ),
                (
                    "wrong realm",
                    "GET",
                    Realm.objects.get(string_id="zephyr").host,
                    valid,
                    400,
                    None,
                ),
                (
                    "safe cross-origin",
                    "GET",
                    host,
                    {**valid, "Origin": "https://other.testserver", "X-CSRFToken": "bad"},
                    200,
                    [own.to_api_dict()],
                ),
            ]
            with NamedTemporaryFile(mode="w", encoding="utf-8") as secret:
                secret.write(settings.SECRET_KEY)
                secret.flush()
                environment = {
                    "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": secret.name,
                    "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                        settings.AUTHENTICATION_BACKENDS
                    ).decode(),
                    "ZULIP_RUST_SESSION_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
                    "ZULIP_RUST_CSRF_COOKIE_NAME": settings.CSRF_COOKIE_NAME,
                }
                service = (
                    rust_http_service(environment)
                    if os.environ.get("ZULIP_RUST_HTTP_BIN")
                    else nullcontext(None)
                )
                with service as rust_port:
                    django_port = urlsplit(self.live_server_url).port
                    assert django_port is not None
                    path = "/json/saved_snippets"
                    for label, method, realm_host, headers, status, snippets in cases:
                        with self.subTest(label=label):
                            rust = (
                                request_test_http(rust_port, method, realm_host, headers, path)
                                if rust_port is not None
                                else None
                            )
                            django = request_test_http(
                                django_port, method, realm_host, headers, path
                            )
                            self.assertEqual(django[0], status)
                            if snippets is not None:
                                self.assertEqual(
                                    orjson.loads(django[1])["saved_snippets"], snippets
                                )
                            if method == "HEAD":
                                self.assertEqual(django[1], b"")
                            if rust is not None:
                                self.assertEqual(rust[:3], django[:3])
                                if django[3] is None:
                                    self.assertIsNone(rust[3])
                                else:
                                    assert rust[3] is not None
                                    drift = parsedate_to_datetime(rust[3]) - parsedate_to_datetime(
                                        django[3]
                                    )
                                    self.assertLessEqual(abs(drift.total_seconds()), 5)

                    malformed = {"Cookie": valid["Cookie"] + f"; {settings.CSRF_COOKIE_NAME}=bad"}
                    attributes = []
                    for port in (django_port, rust_port):
                        if port is None:
                            continue
                        status, body, response_headers, _ = request_test_http(
                            port, "GET", host, malformed, path
                        )
                        self.assertEqual(status, 200)
                        self.assertEqual(orjson.loads(body)["saved_snippets"], [own.to_api_dict()])
                        replacement = response_headers["Set-Cookie"]
                        assert replacement is not None
                        value, *flags = replacement.split("; ")
                        self.assertRegex(
                            value, rf"^{re.escape(settings.CSRF_COOKIE_NAME)}=[A-Za-z0-9]{{32}}$"
                        )
                        attributes.append(
                            [flag for flag in flags if not flag.startswith("expires=")]
                        )
                    if rust_port is not None:
                        self.assertEqual(attributes[0], attributes[1])
        finally:
            Session.objects.filter(session_key__in=keys).delete()
            SavedSnippet.objects.filter(id__in=[own.id, foreign.id]).delete()

    def test_saved_snippet_read_contract(self) -> None:
        user = self.example_user("hamlet")
        other = self.example_user("othello")
        empty = self.example_user("iago")
        other_realm = UserProfile.objects.filter(realm__string_id="zephyr", is_bot=False).first()
        webhook = UserProfile.objects.filter(bot_type=UserProfile.INCOMING_WEBHOOK_BOT).first()
        assert other_realm is not None and webhook is not None

        # Commit fixture rows so both HTTP servers can see them.
        modern, legacy, foreign = SavedSnippet.objects.bulk_create(
            [
                SavedSnippet(
                    user_profile=user,
                    realm=user.realm,
                    title='سلام "☃"',
                    content="**Markdown**\n📌\r\t\\",
                    date_created=datetime(2024, 2, 29, 12, 0, 0, 987654, tzinfo=UTC),
                ),
                SavedSnippet(
                    user_profile=user,
                    realm=user.realm,
                    title="Older",
                    content="Legacy content",
                    date_created=datetime(1969, 12, 31, 23, 59, 59, 250000, tzinfo=UTC),
                ),
                SavedSnippet(
                    user_profile=other,
                    realm=other.realm,
                    title="Private to a second user",
                    content="Another user's text",
                ),
            ]
        )
        try:
            self.check_saved_snippet_reads(
                user, empty, other_realm, webhook, modern, legacy, foreign
            )
        finally:
            SavedSnippet.objects.filter(id__in=[modern.id, legacy.id, foreign.id]).delete()

    def check_saved_snippet_reads(
        self,
        user: UserProfile,
        empty: UserProfile,
        other_realm: UserProfile,
        webhook: UserProfile,
        modern: SavedSnippet,
        legacy: SavedSnippet,
        foreign: SavedSnippet,
    ) -> None:
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None
        path = "/api/v1/saved_snippets"

        def auth(profile: UserProfile) -> dict[str, str]:
            credentials = f"{profile.delivery_email}:{profile.api_key}".encode()
            return {"Authorization": "Basic " + b64encode(credentials).decode()}

        own = auth(user)
        cases = [
            ("own snippets", "GET", 200, user.realm.host, own, "", [modern.id, legacy.id]),
            ("HEAD", "HEAD", 200, user.realm.host, own, "", None),
            ("empty", "GET", 200, empty.realm.host, auth(empty), "", []),
            (
                "second user",
                "GET",
                200,
                foreign.user_profile.realm.host,
                auth(foreign.user_profile),
                "",
                [foreign.id],
            ),
            ("other organization", "GET", 200, other_realm.realm.host, auth(other_realm), "", []),
            (
                "unknown parameters",
                "GET",
                200,
                user.realm.host,
                own,
                "?x=1&x=2",
                [modern.id, legacy.id],
            ),
            ("no credentials", "GET", 401, user.realm.host, {}, "", None),
            ("localized error", "GET", 401, user.realm.host, {"Accept-Language": "fr"}, "", None),
            (
                "browser redirect",
                "GET",
                302,
                user.realm.host,
                {"Accept": "text/html"},
                "?x=1",
                None,
            ),
            (
                "malformed header",
                "GET",
                401,
                user.realm.host,
                {"Authorization": "Basic bad!"},
                "",
                None,
            ),
            ("wrong organization", "GET", 401, other_realm.realm.host, own, "", None),
            ("webhook bot", "GET", 401, webhook.realm.host, auth(webhook), "", None),
        ]
        snippets = {obj.id: obj.to_api_dict() for obj in (modern, legacy, foreign)}
        self.assertEqual(snippets[legacy.id]["date_created"], 0)
        service = (
            rust_http_service({}) if os.environ.get("ZULIP_RUST_HTTP_BIN") else nullcontext(None)
        )
        with service as rust_port:
            for label, method, status, host, headers, query, expected_ids in cases:
                with self.subTest(label=label):
                    django = request_test_http(django_port, method, host, headers, path + query)
                    self.assertEqual(django[0], status)
                    if expected_ids is not None:
                        data = orjson.loads(django[1])
                        self.assertEqual(set(data), {"result", "msg", "saved_snippets"})
                        self.assertCountEqual(
                            data["saved_snippets"],
                            [snippets[snippet_id] for snippet_id in expected_ids],
                        )
                    if method == "HEAD":
                        self.assertEqual(django[1], b"")
                    if rust_port is not None:
                        rust = request_test_http(rust_port, method, host, headers, path + query)
                        self.assertEqual(rust[:3], django[:3])
                        if django[3] is None:
                            self.assertIsNone(rust[3])
                        else:
                            assert rust[3] is not None
                            drift = parsedate_to_datetime(rust[3]) - parsedate_to_datetime(
                                django[3]
                            )
                            self.assertLessEqual(abs(drift.total_seconds()), 5)


@skipUnless(os.environ.get("ZULIP_RUST_ACTIVITY_TEST_VHOST"), "RabbitMQ test vhost is unavailable")
class SavedSnippetWriteHTTPTest(ZulipTransactionTestCase, LiveServerTestCase):
    def saved_snippet_broker(self) -> tuple[str, str]:
        vhost = os.environ["ZULIP_RUST_ACTIVITY_TEST_VHOST"]
        assert vhost.startswith("zulip-rust-test-")
        username, password = settings.RABBITMQ_USERNAME, settings.RABBITMQ_PASSWORD
        assert password is not None
        amqp_url = (
            f"amqp://{quote(username, safe='')}:{quote(password, safe='')}"
            f"@{settings.RABBITMQ_HOST}:{settings.RABBITMQ_PORT}/{quote(vhost, safe='')}"
        )
        return vhost, amqp_url

    def test_saved_snippet_write_http_and_event_parity(self) -> None:
        vhost, amqp_url = self.saved_snippet_broker()
        environment = {"ZULIP_RUST_AMQP_URL": amqp_url, "ZULIP_RUST_SAVED_SNIPPET_WRITES": "1"}
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None
        user = self.example_user("hamlet")
        snippets = SavedSnippet.objects.filter(user_profile=user)
        audits = RealmAuditLog.objects.filter(
            acting_user=user, event_type=AuditLogEventType.SAVED_SNIPPET_CREATED
        )
        initial_snippet_ids = list(snippets.values_list("id", flat=True))
        initial_audit_ids = list(audits.values_list("id", flat=True))
        service = (
            rust_http_service(environment)
            if os.environ.get("ZULIP_RUST_HTTP_BIN")
            else nullcontext(None)
        )
        with (
            self.settings(USING_RABBITMQ=True, RABBITMQ_VHOST=vhost, TORNADO_PROCESSES=1),
            pika.BlockingConnection(pika.URLParameters(amqp_url)) as broker,
            service as rust_port,
        ):
            channel = broker.channel()
            channel.queue_declare("notify_tornado", durable=True)
            channel.queue_declare("user_activity", durable=True)
            channel.queue_purge("notify_tornado")
            channel.queue_purge("user_activity")
            try:
                django = self.check_create_cases(django_port, user, channel)
                django_edits = self.check_edit_cases(django_port, user, channel)
                django_deletes = self.check_delete_cases(django_port, user, channel)
                if rust_port is not None:
                    rust = self.check_create_cases(rust_port, user, channel)
                    self.assertEqual(rust, django)
                    rust_edits = self.check_edit_cases(rust_port, user, channel)
                    self.assertEqual(rust_edits, django_edits)
                    rust_deletes = self.check_delete_cases(
                        rust_port, user, channel, check_race=True
                    )
                    self.assertEqual(rust_deletes, django_deletes)
                    self.check_failed_insert(rust_port, user, channel)
                    self.check_unroutable_event(rust_port, user, channel)
            finally:
                channel.queue_purge("notify_tornado")
                channel.queue_purge("user_activity")
                audits.exclude(id__in=initial_audit_ids).delete()
                snippets.exclude(id__in=initial_snippet_ids).delete()

    def test_saved_snippet_web_post_http_and_event_parity(self) -> None:
        vhost, amqp_url = self.saved_snippet_broker()
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None
        user = self.example_user("hamlet")
        store = SessionStore()
        store.update(
            {
                "_auth_user_id": str(user.id),
                "_auth_user_backend": "zproject.backends.DevAuthBackend",
                "_auth_user_hash": user.get_session_auth_hash(),
            }
        )
        store.create()
        session = f"{settings.SESSION_COOKIE_NAME}={store.session_key}"
        with (
            self.settings(
                USING_RABBITMQ=True,
                RABBITMQ_VHOST=vhost,
                TORNADO_PROCESSES=1,
                SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
            ),
            pika.BlockingConnection(pika.URLParameters(amqp_url)) as broker,
            NamedTemporaryFile(mode="w", encoding="utf-8") as secret,
        ):
            secret.write(settings.SECRET_KEY)
            secret.flush()
            environment = {
                "ZULIP_RUST_AMQP_URL": amqp_url,
                "ZULIP_RUST_SAVED_SNIPPET_WRITES": "1",
                "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": secret.name,
                "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                    settings.AUTHENTICATION_BACKENDS
                ).decode(),
                "ZULIP_RUST_SESSION_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
                "ZULIP_RUST_CSRF_COOKIE_NAME": settings.CSRF_COOKIE_NAME,
            }
            channel = broker.channel()
            for queue in ("notify_tornado", "user_activity"):
                channel.queue_declare(queue, durable=True)
                channel.queue_purge(queue)
            try:
                for scheme in ("http", "https"):
                    with self.subTest(scheme=scheme):
                        kwargs = {"ZULIP_RUST_WEB_POST_SCHEME": scheme}
                        service = (
                            rust_http_service({**environment, **kwargs})
                            if os.environ.get("ZULIP_RUST_HTTP_BIN")
                            else nullcontext(None)
                        )
                        with service as rust_port:
                            self.check_web_post_cases(
                                scheme, django_port, rust_port, user, session, channel
                            )
            finally:
                for queue in ("notify_tornado", "user_activity"):
                    channel.queue_purge(queue)
                Session.objects.filter(session_key=store.session_key).delete()
                RealmAuditLog.objects.filter(
                    acting_user=user, event_type=AuditLogEventType.SAVED_SNIPPET_CREATED
                ).delete()
                SavedSnippet.objects.filter(user_profile=user).delete()

    def check_web_post_cases(
        self,
        scheme: str,
        django_port: int,
        rust_port: int | None,
        user: UserProfile,
        session: str,
        channel: pika.adapters.blocking_connection.BlockingChannel,
    ) -> None:
        host = user.realm.host
        token = _get_new_csrf_string()
        browser_request = HttpRequest()
        browser_request.META["CSRF_COOKIE"] = token
        masked = get_token(browser_request)
        wrong_token = _get_new_csrf_string()
        cookie = f"{session}; {settings.CSRF_COOKIE_NAME}={token}"
        valid = {"Cookie": cookie, "X-CSRFToken": token}
        origin = {"Origin": f"{scheme}://{host}"}
        csrf_headers = {**valid, **origin}
        fields = {"title": "  سلام ☃  ", "content": " **hello**\nworld "}
        credentials = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()

        def post(
            port: int, headers: dict[str, str], form: dict[str, str], query: str = ""
        ) -> tuple[int, bytes, dict[str, str | None], str | None]:
            return request_test_http(
                port,
                "POST",
                host,
                {
                    **headers,
                    "X-Forwarded-Proto": scheme if port == django_port else "http",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                "/json/saved_snippets" + query,
                body=urlencode(form),
            )

        cases = [
            (origin, fields, "", 401),
            ({**origin, "Authorization": f"Basic {credentials}"}, fields, "", 401),
            ({**csrf_headers, "Cookie": session}, fields, "", 403),
            ({**origin, "Cookie": cookie}, fields, f"?csrfmiddlewaretoken={token}", 403),
            ({**csrf_headers, "X-CSRFToken": wrong_token}, fields, "", 403),
            ({**csrf_headers, "X-CSRFToken": "a" * 31 + "!"}, fields, "", 403),
            ({**csrf_headers, "X-CSRFToken": "short"}, fields, "", 403),
            ({**csrf_headers, "Origin": f"{scheme}://other.testserver"}, fields, "", 403),
            ({**csrf_headers, "Origin": "null"}, fields, "", 403),
            ({**csrf_headers, "Origin": f"{scheme}://{host}/"}, fields, "", 403),
            ({**origin, "Cookie": cookie}, {"method": "GET"}, "", 403),
            (csrf_headers, {**fields, "csrfmiddlewaretoken": wrong_token}, "", 403),
            (csrf_headers, {"title": "x"}, "", 400),
            (csrf_headers, {"method": "GET"}, "", 200),
        ]
        if scheme == "https":
            cases.extend(
                [
                    (valid, fields, "", 403),
                    ({**valid, "Referer": f"http://{host}/"}, fields, "", 403),
                    ({**valid, "Referer": "https://other.testserver/"}, fields, "", 403),
                    ({**valid, "Referer": "/relative"}, fields, "", 403),
                ]
            )
        for headers, form, query, expected in cases:
            django = post(django_port, headers, form, query)
            self.assertEqual(django[0], expected, django[1])
            if rust_port is not None:
                self.assertEqual(post(rust_port, headers, form, query)[:3], django[:3])
        self.assertFalse(SavedSnippet.objects.filter(user_profile=user).exists())
        self.assertFalse(
            RealmAuditLog.objects.filter(
                acting_user=user, event_type=AuditLogEventType.SAVED_SNIPPET_CREATED
            ).exists()
        )
        self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[0])

        success_cases = [
            (
                valid if scheme == "http" else {**valid, "Referer": f"https://{host}/#list"},
                {**fields, "csrfmiddlewaretoken": ""},
            ),
            (
                {
                    **csrf_headers,
                    "Cookie": f"{session}; {settings.CSRF_COOKIE_NAME}={masked}",
                    "X-CSRFToken": wrong_token,
                },
                {**fields, "csrfmiddlewaretoken": masked},
            ),
        ]
        for headers, form in success_cases:
            results = []
            for port in (django_port, rust_port):
                if port is None:
                    continue
                status, body, response_headers, _ = post(port, headers, form)
                self.assertEqual(status, 200, body)
                result = orjson.loads(body)
                snippet, audit = self.check_created_snippet(
                    user, channel, result.pop("saved_snippet_id"), "سلام ☃", "**hello**\nworld"
                )
                audit.delete()
                snippet.delete()
                response_headers.pop("Content-Length")
                results.append((result, response_headers))
            if rust_port is not None:
                self.assertEqual(results[0], results[1])

    def post_snippet(
        self,
        port: int,
        user: UserProfile,
        fields: str | bytes,
        *,
        query: str = "",
        content_type: str = "application/x-www-form-urlencoded",
        authenticated: bool = True,
        language: str = "en",
    ) -> tuple[int, bytes, dict[str, str | None], str | None]:
        headers = {"Content-Type": content_type, "Accept-Language": language}
        if authenticated:
            credentials = f"{user.delivery_email}:{user.api_key}".encode()
            headers["Authorization"] = "Basic " + b64encode(credentials).decode()
        return request_test_http(
            port, "POST", user.realm.host, headers, "/api/v1/saved_snippets" + query, body=fields
        )

    def check_create_cases(
        self,
        port: int,
        user: UserProfile,
        channel: pika.adapters.blocking_connection.BlockingChannel,
    ) -> list[tuple[int, bytes, dict[str, str | None]]]:
        responses = []
        baseline_count = SavedSnippet.objects.filter(user_profile=user).count()
        for fields, auth, language, status in (
            ("title=x&content=y", False, "en", 401),
            ("title=x", True, "en", 400),
            ("content=x", True, "en", 400),
            ("title=+&content=x", True, "fr", 400),
            ("title=x&content=+", True, "en", 400),
            (urlencode({"title": "☃" * 61, "content": "x"}), True, "en", 400),
            (urlencode({"title": "x", "content": "x" * 10001}), True, "en", 400),
            ("method=DELETE", True, "en", 405),
            ("method=GET", True, "en", 200),
        ):
            result = self.post_snippet(port, user, fields, authenticated=auth, language=language)
            self.assertEqual(result[0], status)
            if fields == "method=GET":
                self.assert_length(orjson.loads(result[1])["saved_snippets"], baseline_count)
            else:
                responses.append(result[:3])
        self.assertEqual(SavedSnippet.objects.filter(user_profile=user).count(), baseline_count)
        self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[0])

        for body, mime, expected_query, title, content, ignored in (
            (
                urlencode(
                    [
                        ("title", "wrong"),
                        ("title", "  سلام ☃  "),
                        ("extra", "z"),
                        ("client", "ZulipMobile"),
                    ]
                ),
                "application/x-www-form-urlencoded",
                "?title=ignored&content=%2A%2Ax%2A%2A%0Az&another=1",
                "سلام ☃",
                "**x**\nz",
                ["another", "extra"],
            ),
            (
                (
                    b'--boundary\r\nContent-Disposition: form-data; name="title"\r\n\r\nMultipart\r\n'
                    b'--boundary\r\nContent-Disposition: form-data; name="content"\r\n\r\nSaved\r\n--boundary--\r\n'
                ),
                "multipart/form-data; boundary=boundary",
                "",
                "Multipart",
                "Saved",
                [],
            ),
        ):
            status, response, headers, _ = self.post_snippet(
                port, user, body, query=expected_query, content_type=mime
            )
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Length"], str(len(response)))
            result = orjson.loads(response)
            snippet_id = result.pop("saved_snippet_id")
            expected: dict[str, str | list[str]] = {"result": "success", "msg": ""}
            if ignored:
                expected["ignored_parameters_unsupported"] = ignored
            self.assertEqual(result, expected)
            headers.pop("Content-Length")
            responses.append((status, orjson.dumps(result), headers))
            self.check_created_snippet(user, channel, snippet_id, title, content)
        return responses

    def check_created_snippet(
        self,
        user: UserProfile,
        channel: pika.adapters.blocking_connection.BlockingChannel,
        snippet_id: int,
        title: str,
        content: str,
    ) -> tuple[SavedSnippet, RealmAuditLog]:
        saved = SavedSnippet.objects.get(id=snippet_id)
        self.assertEqual(
            (saved.title, saved.content, saved.user_profile_id, saved.realm_id),
            (title, content, user.id, user.realm_id),
        )
        audit = RealmAuditLog.objects.get(
            event_type=AuditLogEventType.SAVED_SNIPPET_CREATED,
            extra_data={"saved_snippet_id": snippet_id},
        )
        self.assertEqual(
            (audit.realm_id, audit.acting_user_id, audit.modified_user_id, audit.backfilled),
            (user.realm_id, user.id, user.id, False),
        )
        self.assertGreaterEqual(audit.event_time, saved.date_created)
        # Django publishes without waiting for broker confirmation.
        deadline = monotonic() + 2
        while True:
            delivery, properties, event_body = channel.basic_get("notify_tornado", auto_ack=True)
            if delivery is not None:
                break
            if monotonic() >= deadline:
                self.fail("RabbitMQ did not deliver the created snippet event")
            sleep(0.01)
        assert properties is not None and event_body is not None
        self.assertEqual(properties.delivery_mode, 2)
        self.assertEqual(
            orjson.loads(event_body),
            {
                "users": [user.id],
                "event": {
                    "type": "saved_snippets",
                    "op": "add",
                    "saved_snippet": saved.to_api_dict(),
                },
            },
        )
        self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[0])
        return saved, audit

    def check_edit_cases(
        self,
        port: int,
        user: UserProfile,
        channel: pika.adapters.blocking_connection.BlockingChannel,
    ) -> list[tuple[int, bytes, dict[str, str | None]]]:
        other = self.example_user("othello")
        created = datetime(2024, 2, 29, 12, 0, 0, 987654, tzinfo=UTC)
        own, foreign = SavedSnippet.objects.bulk_create(
            [
                SavedSnippet(
                    user_profile=user,
                    realm=user.realm,
                    title="Original",
                    content="**Old**",
                    date_created=created,
                ),
                SavedSnippet(
                    user_profile=other,
                    realm=other.realm,
                    title="Private",
                    content="Other user's text",
                    date_created=created,
                ),
            ]
        )
        responses = []
        audit_count = RealmAuditLog.objects.count()

        def patch(
            snippet_id: int,
            body: str | bytes,
            status: int,
            *,
            authenticated: bool = True,
            query: str = "",
            content_type: str = "application/x-www-form-urlencoded",
        ) -> dict[str, object]:
            headers = {"Content-Type": content_type}
            if authenticated:
                credentials = f"{user.delivery_email}:{user.api_key}".encode()
                headers["Authorization"] = "Basic " + b64encode(credentials).decode()
            response = request_test_http(
                port,
                "PATCH",
                user.realm.host,
                headers,
                f"/api/v1/saved_snippets/{snippet_id}{query}",
                body=body,
            )
            self.assertEqual(response[0], status)
            responses.append(response[:3])
            return orjson.loads(response[1])

        try:
            patch(own.id, "title=unauthorized", 401, authenticated=False)
            patch(own.id, "title=+", 400)
            patch(own.id, "content=" + "x" * 10001, 400)
            missing = patch(0, "title=missing", 404)
            self.assertEqual(missing, patch(foreign.id, "title=private", 404))
            self.assertEqual(missing, patch(2**63, "title=out+of+range", 404))
            self.assertEqual(patch(own.id, "", 200), {"result": "success", "msg": ""})
            self.assertEqual(patch(0, "", 200), {"result": "success", "msg": ""})
            self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[0])
            own.refresh_from_db()
            self.assertEqual((own.title, own.content), ("Original", "**Old**"))

            for body, query, mime, title, content, ignored in (
                (
                    urlencode(
                        {
                            "title": "  سلام ☃  ",
                            "unknown": "1",
                            "client": "other",
                            "method": "DELETE",
                        }
                    ),
                    "?title=discard",
                    "application/x-www-form-urlencoded",
                    "سلام ☃",
                    "**Old**",
                    ["client", "method", "unknown"],
                ),
                (
                    "",
                    "?content=%2A%2Anew%2A%2A",
                    "application/x-www-form-urlencoded",
                    "سلام ☃",
                    "**new**",
                    [],
                ),
                (
                    (
                        b'--boundary\r\nContent-Disposition: form-data; name="title"\r\n\r\nMultipart\r\n'
                        b'--boundary\r\nContent-Disposition: form-data; name="content"\r\n\r\nBoth\r\n--boundary--\r\n'
                    ),
                    "",
                    "multipart/form-data; boundary=boundary",
                    "Multipart",
                    "Both",
                    [],
                ),
                (
                    "title=Multipart",
                    "",
                    "application/x-www-form-urlencoded",
                    "Multipart",
                    "Both",
                    [],
                ),
            ):
                result = patch(own.id, body, 200, query=query, content_type=mime)
                self.assertEqual(result.get("ignored_parameters_unsupported", []), ignored)
                own.refresh_from_db()
                self.assertEqual(
                    (own.title, own.content, own.date_created), (title, content, created)
                )
                self.assertEqual((own.user_profile_id, own.realm_id), (user.id, user.realm_id))
                delivery, properties, event_body = channel.basic_get(
                    "notify_tornado", auto_ack=True
                )
                assert delivery is not None and properties is not None and event_body is not None
                self.assertEqual(properties.delivery_mode, 2)
                self.assertEqual(
                    orjson.loads(event_body),
                    {
                        "users": [user.id],
                        "event": {
                            "type": "saved_snippets",
                            "op": "update",
                            "saved_snippet": own.to_api_dict(),
                        },
                    },
                )
                self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[0])

            foreign.refresh_from_db()
            self.assertEqual((foreign.title, foreign.content), ("Private", "Other user's text"))
            self.assertEqual(RealmAuditLog.objects.count(), audit_count)
        finally:
            SavedSnippet.objects.filter(id__in=[own.id, foreign.id]).delete()
        return responses

    def check_delete_cases(
        self,
        port: int,
        user: UserProfile,
        channel: pika.adapters.blocking_connection.BlockingChannel,
        *,
        check_race: bool = False,
    ) -> list[tuple[int, bytes, dict[str, str | None]]]:
        other = self.example_user("othello")
        own, spare, foreign = SavedSnippet.objects.bulk_create(
            [
                SavedSnippet(
                    user_profile=user, realm=user.realm, title="Delete me", content="**Text**"
                ),
                SavedSnippet(user_profile=user, realm=user.realm, title="Keep me", content="Keep"),
                SavedSnippet(
                    user_profile=other,
                    realm=other.realm,
                    title="Private",
                    content="Other user's text",
                ),
            ]
        )
        audit = RealmAuditLog.objects.create(
            realm=user.realm,
            acting_user=user,
            modified_user=user,
            event_time=own.date_created,
            event_type=AuditLogEventType.SAVED_SNIPPET_CREATED,
            extra_data={"saved_snippet_id": own.id},
        )
        audit_count = RealmAuditLog.objects.count()
        responses = []

        def request_delete(
            snippet_id: int,
            *,
            authenticated: bool = True,
            extra: bool = False,
        ) -> tuple[int, bytes, dict[str, str | None], str | None]:
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            if authenticated:
                credentials = f"{user.delivery_email}:{user.api_key}".encode()
                headers["Authorization"] = "Basic " + b64encode(credentials).decode()
            return request_test_http(
                port,
                "DELETE",
                user.realm.host,
                headers,
                f"/api/v1/saved_snippets/{snippet_id}" + ("?unknown=1" if extra else ""),
                body="title=ignored&method=PATCH" if extra else None,
            )

        def verify_removed(snippet_id: int) -> None:
            self.assertFalse(SavedSnippet.objects.filter(id=snippet_id).exists())
            delivery, properties, event_body = channel.basic_get("notify_tornado", auto_ack=True)
            assert delivery is not None and properties is not None and event_body is not None
            self.assertEqual(properties.delivery_mode, 2)
            self.assertEqual(
                orjson.loads(event_body),
                {
                    "users": [user.id],
                    "event": {
                        "type": "saved_snippets",
                        "op": "remove",
                        "saved_snippet_id": snippet_id,
                    },
                },
            )
            self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[0])

        try:
            for snippet_id, has_auth, status in (
                (own.id, False, 401),
                (0, True, 404),
                (foreign.id, True, 404),
                (2**63, True, 404),
            ):
                response = request_delete(snippet_id, authenticated=has_auth)
                self.assertEqual(response[0], status)
                responses.append(response[:3])
            self.assertTrue(SavedSnippet.objects.filter(id=own.id).exists())
            self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[0])

            response = request_delete(own.id, extra=True)
            self.assertEqual(response[0], 200)
            self.assertEqual(orjson.loads(response[1]), {"result": "success", "msg": ""})
            responses.append(response[:3])
            verify_removed(own.id)

            response = request_delete(own.id)
            self.assertEqual(response[0], 404)
            responses.append(response[:3])
            self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[0])
            self.assertTrue(SavedSnippet.objects.filter(id=spare.id).exists())

            if check_race:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(request_delete, (spare.id, spare.id)))
                self.assertCountEqual([result[0] for result in results], [200, 404])
                verify_removed(spare.id)

            self.assertTrue(SavedSnippet.objects.filter(id=foreign.id).exists())
            self.assertTrue(RealmAuditLog.objects.filter(id=audit.id).exists())
            self.assertEqual(RealmAuditLog.objects.count(), audit_count)
        finally:
            audit.delete()
            SavedSnippet.objects.filter(id__in=[own.id, spare.id, foreign.id]).delete()
        return responses

    def check_failed_insert(
        self,
        port: int,
        user: UserProfile,
        channel: pika.adapters.blocking_connection.BlockingChannel,
    ) -> None:
        before = SavedSnippet.objects.filter(user_profile=user).count()
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE zulip.zerver_realmauditlog ADD CONSTRAINT test_snippet_audit_failure "
                "CHECK (event_type <> 800) NOT VALID"
            )
        try:
            status, _, _, _ = self.post_snippet(port, user, "title=Will+roll+back&content=x")
            self.assertEqual(status, 503)
            self.assertEqual(SavedSnippet.objects.filter(user_profile=user).count(), before)
            self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[0])
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE zulip.zerver_realmauditlog DROP CONSTRAINT test_snippet_audit_failure"
                )

    def check_unroutable_event(
        self,
        port: int,
        user: UserProfile,
        channel: pika.adapters.blocking_connection.BlockingChannel,
    ) -> None:
        channel.queue_delete("notify_tornado")
        try:
            status, body, _, _ = self.post_snippet(port, user, "title=Committed&content=Stored")
            self.assertEqual(status, 503)
            self.assertEqual(orjson.loads(body)["msg"], "Notification service unavailable")
            saved = SavedSnippet.objects.get(user_profile=user, title="Committed")
            self.assertTrue(
                RealmAuditLog.objects.filter(
                    event_type=AuditLogEventType.SAVED_SNIPPET_CREATED,
                    extra_data={"saved_snippet_id": saved.id},
                ).exists()
            )
        finally:
            channel.queue_declare("notify_tornado", durable=True)

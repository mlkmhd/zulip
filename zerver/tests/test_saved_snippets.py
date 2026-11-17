import os
from base64 import b64encode
from contextlib import nullcontext
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from unittest import skipUnless
from urllib.parse import quote, urlencode, urlsplit

import orjson
import pika
from django.conf import settings
from django.db import connection
from django.test import LiveServerTestCase

from zerver.actions.saved_snippets import do_create_saved_snippet
from zerver.lib.test_classes import ZulipTestCase, ZulipTransactionTestCase
from zerver.lib.test_rust_http import request_test_http, rust_http_service
from zerver.models import RealmAuditLog, SavedSnippet, UserProfile
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
    def test_create_saved_snippet_http_and_event_parity(self) -> None:
        vhost = os.environ["ZULIP_RUST_ACTIVITY_TEST_VHOST"]
        assert vhost.startswith("zulip-rust-test-")
        username, password = settings.RABBITMQ_USERNAME, settings.RABBITMQ_PASSWORD
        assert password is not None
        amqp_url = (
            f"amqp://{quote(username, safe='')}:{quote(password, safe='')}"
            f"@{settings.RABBITMQ_HOST}:{settings.RABBITMQ_PORT}/{quote(vhost, safe='')}"
        )
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
                if rust_port is not None:
                    rust = self.check_create_cases(rust_port, user, channel)
                    self.assertEqual(rust, django)
                    self.check_failed_insert(rust_port, user, channel)
                    self.check_unroutable_event(rust_port, user, channel)
            finally:
                channel.queue_purge("notify_tornado")
                channel.queue_purge("user_activity")
                audits.exclude(id__in=initial_audit_ids).delete()
                snippets.exclude(id__in=initial_snippet_ids).delete()

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
            delivery, properties, event_body = channel.basic_get("notify_tornado", auto_ack=True)
            assert delivery is not None and properties is not None and event_body is not None
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

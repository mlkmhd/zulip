import os
from base64 import b64encode
from contextlib import nullcontext
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import orjson
from django.test import LiveServerTestCase

from zerver.actions.saved_snippets import do_create_saved_snippet
from zerver.lib.test_classes import ZulipTestCase, ZulipTransactionTestCase
from zerver.lib.test_rust_http import request_test_http, rust_http_service
from zerver.models import SavedSnippet, UserProfile


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

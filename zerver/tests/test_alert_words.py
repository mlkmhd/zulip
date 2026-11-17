import os
import re
import select
import subprocess
from base64 import b64encode
from email.utils import parsedate_to_datetime
from http.client import HTTPConnection
from unittest import skipUnless
from urllib.parse import urlsplit

import orjson
from django.conf import settings
from django.db import connection
from django.test import LiveServerTestCase
from psycopg2.extensions import make_dsn

from zerver.actions.alert_words import do_add_alert_words, do_remove_alert_words
from zerver.lib.alert_words import alert_words_in_realm, user_alert_words
from zerver.lib.test_classes import ZulipTestCase, ZulipTransactionTestCase
from zerver.lib.test_helpers import most_recent_message, most_recent_usermessage
from zerver.models import AlertWord, Realm, UserProfile


class AlertWordTests(ZulipTestCase):
    interesting_alert_word_list = ["alert", "multi-word word", "☃"]

    def get_user(self) -> UserProfile:
        # One nice thing about Hamlet is that he is
        # already subscribed to Denmark.
        user = self.example_user("hamlet")

        # delete words from populate_db to simplify tests
        AlertWord.objects.filter(user_profile=user).delete()
        return user

    def test_internal_endpoint(self) -> None:
        user = self.get_user()
        self.login_user(user)

        params = {
            "alert_words": orjson.dumps(["milk", "cookies"]).decode(),
        }
        result = self.client_post("/json/users/me/alert_words", params)
        self.assert_json_success(result)

        words = user_alert_words(user)
        self.assertEqual(set(words), {"milk", "cookies"})

    def test_default_no_words(self) -> None:
        """
        Users start out with no alert words.
        """
        user = self.get_user()
        words = user_alert_words(user)
        self.assertEqual(words, [])

    def test_basics(self) -> None:
        """
        Verifies the basic behavior of modifying alert words.

        Also verifies the cache-flushing behavior.
        """
        user = self.get_user()
        realm_alert_words = alert_words_in_realm(user.realm)
        self.assert_length(realm_alert_words.get(user.id, []), 0)

        # Add several words, including multi-word and non-ascii words.
        do_add_alert_words(user, self.interesting_alert_word_list)

        words = user_alert_words(user)
        self.assertEqual(set(words), set(self.interesting_alert_word_list))
        realm_alert_words = alert_words_in_realm(user.realm)
        self.assert_length(realm_alert_words[user.id], 3)

        # Test the case-insensitivity of adding words
        do_add_alert_words(user, {"ALert", "ALERT"})
        words = user_alert_words(user)
        self.assertEqual(set(words), set(self.interesting_alert_word_list))
        realm_alert_words = alert_words_in_realm(user.realm)
        self.assert_length(realm_alert_words[user.id], 3)

        # Test the case-insensitivity of removing words
        do_remove_alert_words(user, {"ALert"})
        words = user_alert_words(user)
        self.assertEqual(set(words), set(self.interesting_alert_word_list) - {"alert"})
        realm_alert_words = alert_words_in_realm(user.realm)
        self.assert_length(realm_alert_words[user.id], 2)

    def test_remove_word(self) -> None:
        """
        Removing alert words works via do_remove_alert_words, even
        for multi-word and non-ascii words.
        """
        user = self.get_user()
        expected_remaining_alerts = set(self.interesting_alert_word_list)
        do_add_alert_words(user, self.interesting_alert_word_list)
        for alert_word in self.interesting_alert_word_list:
            do_remove_alert_words(user, [alert_word])
            expected_remaining_alerts.remove(alert_word)
            actual_remaining_alerts = user_alert_words(user)
            self.assertEqual(set(actual_remaining_alerts), expected_remaining_alerts)

    def test_remove_words_bulk_case_insensitive(self) -> None:
        """
        Removing multiple alert words in one call works correctly,
        including case-insensitive matching.
        """
        user = self.get_user()
        do_add_alert_words(user, ["hello", "world", "python"])
        # Delete using different casing — should still match
        do_remove_alert_words(user, ["HELLO", "World"])
        remaining = user_alert_words(user)
        self.assertEqual(set(remaining), {"python"})

    def test_realm_words(self) -> None:
        """
        We can gather alert words for an entire realm via
        alert_words_in_realm. Alerts added for one user do not impact other
        users.
        """

        # Clear all the words that we got from populate_db.
        AlertWord.objects.all().delete()

        user1 = self.get_user()

        do_add_alert_words(user1, self.interesting_alert_word_list)

        user2 = self.example_user("othello")
        do_add_alert_words(user2, ["another"])

        realm_words = alert_words_in_realm(user2.realm)
        self.assert_length(realm_words, 2)
        self.assertEqual(set(realm_words.keys()), {user1.id, user2.id})
        self.assertEqual(set(realm_words[user1.id]), set(self.interesting_alert_word_list))
        self.assertEqual(set(realm_words[user2.id]), {"another"})

    def test_json_list_default(self) -> None:
        user = self.get_user()
        self.login_user(user)

        result = self.client_get("/json/users/me/alert_words")
        response_dict = self.assert_json_success(result)
        self.assertEqual(response_dict["alert_words"], [])

    def test_json_list_nonempty(self) -> None:
        user = self.get_user()
        do_add_alert_words(user, ["one", "two", "three"])

        self.login_user(user)
        result = self.client_get("/json/users/me/alert_words")
        response_dict = self.assert_json_success(result)
        self.assertEqual(set(response_dict["alert_words"]), {"one", "two", "three"})

    def test_json_list_add(self) -> None:
        user = self.get_user()
        self.login_user(user)

        result = self.client_post(
            "/json/users/me/alert_words",
            {"alert_words": orjson.dumps(["one ", "\n two", "three"]).decode()},
        )
        response_dict = self.assert_json_success(result)
        self.assertEqual(set(response_dict["alert_words"]), {"one", "two", "three"})

        result = self.client_post(
            "/json/users/me/alert_words",
            {"alert_words": orjson.dumps(["long" * 26]).decode()},
        )
        self.assert_json_error(result, "alert_words[0] is too long (limit: 100 characters)")

    def test_json_list_remove(self) -> None:
        user = self.get_user()
        self.login_user(user)

        result = self.client_post(
            "/json/users/me/alert_words",
            {"alert_words": orjson.dumps(["one", "two", "three"]).decode()},
        )
        response_dict = self.assert_json_success(result)
        self.assertEqual(set(response_dict["alert_words"]), {"one", "two", "three"})

        result = self.client_delete(
            "/json/users/me/alert_words", {"alert_words": orjson.dumps(["one"]).decode()}
        )
        response_dict = self.assert_json_success(result)
        self.assertEqual(set(response_dict["alert_words"]), {"two", "three"})

    def test_api_add_normalizes_and_deduplicates_alert_words(self) -> None:
        user = self.get_user()
        do_add_alert_words(user, ["Keep"])

        result = self.api_post(
            user,
            "/api/v1/users/me/alert_words",
            {
                "alert_words": orjson.dumps(
                    ["KEEP", "\u00a0tea\u2003", "\u001c", "cake", "TEA"]
                ).decode()
            },
        )
        response = self.assert_json_success(result)
        self.assertCountEqual(response["alert_words"], ["Keep", "TEA", "cake"])
        self.assertCountEqual(user_alert_words(user), ["Keep", "TEA", "cake"])

    def message_does_alert(self, user: UserProfile, message: str) -> bool:
        """Send a bunch of messages as othello, so our user is notified"""
        self.send_stream_message(self.example_user("othello"), "Denmark", message)
        user_message = most_recent_usermessage(user)
        return "has_alert_word" in user_message.flags_list()

    def test_alert_flags(self) -> None:
        user = self.get_user()
        self.login_user(user)

        result = self.client_post(
            "/json/users/me/alert_words",
            {"alert_words": orjson.dumps(["one", "two", "three"]).decode()},
        )
        response_dict = self.assert_json_success(result)
        self.assertEqual(set(response_dict["alert_words"]), {"one", "two", "three"})

        # Alerts in the middle of messages work.
        self.assertTrue(self.message_does_alert(user, "Normal alert one time"))
        # Alerts at the end of messages work.
        self.assertTrue(self.message_does_alert(user, "Normal alert one"))
        # Alerts at the beginning of messages work.
        self.assertTrue(self.message_does_alert(user, "two normal alerts"))
        # Alerts with surrounding punctuation work.
        self.assertTrue(self.message_does_alert(user, "This one? should alert"))
        self.assertTrue(self.message_does_alert(user, "Definitely time for three."))
        # Multiple alerts in a message work.
        self.assertTrue(self.message_does_alert(user, "One two three o'clock"))
        # Alerts are case-insensitive.
        self.assertTrue(self.message_does_alert(user, "One o'clock"))
        self.assertTrue(self.message_does_alert(user, "Case of ONE, won't stop me"))

        # We don't cause alerts for matches in URLs.
        self.assertFalse(self.message_does_alert(user, "Don't alert on http://t.co/one/ URLs"))
        self.assertFalse(self.message_does_alert(user, "Don't alert on http://t.co/one URLs"))

        # We don't cause alerts for matches within a word.
        self.assertFalse(
            self.message_does_alert(user, "Don't alert on clone, twofold or seventytwofold")
        )

    def test_update_alert_words(self) -> None:
        user = self.get_user()
        self.login_user(user)

        result = self.client_post(
            "/json/users/me/alert_words", {"alert_words": orjson.dumps(["ALERT"]).decode()}
        )

        content = "this is an ALERT for you"
        self.send_stream_message(user, "Denmark", content)
        self.assert_json_success(result)

        original_message = most_recent_message(user)

        user_message = most_recent_usermessage(user)
        self.assertIn("has_alert_word", user_message.flags_list())

        result = self.client_patch(
            "/json/messages/" + str(original_message.id),
            {
                "content": "new ALERT for you",
            },
        )
        self.assert_json_success(result)

        user_message = most_recent_usermessage(user)
        self.assertEqual(user_message.message.content, "new ALERT for you")
        self.assertIn("has_alert_word", user_message.flags_list())

        result = self.client_patch(
            "/json/messages/" + str(original_message.id),
            {
                "content": "sorry false alarm",
            },
        )
        self.assert_json_success(result)

        user_message = most_recent_usermessage(user)
        self.assertEqual(user_message.message.content, "sorry false alarm")
        self.assertNotIn("has_alert_word", user_message.flags_list())


@skipUnless(os.environ.get("ZULIP_RUST_HTTP_BIN"), "Rust HTTP service binary is unavailable")
class AlertWordsRustHTTPTest(ZulipTransactionTestCase, LiveServerTestCase):
    def test_alert_words_http_parity(self) -> None:
        user = self.example_user("hamlet")
        inactive = self.example_user("iago")
        other_realm = UserProfile.objects.filter(realm__string_id="zephyr", is_bot=False).first()
        deactivated = UserProfile.objects.filter(realm__string_id="lear", is_bot=False).first()
        webhook = UserProfile.objects.filter(bot_type=UserProfile.INCOMING_WEBHOOK_BOT).first()
        assert other_realm is not None and deactivated is not None and webhook is not None
        self.assertFalse(user_alert_words(other_realm))

        # The independent Rust connection can only see committed changes.
        word = AlertWord.objects.create(user_profile=user, realm=user.realm, word="هشدار ☃")
        realm = deactivated.realm
        try:
            UserProfile.objects.filter(id=inactive.id).update(is_active=False)
            Realm.objects.filter(id=realm.id).update(deactivated=True)
            self.compare_responses(user, other_realm, inactive, deactivated, webhook)
        finally:
            word.delete()
            UserProfile.objects.filter(id=inactive.id).update(is_active=inactive.is_active)
            Realm.objects.filter(id=realm.id).update(deactivated=realm.deactivated)

    def compare_responses(
        self,
        user: UserProfile,
        other_realm: UserProfile,
        inactive: UserProfile,
        deactivated: UserProfile,
        webhook: UserProfile,
    ) -> None:
        db = connection.settings_dict
        database = make_dsn(
            dbname=db["NAME"],
            host=db["HOST"],
            user=db["USER"],
            password=db["PASSWORD"],
            port=db["PORT"] or None,
        )
        external_host = urlsplit("//" + settings.EXTERNAL_HOST).hostname
        assert external_host is not None
        with subprocess.Popen(
            [os.environ["ZULIP_RUST_HTTP_BIN"], "127.0.0.1:0"],
            stderr=subprocess.PIPE,
            text=True,
            env={
                **os.environ,
                "ZULIP_RUST_DATABASE_URL": database,
                "ZULIP_RUST_EXTERNAL_HOST": external_host,
            },
        ) as rust:
            assert rust.stderr is not None
            try:
                ready, _, _ = select.select([rust.stderr], [], [], 10)
                self.assertTrue(ready, "Rust HTTP service did not start")
                address = re.fullmatch(
                    r"Zulip HTTP listening on 127\.0\.0\.1:(\d+)\n", rust.stderr.readline()
                )
                assert address is not None, "Rust HTTP service failed to bind"
                rust_port = int(address[1])
                django_port = urlsplit(self.live_server_url).port
                assert django_port is not None
                self.compare_cases(
                    django_port, rust_port, user, other_realm, inactive, deactivated, webhook
                )
            finally:
                rust.terminate()
                try:
                    rust.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    rust.kill()

    def compare_cases(
        self,
        django_port: int,
        rust_port: int,
        user: UserProfile,
        other_realm: UserProfile,
        inactive: UserProfile,
        deactivated: UserProfile,
        webhook: UserProfile,
    ) -> None:
        def auth(email: str, key: str) -> str:
            return "Basic " + b64encode(f"{email}:{key}".encode()).decode()

        valid = auth(user.delivery_email, user.api_key)
        cases = [
            ("stored words", "GET", 200, user.realm.host, {"Authorization": valid}),
            ("HEAD", "HEAD", 200, user.realm.host, {"Authorization": valid}),
            (
                "empty",
                "GET",
                200,
                other_realm.realm.host,
                {"Authorization": auth(other_realm.delivery_email, other_realm.api_key)},
            ),
            ("missing", "GET", 401, user.realm.host, {}),
            ("missing HEAD", "HEAD", 401, user.realm.host, {}),
            ("localized", "GET", 401, user.realm.host, {"Accept-Language": "fr"}),
            (
                "locale cookie",
                "GET",
                401,
                user.realm.host,
                {"Accept-Language": "de", "Cookie": "django_language=fr"},
            ),
            ("web redirect", "GET", 302, user.realm.host, {"Accept": "text/html, */*;q=0.5"}),
            (
                "JSON preference",
                "GET",
                401,
                user.realm.host,
                {"Accept": "application/json, text/html;q=0.9"},
            ),
            ("malformed header", "GET", 401, user.realm.host, {"Authorization": "Basic invalid!"}),
            (
                "unsupported scheme",
                "GET",
                400,
                user.realm.host,
                {"Authorization": "Bearer example"},
            ),
            (
                "malformed key",
                "GET",
                401,
                user.realm.host,
                {"Authorization": auth(user.delivery_email, "short")},
            ),
            (
                "unknown key",
                "GET",
                401,
                user.realm.host,
                {"Authorization": auth(user.delivery_email, "0" * 32)},
            ),
            (
                "mismatched email",
                "GET",
                401,
                user.realm.host,
                {"Authorization": auth(other_realm.delivery_email, user.api_key)},
            ),
            (
                "whitespace and case",
                "GET",
                200,
                user.realm.host.upper(),
                {"Authorization": auth(f" {user.delivery_email.upper()} ", f" {user.api_key} ")},
            ),
            ("wrong organization", "GET", 401, other_realm.realm.host, {"Authorization": valid}),
            (
                "forwarded host",
                "GET",
                401,
                other_realm.realm.host,
                {"Authorization": valid, "X-Forwarded-Host": user.realm.host},
            ),
            (
                "inactive user",
                "GET",
                401,
                inactive.realm.host,
                {"Authorization": auth(inactive.delivery_email, inactive.api_key)},
            ),
            (
                "deactivated organization",
                "GET",
                401,
                deactivated.realm.host,
                {"Authorization": auth(deactivated.delivery_email, deactivated.api_key)},
            ),
            (
                "incoming webhook",
                "GET",
                401,
                webhook.realm.host,
                {"Authorization": auth(webhook.delivery_email, webhook.api_key)},
            ),
        ]
        for case, method, status, host, headers in cases:
            with self.subTest(case=case):
                django = self.request_words(django_port, method, host, headers)
                rust = self.request_words(rust_port, method, host, headers)
                self.assertEqual(django[0], status)
                self.assertEqual(rust[:3], django[:3])
                if django[3] is None:
                    self.assertIsNone(rust[3])
                else:
                    assert rust[3] is not None
                    expires_difference = parsedate_to_datetime(rust[3]) - parsedate_to_datetime(
                        django[3]
                    )
                    self.assertLessEqual(abs(expires_difference.total_seconds()), 5)
                if case == "stored words":
                    self.assertIn("هشدار ☃", orjson.loads(rust[1])["alert_words"])
                if case == "empty":
                    self.assertEqual(orjson.loads(rust[1])["alert_words"], [])

    def request_words(
        self, port: int, method: str, host: str, headers: dict[str, str]
    ) -> tuple[int, bytes, dict[str, str | None], str | None]:
        client = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            client.request(
                method,
                "/api/v1/users/me/alert_words",
                headers={
                    "Host": host,
                    "User-Agent": "ZulipMobile/1.0",
                    "X-Real-IP": "192.0.2.1",
                    **headers,
                },
            )
            response = client.getresponse()
            compared_headers = (
                "Content-Type",
                "Content-Length",
                "Content-Language",
                "Vary",
                "Cache-Control",
                "WWW-Authenticate",
                "Location",
                "X-RateLimit-Limit",
                "X-RateLimit-Remaining",
                "X-RateLimit-Reset",
            )
            return (
                response.status,
                response.read(),
                {name: response.getheader(name) for name in compared_headers},
                response.getheader("Expires"),
            )
        finally:
            client.close()

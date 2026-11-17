import os
import re
import select
import subprocess
import time
from base64 import b64encode
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from http.client import HTTPConnection
from tempfile import NamedTemporaryFile
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import quote, urlencode, urlsplit

import orjson
import pika
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import connection, transaction
from django.test import LiveServerTestCase
from django.utils.timezone import now as timezone_now
from psycopg2.extensions import make_dsn

from zerver.actions.alert_words import do_add_alert_words, do_remove_alert_words
from zerver.lib import redis_utils
from zerver.lib.alert_words import alert_words_in_realm, user_alert_words
from zerver.lib.rate_limiter import RateLimitedUser, RedisRateLimiterBackend
from zerver.lib.test_classes import ZulipTestCase, ZulipTransactionTestCase
from zerver.lib.test_helpers import most_recent_message, most_recent_usermessage
from zerver.models import AlertWord, Client, Realm, UserActivity, UserProfile
from zerver.worker.user_activity import UserActivityWorker


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
            with self.rust_service({}) as rust_port:
                django_port = urlsplit(self.live_server_url).port
                assert django_port is not None
                self.compare_cases(
                    django_port, rust_port, user, other_realm, inactive, deactivated, webhook
                )
        finally:
            word.delete()
            UserProfile.objects.filter(id=inactive.id).update(is_active=inactive.is_active)
            Realm.objects.filter(id=realm.id).update(deactivated=realm.deactivated)

    def test_alert_words_session_and_csrf_parity(self) -> None:
        user = self.example_user("hamlet")
        inactive = self.example_user("iago")
        other_realm = Realm.objects.get(string_id="zephyr")
        webhook = UserProfile.objects.filter(bot_type=UserProfile.INCOMING_WEBHOOK_BOT).first()
        assert webhook is not None
        backend = "zproject.backends.DevAuthBackend"
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None
        keys: list[str] = []

        def create_session(profile: UserProfile = user, **overrides: str) -> str:
            store = SessionStore()
            store.update(
                {
                    "_auth_user_id": str(profile.id),
                    "_auth_user_backend": backend,
                    "_auth_user_hash": profile.get_session_auth_hash(),
                    **overrides,
                }
            )
            store.create()
            assert store.session_key is not None
            keys.append(store.session_key)
            return store.session_key

        def cookie(key: str) -> dict[str, str]:
            return {"Cookie": f"{settings.SESSION_COOKIE_NAME}={key}"}

        with NamedTemporaryFile(mode="w", encoding="utf-8") as secret:
            secret.write(settings.SECRET_KEY)
            secret.flush()
            try:
                valid = create_session(profile_data="a" * 2048)
                self.assertTrue(Session.objects.get(session_key=valid).session_data.startswith("."))
                regular = create_session()
                inactive_key = create_session(inactive)
                webhook_key = create_session(webhook)
                UserProfile.objects.filter(id=inactive.id).update(is_active=False)
                expired = create_session()
                Session.objects.filter(session_key=expired).update(expire_date=timezone_now())
                wrong_hash = create_session(_auth_user_hash="0" * 64)
                wrong_backend = create_session(_auth_user_backend="not.configured.Backend")
                corrupted = create_session()
                session = Session.objects.get(session_key=corrupted)
                session.session_data += "x"
                session.save(update_fields=["session_data"])
                deleted = create_session()
                Session.objects.filter(session_key=deleted).delete()
                authorization = (
                    "Basic " + b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
                )
                with self.rust_service(self.django_session_environment(secret.name)) as rust_port:
                    cases = [
                        ("valid session", "GET", 200, user.realm.host, cookie(valid)),
                        ("regular session", "GET", 200, user.realm.host, cookie(regular)),
                        ("HEAD", "HEAD", 200, user.realm.host, cookie(valid)),
                        ("missing", "GET", 401, user.realm.host, {}),
                        (
                            "valid Basic without session",
                            "GET",
                            401,
                            user.realm.host,
                            {"Authorization": authorization},
                        ),
                        ("missing HTML", "GET", 302, user.realm.host, {"Accept": "text/html"}),
                        ("expired", "GET", 401, user.realm.host, cookie(expired)),
                        ("deleted", "GET", 401, user.realm.host, cookie(deleted)),
                        ("invalid key", "GET", 401, user.realm.host, cookie("short")),
                        ("password hash changed", "GET", 401, user.realm.host, cookie(wrong_hash)),
                        ("backend removed", "GET", 401, user.realm.host, cookie(wrong_backend)),
                        ("corrupted", "GET", 401, user.realm.host, cookie(corrupted)),
                        ("other organization", "GET", 400, other_realm.host, cookie(valid)),
                        ("inactive account", "GET", 401, inactive.realm.host, cookie(inactive_key)),
                        ("incoming bot", "GET", 400, webhook.realm.host, cookie(webhook_key)),
                        (
                            "safe cross-origin",
                            "GET",
                            200,
                            user.realm.host,
                            {
                                **cookie(valid),
                                "Origin": "https://other.testserver",
                                "Referer": "https://other.testserver/path",
                                "X-CSRFToken": "incorrect",
                            },
                        ),
                    ]
                    for label, method, status, host, headers in cases:
                        with self.subTest(case=label):
                            rust = self.request_words(
                                rust_port, method, host, headers, browser=True
                            )
                            django = self.request_words(
                                django_port, method, host, headers, browser=True
                            )
                            self.assertEqual(django[0], status)
                            self.assertEqual(rust[:3], django[:3])
                            if django[3] is not None:
                                assert rust[3] is not None
                                delta = parsedate_to_datetime(rust[3]) - parsedate_to_datetime(
                                    django[3]
                                )
                                self.assertLessEqual(abs(delta.total_seconds()), 5)
                            else:
                                self.assertIsNone(rust[3])
                    self.check_session_csrf(django_port, rust_port, user, cookie(valid))
            finally:
                Session.objects.filter(session_key__in=keys).delete()
                UserProfile.objects.filter(id=inactive.id).update(is_active=inactive.is_active)

    def check_session_csrf(
        self, django_port: int, rust_port: int, user: UserProfile, headers: dict[str, str]
    ) -> None:
        csrf_name = settings.CSRF_COOKIE_NAME
        malformed = {"Cookie": headers["Cookie"] + f"; {csrf_name}=bad"}
        results = [
            self.request_words(port, "GET", user.realm.host, malformed, browser=True)
            for port in (django_port, rust_port)
        ]
        attrs = []
        for status, _, response_headers, _ in results:
            self.assertEqual(status, 200)
            replacement = response_headers["Set-Cookie"]
            assert replacement is not None
            value, *flags = replacement.split("; ")
            self.assertRegex(value, rf"^{re.escape(csrf_name)}=[A-Za-z0-9]{{32}}$")
            attrs.append([flag for flag in flags if not flag.startswith("expires=")])
        self.assertEqual(attrs[0], attrs[1])
        self.assertEqual(results[0][1], results[1][1])
        words = user_alert_words(user)
        for method in ("POST", "DELETE"):
            django = self.request_words(django_port, method, user.realm.host, headers, browser=True)
            rust = self.request_words(rust_port, method, user.realm.host, headers, browser=True)
            self.assertEqual(django[0], 403)
            self.assertEqual(orjson.loads(django[1])["code"], "CSRF_FAILED")
            self.assertEqual(rust[0], 405)
            self.assertCountEqual(user_alert_words(user), words)

    @contextmanager
    def rust_service(self, extra_env: dict[str, str]) -> Iterator[int]:
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
        env = os.environ.copy()
        env.pop("ZULIP_RUST_REDIS_URL", None)
        env.pop("ZULIP_RUST_AMQP_URL", None)
        env.pop("ZULIP_RUST_DJANGO_SECRET_KEY_FILE", None)
        with subprocess.Popen(
            [os.environ["ZULIP_RUST_HTTP_BIN"], "127.0.0.1:0"],
            stderr=subprocess.PIPE,
            text=True,
            env={
                **env,
                "ZULIP_RUST_DATABASE_URL": database,
                "ZULIP_RUST_EXTERNAL_HOST": external_host,
                **extra_env,
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
                yield int(address[1])
            finally:
                rust.terminate()
                try:
                    rust.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    rust.kill()

    def django_session_environment(self, secret_file: str) -> dict[str, str]:
        return {
            "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": secret_file,
            "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                settings.AUTHENTICATION_BACKENDS
            ).decode(),
            "ZULIP_RUST_SESSION_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
            "ZULIP_RUST_CSRF_COOKIE_NAME": settings.CSRF_COOKIE_NAME,
        }

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
        self,
        port: int,
        method: str,
        host: str,
        headers: dict[str, str],
        *,
        query: str = "",
        browser: bool = False,
    ) -> tuple[int, bytes, dict[str, str | None], str | None]:
        client = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            client.request(
                method,
                ("/json" if browser else "/api/v1")
                + "/users/me/alert_words"
                + (f"?{query}" if query else ""),
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
                "Retry-After",
                "Set-Cookie",
            )
            return (
                response.status,
                response.read(),
                {name: response.getheader(name) for name in compared_headers},
                response.getheader("Expires"),
            )
        finally:
            client.close()

    @skipUnless(
        os.environ.get("ZULIP_RUST_ACTIVITY_TEST_VHOST"), "RabbitMQ test vhost is unavailable"
    )
    def test_shared_rate_limit_and_activity(self) -> None:
        vhost = os.environ["ZULIP_RUST_ACTIVITY_TEST_VHOST"]
        self.assertTrue(vhost.startswith("zulip-rust-test-"), "Use a dedicated RabbitMQ test vhost")
        shards = 3
        rules = [(120, 2), (180, 3)]
        user = self.example_user("hamlet")
        custom_user = self.example_user("othello")
        custom_rules = "120:1,240:2"
        custom_name = ("Rust+parity café " * 3)[: Client.MAX_NAME_LENGTH]
        username = settings.RABBITMQ_USERNAME
        password = settings.RABBITMQ_PASSWORD
        assert password is not None
        broker_url = (
            f"amqp://{quote(username, safe='')}:{quote(password, safe='')}"
            f"@{settings.RABBITMQ_HOST}:{settings.RABBITMQ_PORT}/{quote(vhost, safe='')}"
        )
        redis_password = settings.REDIS_PASSWORD
        redisauth = f":{quote(redis_password, safe='')}@" if redis_password else ""
        redis_url = f"redis://{redisauth}{settings.REDIS_HOST}:{settings.REDIS_PORT}/0"
        environment = {
            "ZULIP_RUST_REDIS_URL": redis_url,
            "ZULIP_RUST_REDIS_KEY_PREFIX": redis_utils.REDIS_KEY_PREFIX,
            "ZULIP_RUST_RATE_LIMIT_RULES": orjson.dumps(rules).decode(),
            "ZULIP_RUST_AMQP_URL": broker_url,
            "ZULIP_RUST_USER_ACTIVITY_SHARDS": str(shards),
        }
        params = pika.ConnectionParameters(
            settings.RABBITMQ_HOST,
            port=settings.RABBITMQ_PORT,
            virtual_host=vhost,
            credentials=pika.PlainCredentials(username, password),
        )
        self.assertFalse(Client.objects.filter(name=custom_name).exists())
        with (
            pika.BlockingConnection(params) as broker,
            NamedTemporaryFile(mode="w", encoding="utf-8") as secret,
        ):
            secret.write(settings.SECRET_KEY)
            secret.flush()
            environment.update(self.django_session_environment(secret.name))
            channel = broker.channel()
            for shard in range(1, shards + 1):
                channel.queue_declare(f"user_activity_shard{shard}", durable=True)
                channel.queue_purge(f"user_activity_shard{shard}")
            UserProfile.objects.filter(id=custom_user.id).update(rate_limits=custom_rules)
            try:
                with (
                    self.settings(
                        RATE_LIMITING=True,
                        DEBUG_RATE_LIMITING=False,
                        RATE_LIMITING_RULES={**settings.RATE_LIMITING_RULES, "api_by_user": rules},
                        USING_RABBITMQ=True,
                        RABBITMQ_VHOST=vhost,
                        USER_ACTIVITY_SHARDS=shards,
                    ),
                    patch.object(RedisRateLimiterBackend, "_testing_clock", False),
                    self.rust_service(environment) as rust_port,
                ):
                    self.check_shared_quota(rust_port, user, custom_user)
                    self.check_session_quota(rust_port, user)
                self.check_activity(channel, shards, user, custom_user, custom_name)
            finally:
                RateLimitedUser(user).clear_history()
                RateLimitedUser(custom_user).clear_history()
                Client.objects.filter(name=custom_name).delete()
                UserProfile.objects.filter(id=custom_user.id).update(
                    rate_limits=custom_user.rate_limits
                )

    def check_session_quota(self, rust_port: int, user: UserProfile) -> None:
        RateLimitedUser(user).clear_history()
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None
        store = SessionStore()
        store.update(
            {
                "_auth_user_id": str(user.id),
                "_auth_user_backend": "zproject.backends.DevAuthBackend",
                "_auth_user_hash": user.get_session_auth_hash(),
            }
        )
        store.create()
        try:
            headers = {
                "Cookie": f"{settings.SESSION_COOKIE_NAME}={store.session_key}",
                "User-Agent": "Mozilla/5.0",
            }
            for port, status, remaining in (
                (django_port, 200, "2"),
                (rust_port, 200, "1"),
                (rust_port, 429, "0"),
                (django_port, 429, "0"),
            ):
                result, body, response_headers, _ = self.request_words(
                    port, "GET", user.realm.host, headers, browser=True
                )
                self.assertEqual(result, status)
                self.assertEqual(response_headers["X-RateLimit-Limit"], "3")
                self.assertEqual(response_headers["X-RateLimit-Remaining"], remaining)
                self.assertEqual(response_headers["Vary"], "Accept-Language, Cookie")
                data = orjson.loads(body)
                if status == 200:
                    self.assertCountEqual(data["alert_words"], user_alert_words(user))
                else:
                    self.assertEqual(data["code"], "RATE_LIMIT_HIT")
        finally:
            store.delete()

    def check_shared_quota(
        self, rust_port: int, user: UserProfile, custom_user: UserProfile
    ) -> None:
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None
        for port in (django_port, rust_port):
            status, _, headers, _ = self.request_words(port, "GET", user.realm.host, {})
            self.assertEqual(status, 401)
            self.assertIsNone(headers["X-RateLimit-Limit"])

        def request(
            port: int, profile: UserProfile, method: str = "GET", **query: str
        ) -> tuple[int, bytes, dict[str, str | None], str | None]:
            authorization = (
                "Basic "
                + b64encode(f"{profile.delivery_email}:{profile.api_key}".encode()).decode()
            )
            return self.request_words(
                port,
                method,
                profile.realm.host,
                {"Authorization": authorization},
                query=urlencode(query),
            )

        django = request(django_port, user)
        rust = request(rust_port, user)
        self.assertEqual(django[:2], rust[:2])
        for response, remaining in [(django, "2"), (rust, "1")]:
            self.assertEqual(response[0], 200)
            self.assertEqual(response[2]["X-RateLimit-Limit"], "3")
            self.assertEqual(response[2]["X-RateLimit-Remaining"], remaining)
            self.assertIsNone(response[2]["Retry-After"])
        for name in ("Content-Type", "Content-Length", "Content-Language", "Vary", "Cache-Control"):
            self.assertEqual(django[2][name], rust[2][name])
        django_reset = django[2]["X-RateLimit-Reset"]
        rust_reset = rust[2]["X-RateLimit-Reset"]
        assert django_reset is not None and rust_reset is not None
        self.assertLessEqual(abs(int(rust_reset) - int(django_reset) - 60), 1)

        def assert_limited_pair() -> None:
            errors = []
            for port in (django_port, rust_port):
                status, body, headers, _ = request(port, user)
                self.assertEqual(status, 429)
                self.assertEqual(headers["X-RateLimit-Limit"], "3")
                self.assertEqual(headers["X-RateLimit-Remaining"], "0")
                self.assertIsNone(headers["Cache-Control"])
                data = orjson.loads(body)
                self.assertEqual(data["code"], "RATE_LIMIT_HIT")
                retry = headers.pop("Retry-After")
                reset = headers.pop("X-RateLimit-Reset")
                assert retry is not None and reset is not None
                self.assertEqual(float(retry), data.pop("retry-after"))
                self.assertGreater(float(retry), 0)
                self.assertLessEqual(abs(int(reset) - int(time.time() + float(retry))), 1)
                headers.pop("Content-Length")
                errors.append((data, headers, float(retry), int(reset)))
            self.assertEqual(errors[0][:2], errors[1][:2])
            self.assertLessEqual(abs(errors[0][2] - errors[1][2]), 1)
            self.assertLessEqual(abs(errors[0][3] - errors[1][3]), 1)

        rate_limit = RateLimitedUser(user)
        gcra, _ = RedisRateLimiterBackend.get_keys(rate_limit.key())
        redis = redis_utils.get_redis_client()
        snapshot = redis.hgetall(gcra)
        self.assertEqual(set(snapshot), {b"120:2", b"180:3"})
        assert_limited_pair()
        self.assertEqual(redis.hgetall(gcra), snapshot)

        rate_limit.block_access(120)
        assert_limited_pair()
        self.assertEqual(redis.hgetall(gcra), snapshot)

        client = "Rust+parity café " * 3
        rust = request(rust_port, custom_user, "HEAD", client=client)
        django = request(django_port, custom_user, client=client)
        self.assertEqual(rust[:2], (200, b""))
        self.assertEqual(django[0], 429)
        self.assertEqual(rust[2]["X-RateLimit-Limit"], "2")
        self.assertEqual(django[2]["X-RateLimit-Limit"], "2")
        self.assertEqual(django[2]["X-RateLimit-Remaining"], "0")

    def check_activity(
        self,
        channel: pika.adapters.blocking_connection.BlockingChannel,
        shards: int,
        user: UserProfile,
        custom_user: UserProfile,
        custom_name: str,
    ) -> None:
        mobile = Client.objects.get(name="ZulipMobile")
        custom = Client.objects.get(name=custom_name)
        website = Client.objects.get(name="website")
        expected = Counter(
            {(user.id, mobile.id): 6, (custom_user.id, custom.id): 2, (user.id, website.id): 2}
        )
        events = []
        for shard in range(1, shards + 1):
            queue = f"user_activity_shard{shard}"
            while True:
                delivery, properties, body = channel.basic_get(queue, auto_ack=True)
                if delivery is None:
                    break
                assert properties is not None and body is not None
                event = orjson.loads(body)
                self.assertEqual(properties.delivery_mode, 2)
                self.assertEqual(set(event), {"query", "user_profile_id", "time", "client_id"})
                self.assertEqual(event["query"], "list_alert_words")
                self.assertEqual(event["user_profile_id"] % shards + 1, shard)
                self.assertIs(type(event["time"]), int)
                self.assertLessEqual(abs(event["time"] - int(time.time())), 10)
                events.append(event)
        self.assertEqual(
            Counter((event["user_profile_id"], event["client_id"]) for event in events), expected
        )

        def activity_counts() -> Counter[tuple[int, int]]:
            return Counter(
                {
                    (user_id, client_id): count
                    for user_id, client_id, count in UserActivity.objects.filter(
                        user_profile_id__in=[user.id, custom_user.id], query="list_alert_words"
                    ).values_list("user_profile_id", "client_id", "count")
                }
            )

        with transaction.atomic(durable=True):
            previous = activity_counts()
            UserActivityWorker(disable_timeout=True).consume_batch(events)
            self.assertEqual(activity_counts(), previous + expected)
            transaction.set_rollback(True)

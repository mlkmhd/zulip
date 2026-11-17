import os
import re
import socket
import time
from base64 import b64encode
from collections import Counter
from contextlib import nullcontext
from email.utils import parsedate_to_datetime
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Literal
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import quote, urlencode, urlsplit

import orjson
import pika
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.test import LiveServerTestCase
from django.urls import get_resolver
from django.utils.timezone import now as timezone_now

from zerver.actions.alert_words import do_add_alert_words, do_remove_alert_words
from zerver.lib import cache as zulip_cache
from zerver.lib import redis_utils
from zerver.lib.alert_words import alert_words_in_realm, get_alert_word_automaton, user_alert_words
from zerver.lib.rate_limiter import RateLimitedUser, RedisRateLimiterBackend
from zerver.lib.test_classes import ZulipTestCase, ZulipTransactionTestCase
from zerver.lib.test_helpers import most_recent_message, most_recent_usermessage
from zerver.lib.test_rust_http import request_test_http, rust_http_service
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

    def test_api_remove_alert_words_refreshes_realm_caches_and_event(self) -> None:
        user = self.get_user()
        other = self.example_user("othello")
        do_add_alert_words(user, ["ος", "hello", "keep"])
        do_add_alert_words(other, ["ος"])

        self.assertCountEqual(alert_words_in_realm(user.realm)[user.id], ["ος", "hello", "keep"])
        automaton = get_alert_word_automaton(user.realm)
        self.assertEqual(automaton.get("ος"), ("ος", {user.id, other.id}))
        self.assertEqual(automaton.get("hello"), ("hello", {user.id}))

        with self.capture_send_event_calls(expected_num_events=1) as events:
            result = self.api_delete(
                user,
                "/api/v1/users/me/alert_words",
                {"alert_words": orjson.dumps(["ΟΣ", "HELLO", "hello", "absent"]).decode()},
            )
            self.assertEqual(self.assert_json_success(result)["alert_words"], ["keep"])
            self.assertEqual(events, [])

        self.assertEqual(
            events,
            [{"event": {"type": "alert_words", "alert_words": ["keep"]}, "users": [user.id]}],
        )
        self.assertEqual(user_alert_words(user), ["keep"])
        self.assertIn("ος", user_alert_words(other))
        self.assertEqual(alert_words_in_realm(user.realm)[user.id], ["keep"])
        automaton = get_alert_word_automaton(user.realm)
        self.assertEqual(automaton.get("ος"), ("ος", {other.id}))
        self.assertNotIn("hello", automaton)

        with self.capture_send_event_calls(expected_num_events=1) as no_op_events:
            result = self.api_delete(user, "/api/v1/users/me/alert_words", {"alert_words": "[]"})
            self.assertEqual(self.assert_json_success(result)["alert_words"], ["keep"])
        self.assertEqual(no_op_events, events)

    def test_remove_alert_words_rolls_back_with_transaction(self) -> None:
        user = self.get_user()
        do_add_alert_words(user, ["rollback"])

        def fail_cache_invalidation(realm_id: int) -> None:
            self.assertEqual(user_alert_words(user), [])
            raise RuntimeError("abort alert word deletion")

        with (
            self.capture_send_event_calls(expected_num_events=0),
            self.assertRaisesMessage(RuntimeError, "abort alert word deletion"),
            patch("zerver.lib.alert_words.flush_realm_alert_words", fail_cache_invalidation),
        ):
            do_remove_alert_words(user, ["ROLLBACK"])

        self.assertEqual(user_alert_words(user), ["rollback"])

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


@skipUnless(
    os.environ.get("ZULIP_RUST_ALERT_WORD_CACHE_TEST_ADDRESS"),
    "Dedicated Memcached test instance is unavailable",
)
class AlertWordMemcachedTest(ZulipTestCase):
    def test_invalidate_alert_words_across_deployment_prefixes(self) -> None:
        user = self.example_user("hamlet")
        other_realm = Realm.objects.get(string_id="zephyr")
        address = os.environ["ZULIP_RUST_ALERT_WORD_CACHE_TEST_ADDRESS"]
        cache_settings = {
            **settings.CACHES,
            "default": {
                "BACKEND": "zerver.lib.singleton_bmemcached.SingletonBMemcached",
                "LOCATION": address,
                "KEY_PREFIX": "django-test",
                "VERSION": 7,
                "OPTIONS": {},
            },
        }
        with (
            self.settings(CACHES=cache_settings, PRODUCTION=True),
            TemporaryDirectory() as deployments_dir,
            patch("zerver.lib.cache.DEPLOYMENTS_DIR", deployments_dir),
        ):
            current_prefix = zulip_cache.KEY_PREFIX
            previous_prefix = f"{current_prefix}previous:"
            cache.set("cache_key_prefixes", [current_prefix, previous_prefix])
            list_key = zulip_cache.realm_alert_words_cache_key(user.realm_id)
            automaton_key = zulip_cache.realm_alert_words_automaton_cache_key(user.realm_id)
            other_realm_key = zulip_cache.realm_alert_words_cache_key(other_realm.id)
            other_realm_entry = f"{previous_prefix}{other_realm_key}"
            entries = [
                f"{prefix}{key}"
                for prefix in (current_prefix, previous_prefix)
                for key in (list_key, automaton_key)
            ]
            try:
                for entry in [*entries, other_realm_entry]:
                    cache.set(entry, "cached")
                    self.assertEqual(cache.get(entry), "cached")
                AlertWord.objects.bulk_create(
                    [AlertWord(user_profile=user, realm=user.realm, word="remove me")]
                )
                with self.capture_send_event_calls(expected_num_events=1):
                    do_remove_alert_words(user, ["REMOVE ME"])
                self.assertFalse(
                    AlertWord.objects.filter(user_profile=user, word="remove me").exists()
                )
                for entry in entries:
                    self.assertIsNone(cache.get(entry))
                with self.capture_send_event_calls(expected_num_events=1):
                    do_remove_alert_words(user, ["REMOVE ME"])
                self.assertEqual(cache.get(other_realm_entry), "cached")
                self.assertEqual(cache.get("cache_key_prefixes"), [current_prefix, previous_prefix])
            finally:
                cache.delete_many([*entries, other_realm_entry, "cache_key_prefixes"])

    @skipUnless(os.environ.get("ZULIP_RUST_ALERT_WORD_CACHE") == "1", "Rust cache is disabled")
    def test_memcached_connection_failure_rolls_back_alert_word_deletion(self) -> None:
        user = self.example_user("hamlet")
        AlertWord.objects.bulk_create(
            [AlertWord(user_profile=user, realm=user.realm, word="keep on cache failure")]
        )
        with socket.socket() as unused_port:
            unused_port.bind(("127.0.0.1", 0))
            address = f"127.0.0.1:{unused_port.getsockname()[1]}"
            cache_settings = {
                **settings.CACHES,
                "default": {
                    "BACKEND": "zerver.lib.singleton_bmemcached.SingletonBMemcached",
                    "LOCATION": address,
                    "OPTIONS": {},
                },
            }
            with (
                self.settings(CACHES=cache_settings),
                self.capture_send_event_calls(expected_num_events=0),
                self.assertRaises(ConnectionError),
            ):
                do_remove_alert_words(user, ["KEEP ON CACHE FAILURE"])

        self.assertIn("keep on cache failure", user_alert_words(user))


@skipUnless(
    os.environ.get("ZULIP_RUST_ALERT_WORD_CACHE_TEST_ADDRESS")
    and os.environ.get("ZULIP_RUST_ACTIVITY_TEST_VHOST"),
    "Dedicated Memcached and RabbitMQ fixtures are unavailable",
)
class AlertWordWriteHTTPTest(ZulipTransactionTestCase, LiveServerTestCase):
    def test_delete_http_cache_and_event_parity(self) -> None:
        self.check_write_http_cache_and_event_parity("DELETE")

    def test_post_http_cache_and_event_parity(self) -> None:
        self.check_write_http_cache_and_event_parity("POST")

    def test_delete_tornado_shard_parity(self) -> None:
        self.check_write_http_cache_and_event_parity("DELETE", sharded=True)

    def check_write_http_cache_and_event_parity(
        self, method: Literal["POST", "DELETE"], *, sharded: bool = False
    ) -> None:
        adding = method == "POST"
        vhost = os.environ["ZULIP_RUST_ACTIVITY_TEST_VHOST"]
        assert vhost.startswith("zulip-rust-test-")
        password = settings.RABBITMQ_PASSWORD
        assert password is not None
        amqp_url = (
            f"amqp://{quote(settings.RABBITMQ_USERNAME, safe='')}:{quote(password, safe='')}"
            f"@{settings.RABBITMQ_HOST}:{settings.RABBITMQ_PORT}/{quote(vhost, safe='')}"
        )
        address = os.environ["ZULIP_RUST_ALERT_WORD_CACHE_TEST_ADDRESS"]
        cache_settings = {
            **settings.CACHES,
            "default": {
                "BACKEND": "zerver.lib.singleton_bmemcached.SingletonBMemcached",
                "LOCATION": address,
                "KEY_PREFIX": "django-test",
                "VERSION": 7,
                "OPTIONS": {},
            },
        }
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None
        user = self.example_user("hamlet")
        other = self.example_user("othello")
        ports = [9983, 9984] if sharded else [9983]
        # Arrange for this user to use the second process, unlike an unmapped realm.
        realm_ports = ports if user.id % 2 else ports[::-1]
        realm_shards = {user.realm.host: realm_ports} if sharded else {}
        notify_queue = "notify_tornado_port_9984" if sharded else "notify_tornado"
        other_queues = ("notify_tornado", "notify_tornado_port_9983") if sharded else ()
        queues = (notify_queue, "user_activity", *other_queues)
        words = AlertWord.objects.filter(user_profile=user)
        original_words = list(words)
        modern_upper = "\u1c89"
        modern_lower = "\u1c8a"
        initial_words = ["hello", "ος", "οσ'α", "i\u0307", "keep", modern_lower]
        after_delete = ["keep"] if modern_upper.lower() == modern_lower else ["keep", modern_lower]
        unicode_limit = "🚀" * 100
        after_post = [*initial_words, "فوری", "FOO", "σ", unicode_limit]
        remaining_words = after_post if adding else after_delete
        (shared_word,) = AlertWord.objects.bulk_create(
            [AlertWord(user_profile=other, realm=user.realm, word="hello")]
        )
        other_words = user_alert_words(other)
        basic = {
            "Authorization": "Basic "
            + b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
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
        cookie = {
            "Cookie": f"{settings.SESSION_COOKIE_NAME}={session.session_key}; {settings.CSRF_COOKIE_NAME}={token}"
        }
        remove = urlencode(
            {
                "alert_words": orjson.dumps(
                    ["HELLO", "ΟΣ", "ΟΣ'Α", "İ", modern_upper, "absent"]
                ).decode()
            }
        )
        add = urlencode(
            {
                "alert_words": orjson.dumps(
                    ["  HELLO", "\x1fفوری\x1e", " foo", " FOO ", "   ", "Σ", "σ", unicode_limit]
                ).decode()
            }
        )
        payload = add if adding else remove
        media = {"Content-Type": "application/x-www-form-urlencoded"}
        # Load the URLconf with the usual test settings before enabling deployment cache prefixes.
        get_resolver().resolve("/api/v1/users/me/alert_words")
        with (
            self.settings(
                CACHES=cache_settings,
                PRODUCTION=True,
                USING_RABBITMQ=True,
                RABBITMQ_VHOST=vhost,
                TORNADO_PORTS=ports,
                TORNADO_PROCESSES=len(ports),
                SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
            ),
            pika.BlockingConnection(pika.URLParameters(amqp_url)) as broker,
            TemporaryDirectory() as directory,
            patch("zerver.lib.cache.DEPLOYMENTS_DIR", directory),
            patch.dict("zerver.tornado.sharding.shard_map", realm_shards),
        ):
            channel = broker.channel()
            for queue in queues:
                channel.queue_declare(queue, durable=True)
                channel.queue_purge(queue)
            prefixes = [zulip_cache.KEY_PREFIX, f"{zulip_cache.KEY_PREFIX}previous:"]
            cache.set("cache_key_prefixes", prefixes)
            keys = [
                prefix + key
                for prefix in prefixes
                for key in (
                    zulip_cache.realm_alert_words_cache_key(user.realm_id),
                    zulip_cache.realm_alert_words_automaton_cache_key(user.realm_id),
                )
            ]
            unrelated = prefixes[1] + zulip_cache.realm_alert_words_cache_key(
                Realm.objects.get(string_id="zephyr").id
            )
            manifest = Path(directory) / "cache.json"
            lowercase = Path(directory) / "lowercase.json"
            secret = Path(directory) / "secret"
            secret.write_text(settings.SECRET_KEY)
            use_rust = os.environ.get("ZULIP_RUST_HTTP_BIN")
            if use_rust:
                call_command("export_rust_alert_word_cache", manifest, lowercase_output=lowercase)
            environment = {
                "ZULIP_RUST_AMQP_URL": amqp_url,
                "ZULIP_RUST_ALERT_WORD_POSTS": str(int(adding)),
                "ZULIP_RUST_ALERT_WORD_CACHE_MANIFEST": str(manifest),
                "ZULIP_RUST_ALERT_WORD_LOWERCASE_FILE": str(lowercase),
                "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
                "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                    settings.AUTHENTICATION_BACKENDS
                ).decode(),
                "ZULIP_RUST_SESSION_COOKIE_NAME": settings.SESSION_COOKIE_NAME,
                "ZULIP_RUST_CSRF_COOKIE_NAME": settings.CSRF_COOKIE_NAME,
            }
            if sharded and use_rust:
                routing = Path(directory) / "tornado-routing.json"
                call_command("export_rust_tornado_routing", routing)
                resolved = orjson.loads(routing.read_bytes())["realm_queues"]
                self.assertEqual(
                    resolved[str(user.realm_id)],
                    [f"notify_tornado_port_{port}" for port in realm_ports],
                )
                environment["ZULIP_RUST_TORNADO_ROUTING_FILE"] = str(routing)

            def check_request(
                port: int,
                path: str,
                headers: dict[str, str],
                body: str,
                status: int,
                remaining: list[str] | None,
            ) -> tuple[int, bytes, dict[str, str | None]]:
                words.delete()
                AlertWord.objects.bulk_create(
                    [
                        AlertWord(user_profile=user, realm=user.realm, word=word)
                        for word in initial_words
                    ]
                )
                for key in [*keys, unrelated]:
                    cache.set(key, "cached")
                read_override = adding and "method=GET" in body
                delete_override = adding and "method=DELETE" in body
                response = request_test_http(
                    port, method, user.realm.host, headers, path, body=body
                )
                self.assertEqual(response[0], status, response[1])
                self.assertCountEqual(
                    user_alert_words(user), initial_words if remaining is None else remaining
                )
                self.assertCountEqual(user_alert_words(other), other_words)
                self.assertEqual(cache.get(unrelated), "cached")
                for key in keys:
                    self.assertEqual(cache.get(key), "cached" if remaining is None else None)
                _, _, event = channel.basic_get(notify_queue, auto_ack=True)
                for queue in other_queues:
                    self.assertIsNone(channel.basic_get(queue, auto_ack=True)[2])
                _, _, activity = channel.basic_get("user_activity", auto_ack=True)
                if status in (401, 403, 405):
                    self.assertIsNone(activity)
                else:
                    assert activity is not None
                    activity_data = orjson.loads(activity)
                    self.assertEqual(activity_data["user_profile_id"], user.id)
                    expected_activity = (
                        "list_alert_words"
                        if read_override
                        else "remove_alert_words"
                        if delete_override or not adding
                        else "add_alert_words"
                    )
                    self.assertEqual(activity_data["query"], expected_activity)
                    self.assertIsNone(channel.basic_get("user_activity", auto_ack=True)[2])
                if remaining is None:
                    self.assertIsNone(event)
                    if read_override:
                        self.assertCountEqual(
                            orjson.loads(response[1])["alert_words"], initial_words
                        )
                else:
                    self.assertCountEqual(orjson.loads(response[1])["alert_words"], remaining)
                    assert event is not None
                    payload = orjson.loads(event)
                    self.assertEqual(payload["users"], [user.id])
                    self.assertEqual(payload["event"]["type"], "alert_words")
                    self.assertCountEqual(payload["event"]["alert_words"], remaining)
                    self.assertIsNone(channel.basic_get(notify_queue, auto_ack=True)[2])
                response_body = response[1]
                if status == 200:
                    # Neither query specifies an order for the alert-word list.
                    data = orjson.loads(response_body)
                    data["alert_words"].sort()
                    response_body = orjson.dumps(data)
                return response[0], response_body, response[2]

            api = "/api/v1/users/me/alert_words"
            web = "/json/users/me/alert_words"
            try:
                for scheme in ("http", "https"):
                    secure = {"X-Forwarded-Proto": "https"} if scheme == "https" else {}
                    browser = {**media, **secure, **cookie, "X-CSRFToken": token}
                    origin = {"Origin": f"{scheme}://{user.realm.host}"}
                    cases = [
                        ("web valid", web, {**browser, **origin}, payload, 200, remaining_words),
                        (
                            "web CSRF in body",
                            web,
                            {**media, **secure, **cookie, **origin},
                            f"{payload}&csrfmiddlewaretoken={token}",
                            200 if adding else 403,
                            remaining_words if adding else None,
                        ),
                        (
                            "web other origin",
                            web,
                            {**browser, "Origin": f"{scheme}://other.testserver"},
                            payload,
                            403,
                            None,
                        ),
                        ("web anonymous", web, {**media, **secure}, payload, 401, None),
                    ]
                    if scheme == "https":
                        cases.extend(
                            [
                                (
                                    "secure referer",
                                    web,
                                    {**browser, "Referer": f"https://{user.realm.host}/"},
                                    payload,
                                    200,
                                    remaining_words,
                                ),
                                ("missing referer", web, browser, payload, 403, None),
                            ]
                        )
                    else:
                        authorized = {**media, **basic}
                        cases.extend(
                            [
                                ("API valid", api, authorized, payload, 200, remaining_words),
                                (
                                    "API empty",
                                    api,
                                    authorized,
                                    "alert_words=%5B%5D",
                                    200,
                                    initial_words,
                                ),
                                (
                                    "API body overrides query",
                                    f"{api}?alert_words=invalid&extra=1&client=ZulipMobile",
                                    authorized,
                                    f"{payload}&client=website"
                                    if adding
                                    else f"{payload}&method=GET&client=ignored",
                                    200,
                                    remaining_words,
                                ),
                                (
                                    "API query only",
                                    f"{api}?{payload}",
                                    authorized,
                                    "",
                                    200,
                                    remaining_words,
                                ),
                                (
                                    "API multipart",
                                    api,
                                    {
                                        **basic,
                                        "Content-Type": "multipart/form-data; boundary=words",
                                    },
                                    '--words\r\nContent-Disposition: form-data; name="alert_words"\r\n\r\n["HELLO"]\r\n--words--\r\n',
                                    200,
                                    initial_words if adding else initial_words[1:],
                                ),
                                ("API missing", api, authorized, "", 400, None),
                                ("API malformed", api, authorized, "alert_words=%5B", 400, None),
                                ("API wrong shape", api, authorized, "alert_words=null", 400, None),
                                (
                                    "API wrong item",
                                    api,
                                    authorized,
                                    "alert_words=%5B%22ok%22%2CNaN%5D",
                                    400,
                                    None,
                                ),
                                ("API anonymous", api, media, payload, 401, None),
                            ]
                        )
                    if adding and scheme == "http":
                        cases.extend(
                            [
                                (
                                    "API length before trimming",
                                    api,
                                    authorized,
                                    urlencode(
                                        {
                                            "alert_words": orjson.dumps(
                                                ["valid", " " + unicode_limit]
                                            ).decode()
                                        }
                                    ),
                                    400,
                                    None,
                                ),
                                (
                                    "API method override GET",
                                    api,
                                    authorized,
                                    "method=GET",
                                    200,
                                    None,
                                ),
                                (
                                    "API method override DELETE",
                                    api,
                                    authorized,
                                    f"{remove}&method=DELETE",
                                    200,
                                    after_delete,
                                ),
                                (
                                    "API unsupported method override",
                                    api,
                                    authorized,
                                    "method=PATCH",
                                    405,
                                    None,
                                ),
                                (
                                    "web form token precedence",
                                    web,
                                    {**browser, **origin, "X-CSRFToken": "wrong"},
                                    f"{add}&csrfmiddlewaretoken={token}",
                                    200,
                                    after_post,
                                ),
                            ]
                        )
                    if sharded:
                        cases = [case for case in cases if case[0] in {"API valid", "web valid"}]
                    service = (
                        rust_http_service({**environment, "ZULIP_RUST_WEB_POST_SCHEME": scheme})
                        if use_rust
                        else nullcontext(None)
                    )
                    with service as rust_port:
                        for label, path, headers, body, status, remaining in cases:
                            with self.subTest(scheme=scheme, case=label):
                                django = check_request(
                                    django_port, path, headers, body, status, remaining
                                )
                                if rust_port is not None:
                                    rust = check_request(
                                        rust_port, path, headers, body, status, remaining
                                    )
                                    self.assertEqual(rust, django)
                        if scheme == "http" and rust_port is not None:
                            original_manifest = manifest.read_bytes()
                            with socket.socket() as unused_port:
                                unused_port.bind(("127.0.0.1", 0))
                                failed = {
                                    **orjson.loads(original_manifest),
                                    "address": f"127.0.0.1:{unused_port.getsockname()[1]}",
                                }
                                manifest.write_bytes(orjson.dumps(failed))
                                check_request(
                                    rust_port, api, {**media, **basic}, payload, 503, None
                                )
                            manifest.write_bytes(original_manifest)
                            check_request(
                                rust_port, api, {**media, **basic}, payload, 200, remaining_words
                            )
                if sharded and use_rust:
                    stale = Path(directory) / "stale-routing.json"
                    stale.write_bytes(orjson.dumps({"realm_queues": {}}))
                    with rust_http_service(
                        {**environment, "ZULIP_RUST_TORNADO_ROUTING_FILE": str(stale)}
                    ) as rust_port:
                        check_request(rust_port, api, {**media, **basic}, payload, 503, None)
            finally:
                words.delete()
                AlertWord.objects.bulk_create(original_words)
                shared_word.delete()
                cache.delete_many([*keys, unrelated, "cache_key_prefixes"])
                for queue in queues:
                    channel.queue_purge(queue)


@skipUnless(os.environ.get("ZULIP_RUST_HTTP_BIN"), "Rust HTTP service binary is unavailable")
class AlertWordsRustHTTPTest(ZulipTransactionTestCase, LiveServerTestCase):
    @skipUnless(
        os.environ.get("ZULIP_RUST_ALERT_WORD_CACHE_TEST_ADDRESS"),
        "Dedicated Memcached test instance is unavailable",
    )
    def test_alert_word_cache_manifest_readiness(self) -> None:
        user = self.example_user("hamlet")
        address = os.environ["ZULIP_RUST_ALERT_WORD_CACHE_TEST_ADDRESS"]
        default_cache = {
            "BACKEND": "zerver.lib.singleton_bmemcached.SingletonBMemcached",
            "LOCATION": address,
            "KEY_PREFIX": zulip_cache.KEY_PREFIX,
            "VERSION": 7,
            "OPTIONS": {},
        }
        cache_settings = {**settings.CACHES, "default": default_cache}
        with (
            self.settings(CACHES=cache_settings, PRODUCTION=True),
            TemporaryDirectory() as work_dir,
            patch("zerver.lib.cache.DEPLOYMENTS_DIR", work_dir),
            NamedTemporaryFile(mode="w", encoding="utf-8") as secret,
        ):
            secret.write(settings.SECRET_KEY)
            secret.flush()
            manifest = Path(work_dir) / "alert-word-cache.json"
            prefixes = [zulip_cache.KEY_PREFIX, "previous:"]
            cache.set("cache_key_prefixes", prefixes)
            try:
                with rust_http_service(
                    {
                        "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": secret.name,
                        "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                            settings.AUTHENTICATION_BACKENDS
                        ).decode(),
                        "ZULIP_RUST_ALERT_WORD_CACHE_MANIFEST": str(manifest),
                    }
                ) as rust_port:

                    def assert_readiness(expected: int) -> None:
                        status, body, _, _ = request_test_http(
                            rust_port, "GET", user.realm.host, {}, "/health/rust-alert-words"
                        )
                        self.assertEqual(status, expected, body)
                        self.assertEqual(orjson.loads(body), {"ready": expected == 200})

                    assert_readiness(503)
                    call_command("export_rust_alert_word_cache", str(manifest))
                    assert_readiness(200)
                    exported = orjson.loads(manifest.read_bytes())
                    self.assertEqual(
                        exported,
                        {
                            "address": address,
                            "key_prefixes": sorted(cache.make_key(prefix) for prefix in prefixes),
                        },
                    )
                    new_prefix = "new-deployment:"
                    cache.set("cache_key_prefixes", [*prefixes, new_prefix])
                    call_command("export_rust_alert_word_cache", str(manifest))
                    self.assertIn(
                        cache.make_key(new_prefix),
                        orjson.loads(manifest.read_bytes())["key_prefixes"],
                    )
                    assert_readiness(200)

                    before = manifest.read_bytes()
                    for unsupported in (
                        {"OPTIONS": {"username": "memcached-user"}},
                        {"KEY_FUNCTION": "builtins.str"},
                        {"LOCATION": "192.0.2.1:11211"},
                    ):
                        with (
                            self.settings(
                                CACHES={
                                    **cache_settings,
                                    "default": {**default_cache, **unsupported},
                                }
                            ),
                            self.assertRaises(CommandError),
                        ):
                            call_command("export_rust_alert_word_cache", str(manifest))
                        self.assertEqual(manifest.read_bytes(), before)
                    cache.set("cache_key_prefixes", [""])
                    with self.assertRaisesMessage(
                        CommandError, "Active Memcached deployment prefixes are unavailable"
                    ):
                        call_command("export_rust_alert_word_cache", str(manifest))
                    self.assertEqual(manifest.read_bytes(), before)
                    cache.set("cache_key_prefixes", [*prefixes, new_prefix])
                    assert_readiness(200)

                    manifest.write_bytes(b"{")
                    assert_readiness(503)
                    manifest.write_bytes(orjson.dumps({**exported, "key_prefixes": []}))
                    assert_readiness(503)
                    with socket.socket() as unused_port:
                        unused_port.bind(("127.0.0.1", 0))
                        invalid_address = f"127.0.0.1:{unused_port.getsockname()[1]}"
                        manifest.write_bytes(orjson.dumps({**exported, "address": invalid_address}))
                        assert_readiness(503)
                    call_command("export_rust_alert_word_cache", str(manifest))
                    assert_readiness(200)
            finally:
                cache.delete("cache_key_prefixes")

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
            with rust_http_service({}) as rust_port:
                django_port = urlsplit(self.live_server_url).port
                assert django_port is not None
                self.compare_cases(
                    django_port, rust_port, user, other_realm, inactive, deactivated, webhook
                )
        finally:
            word.delete()
            UserProfile.objects.filter(id=inactive.id).update(is_active=inactive.is_active)
            Realm.objects.filter(id=realm.id).update(deactivated=realm.deactivated)

    def test_postgresql_connection_recovery(self) -> None:
        user = self.example_user("hamlet")
        authorization = (
            "Basic " + b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
        )
        headers = {"Authorization": authorization}
        application_name = "zulip-rust-alert-words-recovery-test"
        with rust_http_service({}, database_application_name=application_name) as rust_port:
            before = self.request_words(rust_port, "GET", user.realm.host, headers)
            self.assertEqual(before[0], 200)

            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pid FROM pg_stat_activity WHERE datname = current_database() "
                    "AND application_name = %s",
                    [application_name],
                )
                [(pid,)] = cursor.fetchall()
                cursor.execute("SELECT pg_terminate_backend(%s, 5000)", [pid])
                self.assertEqual(cursor.fetchone(), (True,))

            # A request racing with detection of the disconnect can still see a 503.
            after = self.request_words(rust_port, "GET", user.realm.host, headers)
            if after[0] == 503:
                after = self.request_words(rust_port, "GET", user.realm.host, headers)
            self.assertEqual(after[:3], before[:3])

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
                with rust_http_service(self.django_session_environment(secret.name)) as rust_port:
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
        path = ("/json" if browser else "/api/v1") + "/users/me/alert_words"
        if query:
            path += f"?{query}"
        return request_test_http(port, method, host, headers, path)

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
            "ZULIP_RUST_CLIENT_IP_SOURCE": "proxy",
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
        self.assertFalse(Client.objects.filter(name="InTeRnAl").exists())
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
                ):
                    with rust_http_service(environment) as rust_port:
                        self.check_shared_quota(rust_port, user, custom_user)
                        self.check_session_quota(rust_port, user)
                    with (
                        self.settings(DEBUG_RATE_LIMITING=True),
                        rust_http_service(
                            {**environment, "ZULIP_RUST_DEBUG_RATE_LIMITING": "1"}
                        ) as rust_port,
                    ):
                        self.check_debug_internal_quota(rust_port, user)
                self.check_activity(channel, shards, user, custom_user, custom_name)
            finally:
                RateLimitedUser(user).clear_history()
                RateLimitedUser(custom_user).clear_history()
                Client.objects.filter(name__in=[custom_name, "InTeRnAl"]).delete()
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
            port: int,
            profile: UserProfile,
            method: str = "GET",
            *,
            endpoint: str = "users/me/alert_words",
            real_ip: str = "192.0.2.1",
            **query: str,
        ) -> tuple[int, bytes, dict[str, str | None], str | None]:
            authorization = (
                "Basic "
                + b64encode(f"{profile.delivery_email}:{profile.api_key}".encode()).decode()
            )
            return request_test_http(
                port,
                method,
                profile.realm.host,
                {"Authorization": authorization, "X-Real-IP": real_ip},
                f"/api/v1/{endpoint}?{urlencode(query)}",
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
                status, body, headers, _ = request(port, user, endpoint="saved_snippets")
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

        for port in (django_port, rust_port):
            for address, status in (("127.0.0.1", 200), ("::1", 200), ("192.0.2.1", 429)):
                result, _, headers, _ = request(
                    port, user, endpoint="saved_snippets", real_ip=address, client="InTeRnAl"
                )
                self.assertEqual(result, status)
                self.assertEqual(headers["X-RateLimit-Limit"], "3" if status == 429 else None)
        self.assertEqual(redis.hgetall(gcra), snapshot)

        client = "Rust+parity café " * 3
        rust = request(rust_port, custom_user, "HEAD", endpoint="saved_snippets", client=client)
        django = request(django_port, custom_user, endpoint="saved_snippets", client=client)
        self.assertEqual(rust[:2], (200, b""))
        self.assertEqual(django[0], 429)
        self.assertEqual(rust[2]["X-RateLimit-Limit"], "2")
        self.assertEqual(django[2]["X-RateLimit-Limit"], "2")
        self.assertEqual(django[2]["X-RateLimit-Remaining"], "0")

    def check_debug_internal_quota(self, rust_port: int, user: UserProfile) -> None:
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None
        authorization = (
            "Basic " + b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
        )
        headers = {"Authorization": authorization}
        path = "/api/v1/saved_snippets"
        for port in (django_port, rust_port):
            result, _, response_headers, _ = request_test_http(
                port, "GET", user.realm.host, headers, path + "?client=InTeRnAl"
            )
            self.assertEqual(result, 200)
            self.assertIsNone(response_headers["X-RateLimit-Limit"])
            result, _, response_headers, _ = request_test_http(
                port, "GET", user.realm.host, headers, path
            )
            self.assertEqual(result, 429)
            self.assertEqual(response_headers["X-RateLimit-Limit"], "3")

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
        internal = Client.objects.get(name="InTeRnAl")
        expected = Counter(
            {
                ("list_alert_words", user.id, mobile.id): 2,
                ("get_saved_snippets", user.id, mobile.id): 6,
                ("get_saved_snippets", custom_user.id, custom.id): 2,
                ("get_saved_snippets", user.id, internal.id): 8,
                ("list_alert_words", user.id, website.id): 2,
            }
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
                self.assertEqual(event["user_profile_id"] % shards + 1, shard)
                self.assertIs(type(event["time"]), int)
                self.assertLessEqual(abs(event["time"] - int(time.time())), 10)
                events.append(event)
        self.assertEqual(
            Counter(
                (event["query"], event["user_profile_id"], event["client_id"]) for event in events
            ),
            expected,
        )

        def activity_counts() -> Counter[tuple[str, int, int]]:
            return Counter(
                {
                    (query, user_id, client_id): count
                    for query, user_id, client_id, count in UserActivity.objects.filter(
                        user_profile_id__in=[user.id, custom_user.id],
                        query__in=["list_alert_words", "get_saved_snippets"],
                    ).values_list("query", "user_profile_id", "client_id", "count")
                }
            )

        with transaction.atomic(durable=True):
            previous = activity_counts()
            UserActivityWorker(disable_timeout=True).consume_batch(events)
            self.assertEqual(activity_counts(), previous + expected)
            transaction.set_rollback(True)

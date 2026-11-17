import os
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit

import orjson
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.middleware.csrf import _get_new_csrf_string
from django.test import LiveServerTestCase, override_settings
from typing_extensions import override

from zerver.actions.create_user import do_create_user
from zerver.lib.bot_config import ConfigError, load_bot_config_template, set_bot_config
from zerver.lib.bot_lib import (
    EmbeddedBotEmptyRecipientsListError,
    EmbeddedBotHandler,
    EmbeddedBotQuitError,
    StateHandler,
)
from zerver.lib.bot_storage import StateError
from zerver.lib.display_recipient import get_display_recipient
from zerver.lib.test_classes import ZulipTestCase, ZulipTransactionTestCase
from zerver.lib.test_rust_http import request_test_http, rust_http_service
from zerver.lib.validator import check_string
from zerver.models import BotStorageData, UserProfile
from zerver.models.bots import get_service_profile
from zerver.models.realms import get_realm
from zerver.models.recipients import get_or_create_direct_message_group
from zerver.models.users import get_user


class BotStorageReadHTTPTest(ZulipTransactionTestCase, LiveServerTestCase):
    @staticmethod
    def basic(user: UserProfile) -> dict[str, str]:
        token = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def test_storage_parity_with_rust(self) -> None:
        bot = self.example_user("default_bot")
        other = self.example_user("outgoing_webhook_bot")
        human = self.example_user("hamlet")
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None

        with TemporaryDirectory() as directory, ExitStack() as cleanup:
            for owner, key, value in (
                (bot, "درود", "📝"),
                (bot, "plain", "text"),
                (other, "secret", "other bot's value"),
            ):
                row = BotStorageData.objects.create(bot_profile=owner, key=key, value=value)
                cleanup.callback(row.delete)
            session = SessionStore()
            session.update(
                {
                    "_auth_user_id": str(bot.id),
                    "_auth_user_backend": "zproject.backends.DevAuthBackend",
                    "_auth_user_hash": bot.get_session_auth_hash(),
                }
            )
            session.create()
            cleanup.callback(session.delete)
            csrf = _get_new_csrf_string()
            cookie = {
                "Cookie": f"{settings.SESSION_COOKIE_NAME}={session.session_key}; "
                f"{settings.CSRF_COOKIE_NAME}={csrf}"
            }
            secret = Path(directory) / "secret"
            secret.write_text(settings.SECRET_KEY)
            service = (
                rust_http_service(
                    {
                        "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
                        "ZULIP_RUST_BOT_STORAGE_DELETES": "1",
                        "ZULIP_RUST_WEB_POST_SCHEME": "http",
                        "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                            settings.AUTHENTICATION_BACKENDS
                        ).decode(),
                    }
                )
                if os.environ.get("ZULIP_RUST_HTTP_BIN")
                else nullcontext(None)
            )
            with service as rust_port:

                def check(method: str, path: str, headers: dict[str, str], status: int) -> bytes:
                    django = request_test_http(django_port, method, bot.realm.host, headers, path)
                    self.assertEqual(django[0], status, django[1])
                    if rust_port is not None:
                        rust = request_test_http(rust_port, method, bot.realm.host, headers, path)
                        self.assertEqual((rust[0], rust[2]), (django[0], django[2]))
                        if method == "GET":
                            self.assertEqual(orjson.loads(rust[1]), orjson.loads(django[1]))
                        else:
                            self.assertEqual(rust[1], django[1])
                    return django[1]

                for path, headers in (
                    ("/api/v1/bot_storage", self.basic(bot)),
                    ("/json/bot_storage", cookie),
                ):
                    with self.subTest(path=path):
                        expected = {"درود": "📝", "plain": "text"}
                        self.assertEqual(
                            orjson.loads(check("GET", path, headers, 200))["storage"], expected
                        )
                        self.assertEqual(check("HEAD", path, headers, 200), b"")
                        for raw, storage in (
                            ("null", expected),
                            ("[]", {}),
                            ('["درود","درود"]', {"درود": "📝"}),
                        ):
                            query = urlencode([("keys", "bad"), ("keys", raw), ("unknown", "1")])
                            result = orjson.loads(check("GET", f"{path}?{query}", headers, 200))
                            self.assertEqual(result["storage"], storage)
                            self.assertEqual(result["ignored_parameters_unsupported"], ["unknown"])
                        for raw, error in (
                            ('["secret"]', "Key does not exist."),
                            ("garbage", "keys is not valid JSON"),
                            ("{}", "keys is not a list"),
                            ("[42]", "keys[0] is not a string"),
                        ):
                            query = urlencode({"keys": raw})
                            result = orjson.loads(check("GET", f"{path}?{query}", headers, 400))
                            self.assertEqual(result["msg"], error)

                path = "/api/v1/bot_storage?keys=garbage"
                result = orjson.loads(check("GET", path, self.basic(human), 400))
                self.assertEqual(result["code"], "PERMISSION_DENIED")
                self.assertEqual(result["msg"], "Must be a bot user")

                def check_deletes(
                    port: int, path: str, headers: dict[str, str]
                ) -> list[tuple[int, dict[str, object], dict[str, str | None]]]:
                    def stored() -> dict[str, str]:
                        return dict(
                            BotStorageData.objects.filter(bot_profile=bot).values_list(
                                "key", "value"
                            )
                        )

                    def remove(
                        keys: str | None, expected: int, *, include_csrf: bool = True
                    ) -> dict[str, object]:
                        body = (
                            urlencode({"keys": keys, "unsupported": "1"})
                            if keys is not None
                            else None
                        )
                        credentials = {
                            **headers,
                            "Content-Type": "application/x-www-form-urlencoded",
                        }
                        if include_csrf and path.startswith("/json/"):
                            credentials["X-CSRFToken"] = csrf
                        status, payload, response_headers, _ = request_test_http(
                            port, "DELETE", bot.realm.host, credentials, path, body=body
                        )
                        self.assertEqual(status, expected, payload)
                        result = orjson.loads(payload)
                        responses.append((status, result, response_headers))
                        return result

                    responses: list[tuple[int, dict[str, object], dict[str, str | None]]] = []
                    original = stored()
                    try:
                        if path.startswith("/json/"):
                            remove('["plain"]', 403, include_csrf=False)
                            self.assertEqual(stored(), original)
                        for raw in ('["plain","secret"]', '["plain","plain"]'):
                            result = remove(raw, 400)
                            self.assertEqual(result["msg"], "Key does not exist.")
                            self.assertEqual(stored(), original)
                        result = remove("[]", 200)
                        self.assertEqual(result["ignored_parameters_unsupported"], ["unsupported"])
                        self.assertEqual(stored(), original)
                        remove('["درود"]', 200)
                        self.assertEqual(stored(), {"plain": "text"})
                        remove('["درود"]', 400)
                        remove("null", 200)
                        self.assertEqual(stored(), {})
                        remove(None, 200)
                        self.assertTrue(
                            BotStorageData.objects.filter(bot_profile=other, key="secret").exists()
                        )
                    finally:
                        for key, value in original.items():
                            row, created = BotStorageData.objects.get_or_create(
                                bot_profile=bot, key=key, defaults={"value": value}
                            )
                            if created:
                                cleanup.callback(row.delete)
                    return responses

                for path, headers in (
                    ("/api/v1/bot_storage", self.basic(bot)),
                    ("/json/bot_storage", cookie),
                ):
                    with self.subTest(delete=path):
                        baseline = check_deletes(django_port, path, headers)
                        if rust_port is not None:
                            self.assertEqual(check_deletes(rust_port, path, headers), baseline)

                if rust_port is not None:
                    path = "/api/v1/bot_storage"
                    auth = {
                        **self.basic(bot),
                        "Content-Type": "application/x-www-form-urlencoded",
                    }

                    def remove_concurrently(port: int, keys: str) -> int:
                        return request_test_http(
                            port,
                            "DELETE",
                            bot.realm.host,
                            auth,
                            path,
                            body=urlencode({"keys": keys}),
                        )[0]

                    with (
                        rust_http_service({"ZULIP_RUST_BOT_STORAGE_DELETES": "1"}) as second_port,
                        ThreadPoolExecutor(max_workers=2) as executor,
                    ):
                        first = executor.submit(remove_concurrently, rust_port, '["درود","plain"]')
                        second = executor.submit(remove_concurrently, second_port, '["درود"]')
                        self.assertCountEqual([first.result(), second.result()], [200, 400])
                        self.assertFalse(
                            BotStorageData.objects.filter(bot_profile=bot, key="درود").exists()
                        )
                        self.assertEqual(
                            BotStorageData.objects.filter(bot_profile=bot, key="plain").exists(),
                            first.result() == 400,
                        )

    @override_settings(USER_STATE_SIZE_LIMIT=10)
    def test_update_storage_parity_with_rust(self) -> None:
        bot = self.example_user("default_bot")
        other = self.example_user("outgoing_webhook_bot")
        human = self.example_user("hamlet")
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None
        api_path = "/api/v1/bot_storage"
        web_path = "/json/bot_storage"
        storage = BotStorageData.objects.filter(bot_profile=bot)

        def stored() -> dict[str, str]:
            return dict(storage.values_list("key", "value"))

        def request(
            port: int, path: str, headers: dict[str, str], raw: str | None, *, extra: str = ""
        ) -> tuple[int, bytes, dict[str, str | None]]:
            body = extra if raw is None else urlencode({"storage": raw}) + extra
            status, payload, response_headers, _ = request_test_http(
                port,
                "PUT",
                bot.realm.host,
                {"Content-Type": "application/x-www-form-urlencoded", **headers},
                path,
                body=body,
            )
            return status, payload, response_headers

        with TemporaryDirectory() as directory, ExitStack() as cleanup:
            cleanup.callback(storage.delete)
            foreign = BotStorageData.objects.create(bot_profile=other, key="x", value="foreign")
            cleanup.callback(foreign.delete)
            session = SessionStore()
            session.update(
                {
                    "_auth_user_id": str(bot.id),
                    "_auth_user_backend": "zproject.backends.DevAuthBackend",
                    "_auth_user_hash": bot.get_session_auth_hash(),
                }
            )
            session.create()
            cleanup.callback(session.delete)
            csrf = _get_new_csrf_string()
            cookie = {
                "Cookie": f"{settings.SESSION_COOKIE_NAME}={session.session_key}; "
                f"{settings.CSRF_COOKIE_NAME}={csrf}"
            }
            secret = Path(directory) / "secret"
            secret.write_text(settings.SECRET_KEY)
            rust_settings = {
                "ZULIP_RUST_BOT_STORAGE_DELETES": "1",
                "ZULIP_RUST_BOT_STORAGE_UPDATES": "1",
                "ZULIP_RUST_USER_STATE_SIZE_LIMIT": str(settings.USER_STATE_SIZE_LIMIT),
            }

            def exercise(port: int) -> list[tuple[int, bytes, dict[str, str | None]]]:
                responses = []

                def check(
                    path: str,
                    headers: dict[str, str],
                    raw: str | None,
                    status: int,
                    *,
                    extra: str = "",
                    message: str = "",
                ) -> dict[str, object]:
                    response = request(port, path, headers, raw, extra=extra)
                    responses.append(response)
                    self.assertEqual(response[0], status, response[1])
                    result = orjson.loads(response[1])
                    if status != 403:
                        self.assertEqual(result["msg"], message)
                    return result

                check(api_path, self.basic(human), "{}", 400, message="Must be a bot user")
                check(web_path, cookie, '{"x":"no CSRF"}', 403)
                self.assertEqual(stored(), {})
                for path, headers in (
                    (api_path, self.basic(bot)),
                    (web_path, {**cookie, "X-CSRFToken": csrf}),
                ):
                    storage.all().delete()
                    for raw, message in (
                        (None, "Missing 'storage' argument"),
                        ("invalid", "storage is not valid JSON"),
                        ("[]", "storage is not a dict"),
                        ('{"x":false,"x":"valid"}', 'storage["x"] is not a string'),
                        ('{"📝":42,"a":false}', 'storage["\\ud83d\\udcdd"] is not a string'),
                    ):
                        check(path, headers, raw, 400, message=message)
                    self.assertEqual(stored(), {})
                    result = check(
                        path,
                        headers,
                        '{"x":"old","x":"📝📝","در":"و"}',
                        200,
                        extra="&unsupported=1",
                    )
                    self.assertEqual(result["ignored_parameters_unsupported"], ["unsupported"])
                    self.assertEqual(stored(), {"x": "📝📝", "در": "و"})
                    check(path, headers, '{"x":"📝📝📝📝📝","y":""}', 200)
                    check(path, headers, "{}", 200)
                    check(
                        path,
                        headers,
                        '{"x":"replacement","z":""}',
                        400,
                        message="Request exceeds storage limit by 7 characters. The limit is 10 characters.",
                    )
                    self.assertEqual(stored(), {"x": "📝📝📝📝📝", "در": "و", "y": ""})
                    query = urlencode({"storage": '{"x":"short"}'})
                    check(f"{path}?{query}", headers, None, 200)
                    self.assertEqual(stored(), {"x": "short", "در": "و", "y": ""})
                    foreign.refresh_from_db()
                    self.assertEqual(foreign.value, "foreign")
                storage.all().delete()
                return responses

            baseline = exercise(django_port)
            if not os.environ.get("ZULIP_RUST_HTTP_BIN"):
                return
            with rust_http_service(
                {
                    **rust_settings,
                    "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
                    "ZULIP_RUST_WEB_POST_SCHEME": "http",
                    "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                        settings.AUTHENTICATION_BACKENDS
                    ).decode(),
                }
            ) as rust_port:
                self.assertEqual(exercise(rust_port), baseline)

                def race_updates(first_port: int) -> None:
                    barrier = Barrier(2, timeout=5)

                    def update(port: int, key: str) -> int:
                        barrier.wait()
                        raw = orjson.dumps({key: "📝" * 5}).decode()
                        return request(port, api_path, self.basic(bot), raw)[0]

                    with ThreadPoolExecutor(max_workers=2) as executor:
                        first = executor.submit(update, first_port, "a")
                        second = executor.submit(update, rust_port, "b")
                        outcomes = [first.result(), second.result()]
                    self.assertCountEqual(outcomes, [200, 400])
                    self.assertEqual(stored(), {"a" if outcomes[0] == 200 else "b": "📝" * 5})
                    storage.all().delete()

                race_updates(django_port)
                with rust_http_service(rust_settings) as second_port:
                    race_updates(second_port)
                    storage.create(key="full", value="📝" * 6, bot_profile=bot)
                    barrier = Barrier(2, timeout=5)

                    def update_while_deleting() -> int:
                        barrier.wait()
                        return request(rust_port, api_path, self.basic(bot), '{"new":"v"}')[0]

                    with ThreadPoolExecutor(max_workers=1) as executor:
                        update = executor.submit(update_while_deleting)
                        barrier.wait()
                        deletion = request_test_http(
                            second_port, "DELETE", bot.realm.host, self.basic(bot), api_path
                        )
                        outcome = update.result()
                    self.assertEqual(deletion[0], 200, deletion[1])
                    self.assertIn(outcome, [200, 400])
                    self.assertEqual(stored(), {"new": "v"} if outcome == 200 else {})


class TestEmbeddedBotMessaging(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.user_profile = self.example_user("othello")
        self.bot_profile = self.create_test_bot(
            "embedded",
            self.user_profile,
            full_name="Embedded bot",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name="helloworld",
            config_data=orjson.dumps({"foo": "bar"}).decode(),
        )

    def test_pm_to_embedded_bot_using_direct_group_message(self) -> None:
        direct_group_message = get_or_create_direct_message_group(
            id_list=[self.user_profile.id, self.bot_profile.id]
        )

        self.send_personal_message(self.user_profile, self.bot_profile, content="help")

        last_message = self.get_last_message()
        self.assertEqual(last_message.content, "beep boop")
        self.assertEqual(last_message.sender_id, self.bot_profile.id)
        self.assertEqual(last_message.recipient, direct_group_message.recipient)

        display_recipient = get_display_recipient(last_message.recipient)
        assert isinstance(display_recipient, list)
        self.assert_length(display_recipient, 2)
        self.assertEqual(display_recipient[0]["email"], self.user_profile.email)
        self.assertEqual(display_recipient[1]["email"], self.bot_profile.email)

    def test_stream_message_to_embedded_bot(self) -> None:
        self.send_stream_message(
            self.user_profile,
            "Denmark",
            content=f"@**{self.bot_profile.full_name}** foo",
            topic_name="bar",
        )
        last_message = self.get_last_message()
        self.assertEqual(last_message.content, "beep boop")
        self.assertEqual(last_message.sender_id, self.bot_profile.id)
        self.assertEqual(last_message.topic_name(), "bar")
        self.assert_message_stream_name(last_message, "Denmark")

    def test_stream_message_not_to_embedded_bot(self) -> None:
        self.send_stream_message(self.user_profile, "Denmark", content="foo", topic_name="bar")
        last_message = self.get_last_message()
        self.assertEqual(last_message.content, "foo")

    def test_message_to_embedded_bot_with_initialize(self) -> None:
        self.subscribe(self.user_profile, "Denmark")
        with patch(
            "zulip_bots.bots.helloworld.helloworld.HelloWorldHandler.initialize", create=True
        ) as mock_initialize:
            self.send_stream_message(
                self.user_profile,
                "Denmark",
                content=f"@**{self.bot_profile.full_name}** foo",
                topic_name="bar",
            )
            mock_initialize.assert_called_once()

    def test_embedded_bot_quit_exception(self) -> None:
        with (
            patch(
                "zulip_bots.bots.helloworld.helloworld.HelloWorldHandler.handle_message",
                side_effect=EmbeddedBotQuitError("I'm quitting!"),
            ),
            self.assertLogs(level="WARNING") as m,
        ):
            self.send_stream_message(
                self.user_profile,
                "Denmark",
                content=f"@**{self.bot_profile.full_name}** foo",
                topic_name="bar",
            )
            self.assertEqual(m.output, ["WARNING:root:I'm quitting!"])


class TestEmbeddedBotFailures(ZulipTestCase):
    def test_message_embedded_bot_with_invalid_service(self) -> None:
        user_profile = self.example_user("othello")
        self.create_test_bot(
            short_name="embedded",
            user_profile=user_profile,
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name="helloworld",
        )
        bot_profile = get_user("embedded-bot@zulip.testserver", get_realm("zulip"))
        service_profile = get_service_profile(bot_profile.id, "helloworld")
        service_profile.name = "invalid"
        service_profile.save()
        with self.assertLogs(level="ERROR") as m:
            self.send_stream_message(
                user_profile,
                "Denmark",
                content=f"@**{bot_profile.full_name}** foo",
                topic_name="bar",
            )
            self.assertEqual(
                m.output[0],
                f"ERROR:root:Error: User {bot_profile.id} has bot with invalid embedded bot service invalid",
            )


class TestEmbeddedBotStateHandler(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.user_profile = self.example_user("othello")
        self.bot_profile = do_create_user(
            email="embedded-bot-1@zulip.com",
            password="test",
            realm=get_realm("zulip"),
            full_name="EmbeddedBo1",
            bot_type=UserProfile.EMBEDDED_BOT,
            bot_owner=self.user_profile,
            acting_user=None,
        )
        self.second_bot_profile = do_create_user(
            email="embedded-bot-2@zulip.com",
            password="test",
            realm=get_realm("zulip"),
            full_name="EmbeddedBot2",
            bot_type=UserProfile.EMBEDDED_BOT,
            bot_owner=self.user_profile,
            acting_user=None,
        )

    def test_basic_storage_and_retrieval(self) -> None:
        storage = StateHandler(self.bot_profile)
        storage.put("some key", "some value")
        storage.put("some other key", "some other value")
        self.assertEqual(storage.get("some key"), "some value")
        self.assertEqual(storage.get("some other key"), "some other value")
        self.assertTrue(storage.contains("some key"))
        self.assertFalse(storage.contains("nonexistent key"))
        self.assertRaisesMessage(
            StateError, "Key does not exist.", lambda: storage.get("nonexistent key")
        )
        storage.put("some key", "a new value")
        self.assertEqual(storage.get("some key"), "a new value")
        second_storage = StateHandler(self.second_bot_profile)
        self.assertRaises(StateError, lambda: second_storage.get("some key"))
        second_storage.put("some key", "yet another value")
        self.assertEqual(storage.get("some key"), "a new value")
        self.assertEqual(second_storage.get("some key"), "yet another value")

    def test_marshaling(self) -> None:
        storage = StateHandler(self.bot_profile)
        serializable_obj = {"foo": "bar", "baz": [42, "cux"]}
        storage.put("some key", serializable_obj)
        self.assertEqual(storage.get("some key"), serializable_obj)

    # Reduce maximal storage size for faster test string construction.
    @override_settings(USER_STATE_SIZE_LIMIT=100)
    def test_storage_limit(self) -> None:
        storage = StateHandler(self.bot_profile)

        # Disable marshaling for storing a string whose size is
        # equivalent to the size of the stored object.
        storage.marshal = lambda obj: check_string("obj", obj)
        storage.demarshal = lambda obj: obj

        key = "capacity-filling entry"
        storage.put(key, "x" * (settings.USER_STATE_SIZE_LIMIT - len(key)))

        with self.assertRaisesMessage(
            StateError,
            "Request exceeds storage limit by 32 characters. The limit is 100 characters.",
        ):
            storage.put("too much data", "a few bits too long")

        second_storage = StateHandler(self.second_bot_profile)
        second_storage.put("another big entry", "x" * (settings.USER_STATE_SIZE_LIMIT - 40))
        second_storage.put("normal entry", "abcd")

    def test_entry_removal(self) -> None:
        storage = StateHandler(self.bot_profile)
        storage.put("some key", "some value")
        storage.put("another key", "some value")
        self.assertTrue(storage.contains("some key"))
        self.assertTrue(storage.contains("another key"))
        storage.remove("some key")
        self.assertFalse(storage.contains("some key"))
        self.assertTrue(storage.contains("another key"))
        self.assertRaises(StateError, lambda: storage.remove("some key"))

    def test_bot_storage_restrictions(self) -> None:
        bot_storage_url = "/api/v1/bot_storage"
        result = self.api_put(self.user_profile, bot_storage_url, {"storage": "{}"})
        self.assert_json_error(result, "Must be a bot user")
        result = self.api_get(self.user_profile, bot_storage_url)
        self.assert_json_error(result, "Must be a bot user")
        result = self.api_delete(self.user_profile, bot_storage_url)
        self.assert_json_error(result, "Must be a bot user")

    @override_settings(USER_STATE_SIZE_LIMIT=4)
    def test_bot_storage_limit_counts_unicode_code_points(self) -> None:
        endpoint = "/api/v1/bot_storage"
        initial_storage = {"🦀": "e\u0301🤝"}
        result = self.api_put(
            self.bot_profile, endpoint, {"storage": orjson.dumps(initial_storage).decode()}
        )
        self.assert_json_success(result)

        result = self.api_put(
            self.bot_profile, endpoint, {"storage": orjson.dumps({"🦀": "β", "名": "x界"}).decode()}
        )
        self.assert_json_error(
            result, "Request exceeds storage limit by 1 characters. The limit is 4 characters."
        )
        result = self.api_get(self.bot_profile, endpoint)
        self.assertEqual(self.assert_json_success(result)["storage"], initial_storage)

        updated_storage = {"🦀": "β", "名": "x"}
        result = self.api_put(
            self.bot_profile, endpoint, {"storage": orjson.dumps(updated_storage).decode()}
        )
        self.assert_json_success(result)
        result = self.api_get(self.bot_profile, endpoint)
        self.assertEqual(self.assert_json_success(result)["storage"], updated_storage)

    def test_bot_storage_endpoint(self) -> None:
        bot_storage_url = "/api/v1/bot_storage"

        # Store some data.
        initial_dict = {"key 1": "value 1", "key 2": "value 2", "key 3": "value 3"}
        params = {
            "storage": orjson.dumps(initial_dict).decode(),
        }
        result = self.api_put(self.bot_profile, bot_storage_url, params)
        self.assert_json_success(result)

        # Assert the stored data for some keys.
        params = {
            "keys": orjson.dumps(["key 1", "key 3"]).decode(),
        }
        result = self.api_get(self.bot_profile, bot_storage_url, params)
        response_dict = self.assert_json_success(result)
        self.assertEqual(response_dict["storage"], {"key 3": "value 3", "key 1": "value 1"})

        # Assert the stored data for all keys.
        result = self.api_get(self.bot_profile, bot_storage_url)
        response_dict = self.assert_json_success(result)
        self.assertEqual(response_dict["storage"], initial_dict)

        # Store some more data; update an entry and store a new entry
        dict_update = {"key 1": "new value", "key 4": "value 4"}
        params = {
            "storage": orjson.dumps(dict_update).decode(),
        }
        result = self.api_put(self.bot_profile, bot_storage_url, params)
        self.assert_json_success(result)

        # Assert the data was updated.
        updated_dict = initial_dict.copy()
        updated_dict.update(dict_update)
        result = self.api_get(self.bot_profile, bot_storage_url)
        response_dict = self.assert_json_success(result)
        self.assertEqual(response_dict["storage"], updated_dict)

        # Assert errors on invalid requests.
        invalid_params = {
            "keys": ["This is a list, but should be a serialized string."],
        }
        result = self.api_get(self.bot_profile, bot_storage_url, invalid_params)
        self.assert_json_error(result, "keys is not valid JSON")

        params = {
            "keys": orjson.dumps(["key 1", "nonexistent key"]).decode(),
        }
        result = self.api_get(self.bot_profile, bot_storage_url, params)
        self.assert_json_error(result, "Key does not exist.")

        params = {
            "storage": orjson.dumps({"foo": [1, 2, 3]}).decode(),
        }
        result = self.api_put(self.bot_profile, bot_storage_url, params)
        self.assert_json_error(result, 'storage["foo"] is not a string')

        # Remove some entries.
        keys_to_remove = ["key 1", "key 2"]
        params = {
            "keys": orjson.dumps(keys_to_remove).decode(),
        }
        result = self.api_delete(self.bot_profile, bot_storage_url, params)
        self.assert_json_success(result)

        # Assert the entries were removed.
        for key in keys_to_remove:
            updated_dict.pop(key)
        result = self.api_get(self.bot_profile, bot_storage_url)
        response_dict = self.assert_json_success(result)
        self.assertEqual(response_dict["storage"], updated_dict)

        # Try to remove an existing and a nonexistent key.
        params = {
            "keys": orjson.dumps(["key 3", "nonexistent key"]).decode(),
        }
        result = self.api_delete(self.bot_profile, bot_storage_url, params)
        self.assert_json_error(result, "Key does not exist.")

        # Assert an error has been thrown and no entries were removed.
        result = self.api_get(self.bot_profile, bot_storage_url)
        response_dict = self.assert_json_success(result)
        self.assertEqual(response_dict["storage"], updated_dict)

        # Remove the entire storage.
        result = self.api_delete(self.bot_profile, bot_storage_url)
        self.assert_json_success(result)

        # Assert the entire storage has been removed.
        result = self.api_get(self.bot_profile, bot_storage_url)
        response_dict = self.assert_json_success(result)
        self.assertEqual(response_dict["storage"], {})


class TestEmbeddedBotConfigHandler(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.user_profile = self.example_user("othello")
        self.bot_profile = self.create_test_bot(
            "embedded",
            self.user_profile,
            full_name="Embedded bot",
            bot_type=UserProfile.EMBEDDED_BOT,
            service_name="helloworld",
        )
        self.bot_handler = EmbeddedBotHandler(self.bot_profile)

    def test_basic_storage_and_retrieval(self) -> None:
        with self.assertRaises(ConfigError):
            self.bot_handler.get_config_info("foo")

        self.assertEqual(self.bot_handler.get_config_info("foo", optional=True), {})

        config_dict = {"entry 1": "value 1", "entry 2": "value 2"}
        for key, value in config_dict.items():
            set_bot_config(self.bot_profile, key, value)
        self.assertEqual(self.bot_handler.get_config_info("foo"), config_dict)

        config_update = {"entry 2": "new value", "entry 3": "value 3"}
        for key, value in config_update.items():
            set_bot_config(self.bot_profile, key, value)
        config_dict.update(config_update)
        self.assertEqual(self.bot_handler.get_config_info("foo"), config_dict)

    @override_settings(BOT_CONFIG_SIZE_LIMIT=100)
    def test_config_entry_limit(self) -> None:
        set_bot_config(self.bot_profile, "some key", "x" * (settings.BOT_CONFIG_SIZE_LIMIT - 8))
        self.assertRaisesMessage(
            ConfigError,
            "Cannot store configuration. Request would require 101 characters. "
            "The current configuration size limit is 100 characters.",
            lambda: set_bot_config(
                self.bot_profile, "some key", "x" * (settings.BOT_CONFIG_SIZE_LIMIT - 8 + 1)
            ),
        )
        set_bot_config(self.bot_profile, "some key", "x" * (settings.BOT_CONFIG_SIZE_LIMIT - 20))
        set_bot_config(self.bot_profile, "another key", "x")
        self.assertRaisesMessage(
            ConfigError,
            "Cannot store configuration. Request would require 116 characters. "
            "The current configuration size limit is 100 characters.",
            lambda: set_bot_config(self.bot_profile, "yet another key", "x"),
        )

    def test_load_bot_config_template(self) -> None:
        bot_config = load_bot_config_template("giphy")
        self.assertTrue(isinstance(bot_config, dict))
        self.assert_length(bot_config, 1)

    def test_load_bot_config_template_for_bot_without_config_data(self) -> None:
        bot_config = load_bot_config_template("converter")
        self.assertTrue(isinstance(bot_config, dict))
        self.assert_length(bot_config, 0)

    def test_bot_send_pm_with_empty_recipients_list(self) -> None:
        with self.assertRaisesRegex(
            EmbeddedBotEmptyRecipientsListError, "Message must have recipients!"
        ):
            self.bot_handler.send_message(message={"type": "private", "to": []})

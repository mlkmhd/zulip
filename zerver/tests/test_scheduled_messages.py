import os
import re
import time
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, nullcontext
from datetime import timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any
from unittest import mock, skipUnless
from urllib.parse import quote, urlsplit

import orjson
import pika
import time_machine
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.test import LiveServerTestCase
from django.utils.timezone import now as timezone_now
from typing_extensions import override

from zerver.actions.scheduled_messages import (
    SCHEDULED_MESSAGE_LATE_CUTOFF_MINUTES,
    try_deliver_one_scheduled_message,
)
from zerver.actions.users import change_user_is_active
from zerver.lib.message import is_message_to_self
from zerver.lib.test_classes import ZulipTestCase, ZulipTransactionTestCase
from zerver.lib.test_helpers import most_recent_message
from zerver.lib.test_rust_http import request_test_http, rust_http_service
from zerver.lib.timestamp import timestamp_to_datetime
from zerver.models import Attachment, Message, Recipient, ScheduledMessage, UserMessage, UserProfile
from zerver.models.clients import get_client
from zerver.models.recipients import get_direct_message_group, get_or_create_direct_message_group
from zerver.models.streams import get_stream
from zerver.models.users import get_system_bot

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse


class ScheduledMessageReadHTTPTest(ZulipTransactionTestCase, LiveServerTestCase):
    def test_scheduled_messages_and_reminders_parity_with_rust(self) -> None:
        user = self.example_user("hamlet")
        other = self.example_user("othello")
        third = self.example_user("ZOE")
        stream = get_stream("Denmark", user.realm)
        channel_recipient = stream.recipient
        assert channel_recipient is not None
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None
        token = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
        api_headers = {"Authorization": f"Basic {token}"}

        with TemporaryDirectory() as directory, ExitStack() as cleanup:

            def group_recipient(ids: list[int]) -> Recipient:
                group = get_direct_message_group(ids)
                if group is None:
                    group = get_or_create_direct_message_group(ids)
                    assert group.recipient is not None
                    cleanup.callback(group.recipient.delete)
                    cleanup.callback(group.delete)
                assert group.recipient is not None
                return group.recipient

            group = group_recipient([user.id, other.id, third.id])
            dm_to_self = group_recipient([user.id])
            client = get_client("website")

            def create(
                sender: UserProfile,
                recipient: Recipient,
                timestamp: float,
                *,
                failed: bool = False,
                delivered: bool = False,
                reminder_target: int | None = None,
            ) -> ScheduledMessage:
                is_stream = recipient == channel_recipient
                message = ScheduledMessage(
                    realm=sender.realm,
                    sender=sender,
                    recipient=recipient,
                    stream=stream if is_stream else None,
                    content="متن **پیام**",
                    rendered_content="<p>متن <strong>پیام</strong></p>",
                    sending_client=client,
                    scheduled_timestamp=timestamp_to_datetime(timestamp),
                    read_by_sender=True,
                    failed=failed,
                    delivered=delivered,
                    reminder_target_message_id=reminder_target,
                    delivery_type=(
                        ScheduledMessage.SEND_LATER
                        if reminder_target is None
                        else ScheduledMessage.REMIND
                    ),
                )
                message.set_topic_name("سلام" if is_stream else Message.DM_TOPIC)
                message.save()
                cleanup.callback(message.delete)
                return message

            direct = create(user, group, 2, failed=True)
            channel = create(user, channel_recipient, -1.5)
            self_dm = create(user, dm_to_self, 3)
            delivered = create(user, group, 1, delivered=True)
            other_message = create(other, group, 1)
            reminder = create(user, dm_to_self, 1, failed=True, reminder_target=123)
            delivered_reminder = create(user, dm_to_self, 2, delivered=True, reminder_target=456)

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
            web_headers = {"Cookie": f"{settings.SESSION_COOKIE_NAME}={session.session_key}"}
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

                def check(method: str, path: str, headers: dict[str, str], status: int) -> bytes:
                    django = request_test_http(django_port, method, user.realm.host, headers, path)
                    self.assertEqual(django[0], status, django[1])
                    if rust_port is not None:
                        rust = request_test_http(rust_port, method, user.realm.host, headers, path)
                        self.assertEqual((rust[0], rust[2]), (django[0], django[2]))
                        if method == "GET":
                            self.assertEqual(orjson.loads(rust[1]), orjson.loads(django[1]))
                        else:
                            self.assertEqual(rust[1], django[1])
                    return django[1]

                for prefix, headers in (("/api/v1", api_headers), ("/json", web_headers)):
                    with self.subTest(prefix=prefix):
                        query = "?unknown=1&client=ZulipMobile"
                        messages_path = f"{prefix}/scheduled_messages{query}"
                        reminders_path = f"{prefix}/reminders{query}"
                        messages = orjson.loads(check("GET", messages_path, headers, 200))
                        self.assertEqual(messages["ignored_parameters_unsupported"], ["unknown"])
                        self.assertEqual(
                            messages["scheduled_messages"][:3],
                            [channel.to_dict(), direct.to_dict(), self_dm.to_dict()],
                        )
                        ids = {
                            item["scheduled_message_id"] for item in messages["scheduled_messages"]
                        }
                        self.assertTrue(
                            ids.isdisjoint({delivered.id, other_message.id, reminder.id})
                        )
                        reminders = orjson.loads(check("GET", reminders_path, headers, 200))
                        self.assertEqual(reminders["ignored_parameters_unsupported"], ["unknown"])
                        self.assertEqual(reminders["reminders"][0], reminder.to_reminder_dict())
                        ids = {item["reminder_id"] for item in reminders["reminders"]}
                        self.assertTrue(ids.isdisjoint({delivered_reminder.id, self_dm.id}))
                        for path in (messages_path, reminders_path):
                            self.assertEqual(check("HEAD", path, headers, 200), b"")
                            check("GET", path, {}, 401)


class ScheduledMessageTest(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        # populate_db schedules messages for a year after the test
        # database was built, which may already be due.
        ScheduledMessage.objects.all().delete()

    def last_scheduled_message(self) -> ScheduledMessage:
        return ScheduledMessage.objects.all().order_by("-id")[0]

    def get_scheduled_message(self, id: str) -> ScheduledMessage:
        return ScheduledMessage.objects.get(id=id)

    def do_schedule_message(
        self,
        msg_type: str,
        to: int | list[str] | list[int],
        msg: str,
        scheduled_delivery_timestamp: int,
    ) -> "TestHttpResponse":
        self.login("hamlet")

        topic_name = ""
        if msg_type in ["stream", "channel"]:
            topic_name = "Test topic"

        payload = {
            "type": msg_type,
            "to": orjson.dumps(to).decode(),
            "content": msg,
            "topic": topic_name,
            "scheduled_delivery_timestamp": scheduled_delivery_timestamp,
        }

        result = self.client_post("/json/scheduled_messages", payload)
        return result

    def test_schedule_message(self) -> None:
        content = "Test message"
        scheduled_delivery_timestamp = int(time.time() + 86400)
        verona_stream_id = self.get_stream_id("Verona")

        # Scheduling a message to a stream you are subscribed is successful.
        result = self.do_schedule_message(
            "channel", verona_stream_id, f"{content} 1", scheduled_delivery_timestamp
        )
        scheduled_message = self.last_scheduled_message()
        self.assert_json_success(result)
        self.assertEqual(scheduled_message.content, "Test message 1")
        self.assertEqual(scheduled_message.rendered_content, "<p>Test message 1</p>")
        self.assertEqual(scheduled_message.topic_name(), "Test topic")
        self.assertEqual(
            scheduled_message.scheduled_timestamp,
            timestamp_to_datetime(scheduled_delivery_timestamp),
        )

        # Scheduling a direct message with user IDs is successful.
        othello = self.example_user("othello")
        result = self.do_schedule_message(
            "direct", [othello.id], f"{content} 3", scheduled_delivery_timestamp
        )
        scheduled_message = self.last_scheduled_message()
        self.assert_json_success(result)
        self.assertEqual(scheduled_message.content, "Test message 3")
        self.assertEqual(scheduled_message.rendered_content, "<p>Test message 3</p>")
        self.assertEqual(
            scheduled_message.scheduled_timestamp,
            timestamp_to_datetime(scheduled_delivery_timestamp),
        )
        self.assertEqual(scheduled_message.topic_name(), Message.DM_TOPIC)

        # Cannot schedule a direct message with user emails.
        result = self.do_schedule_message(
            "direct", [othello.email], f"{content} 4", scheduled_delivery_timestamp
        )
        self.assert_json_error(result, 'to["int"] is not an integer')

    def create_scheduled_message(self) -> None:
        content = "Test message"
        scheduled_delivery_datetime = timezone_now() + timedelta(minutes=5)
        scheduled_delivery_timestamp = int(scheduled_delivery_datetime.timestamp())
        verona_stream_id = self.get_stream_id("Verona")
        result = self.do_schedule_message(
            "channel", verona_stream_id, f"{content} 1", scheduled_delivery_timestamp
        )
        self.assert_json_success(result)

    def assert_scheduled_message_delivered(
        self, scheduled_message: ScheduledMessage, recipient: Recipient | None
    ) -> None:
        sender = self.example_user("hamlet")

        # mock current time to be greater than the scheduled time, so that the `scheduled_message` can be sent.
        more_than_scheduled_delivery_datetime = scheduled_message.scheduled_timestamp + timedelta(
            minutes=1
        )

        with (
            time_machine.travel(more_than_scheduled_delivery_datetime, tick=False),
            self.assertLogs(level="INFO") as logs,
        ):
            result = try_deliver_one_scheduled_message()
            self.assertTrue(result)
            self.assertEqual(
                logs.output,
                [
                    f"INFO:root:Sending scheduled message {scheduled_message.id} with date {scheduled_message.scheduled_timestamp} (sender: {scheduled_message.sender_id})"
                ],
            )
            scheduled_message.refresh_from_db()

            assert isinstance(scheduled_message.delivered_message_id, int)
            self.assertEqual(scheduled_message.delivered, True)
            self.assertEqual(scheduled_message.failed, False)
            self.assertEqual(scheduled_message.recipient, recipient)
            self.assertEqual(scheduled_message.sender, sender)

            delivered_message = Message.objects.get(id=scheduled_message.delivered_message_id)
            self.assertEqual(delivered_message.content, scheduled_message.content)
            self.assertEqual(delivered_message.rendered_content, scheduled_message.rendered_content)
            self.assertEqual(delivered_message.topic_name(), scheduled_message.topic_name())
            self.assertEqual(delivered_message.date_sent, more_than_scheduled_delivery_datetime)
            self.assertEqual(delivered_message.recipient, scheduled_message.recipient)
            self.assertEqual(delivered_message.sender, scheduled_message.sender)

            sender_user_message = UserMessage.objects.get(
                message_id=scheduled_message.delivered_message_id, user_profile_id=sender.id
            )
            self.assertEqual(
                sender_user_message.flags.read, not is_message_to_self(delivered_message)
            )

    def test_successful_deliver_stream_scheduled_message(self) -> None:
        # No scheduled message
        result = try_deliver_one_scheduled_message()
        self.assertFalse(result)

        self.create_scheduled_message()
        scheduled_message = self.last_scheduled_message()

        stream_id = self.get_stream_id("Verona")
        recipient = Recipient.objects.get(type=Recipient.STREAM, type_id=stream_id)
        self.assert_scheduled_message_delivered(scheduled_message, recipient)

    def test_successful_deliver_direct_scheduled_message_to_other(
        self,
    ) -> None:
        # No scheduled message
        self.assertFalse(try_deliver_one_scheduled_message())

        content = "Test message"
        scheduled_delivery_datetime = timezone_now() + timedelta(minutes=5)
        scheduled_delivery_timestamp = int(scheduled_delivery_datetime.timestamp())
        sender = self.example_user("hamlet")
        othello = self.example_user("othello")
        response = self.do_schedule_message(
            "direct", [othello.id], f"{content} 3", scheduled_delivery_timestamp
        )
        self.assert_json_success(response)
        scheduled_message = self.last_scheduled_message()

        direct_message_group = get_or_create_direct_message_group(id_list=[sender.id, othello.id])
        self.assert_scheduled_message_delivered(
            scheduled_message, recipient=direct_message_group.recipient
        )

        # Check error is sent if an edit happens after the scheduled
        # message is successfully sent.
        new_delivery_datetime = timezone_now() + timedelta(minutes=7)
        new_delivery_timestamp = int(new_delivery_datetime.timestamp())
        content = "New message content"
        payload = {
            "content": content,
            "scheduled_delivery_timestamp": new_delivery_timestamp,
        }
        updated_response = self.client_patch(
            f"/json/scheduled_messages/{scheduled_message.id}", payload
        )
        self.assert_json_error(updated_response, "Scheduled message was already sent")

    def test_successful_deliver_direct_scheduled_message_to_self(
        self,
    ) -> None:
        # No scheduled message
        self.assertFalse(try_deliver_one_scheduled_message())

        content = "Test message to self"
        scheduled_delivery_datetime = timezone_now() + timedelta(minutes=5)
        scheduled_delivery_timestamp = int(scheduled_delivery_datetime.timestamp())
        sender = self.example_user("hamlet")

        # Create a direct message group for the sender.
        direct_message_group = get_or_create_direct_message_group(id_list=[sender.id])

        response = self.do_schedule_message(
            "direct", [sender.id], content, scheduled_delivery_timestamp
        )
        self.assert_json_success(response)
        scheduled_message = self.last_scheduled_message()

        self.assert_scheduled_message_delivered(
            scheduled_message, recipient=direct_message_group.recipient
        )

    def verify_deliver_scheduled_message_failure(
        self, scheduled_message: ScheduledMessage, expected_failure_message: str
    ) -> None:
        with self.assertLogs(level="INFO") as logs:
            result = try_deliver_one_scheduled_message()
        self.assertTrue(result)
        scheduled_message.refresh_from_db()
        self.assertEqual(scheduled_message.failure_message, expected_failure_message)
        self.assertEqual(
            logs.output,
            [
                f"INFO:root:Sending scheduled message {scheduled_message.id} with date {scheduled_message.scheduled_timestamp} (sender: {scheduled_message.sender_id})",
                f"INFO:root:Failed with message: {scheduled_message.failure_message}",
            ],
        )
        self.assertTrue(scheduled_message.failed)

    def test_too_late_to_deliver_scheduled_message(self) -> None:
        expected_failure_message = "Message could not be sent at the scheduled time."
        self.create_scheduled_message()
        scheduled_message = self.last_scheduled_message()

        too_late_to_send_message_datetime = scheduled_message.scheduled_timestamp + timedelta(
            minutes=SCHEDULED_MESSAGE_LATE_CUTOFF_MINUTES + 1
        )

        with time_machine.travel(too_late_to_send_message_datetime, tick=False):
            self.verify_deliver_scheduled_message_failure(
                scheduled_message, expected_failure_message
            )

        # Verify that the user was sent a message informing them about
        # the failed scheduled message.
        realm = scheduled_message.realm
        msg = most_recent_message(scheduled_message.sender)
        self.assertEqual(msg.recipient.type, msg.recipient.DIRECT_MESSAGE_GROUP)
        self.assertEqual(msg.sender_id, self.notification_bot(realm).id)
        self.assertIn(expected_failure_message, msg.content)

    def test_realm_deactivated_failed_to_deliver_scheduled_message(self) -> None:
        expected_failure_message = "This organization has been deactivated"
        self.create_scheduled_message()
        scheduled_message = self.last_scheduled_message()

        # Verify realm isn't deactivated and get user's most recent
        # message.
        self.assertFalse(scheduled_message.realm.deactivated)
        message_before_deactivation = most_recent_message(scheduled_message.sender)

        more_than_scheduled_delivery_datetime = scheduled_message.scheduled_timestamp + timedelta(
            minutes=1
        )

        with time_machine.travel(more_than_scheduled_delivery_datetime, tick=False):
            scheduled_message = self.last_scheduled_message()
            scheduled_message.realm.deactivated = True
            scheduled_message.realm.save()
            self.verify_deliver_scheduled_message_failure(
                scheduled_message, expected_failure_message
            )

        # Verify that no failed scheduled message notification was sent.
        self.assertTrue(scheduled_message.realm.deactivated)
        message_after_deactivation = most_recent_message(scheduled_message.sender)
        self.assertEqual(message_after_deactivation.content, message_before_deactivation.content)
        self.assertNotIn(expected_failure_message, message_after_deactivation.content)

    def test_sender_deactivated_failed_to_deliver_scheduled_message(self) -> None:
        expected_failure_message = "Account is deactivated"
        self.create_scheduled_message()
        scheduled_message = self.last_scheduled_message()

        # Verify user isn't deactivated and get user's most recent
        # message.
        self.assertTrue(scheduled_message.sender.is_active)
        message_before_deactivation = most_recent_message(scheduled_message.sender)

        more_than_scheduled_delivery_datetime = scheduled_message.scheduled_timestamp + timedelta(
            minutes=1
        )

        with time_machine.travel(more_than_scheduled_delivery_datetime, tick=False):
            scheduled_message = self.last_scheduled_message()
            change_user_is_active(scheduled_message.sender, False)
            self.verify_deliver_scheduled_message_failure(
                scheduled_message, expected_failure_message
            )

        # Verify that no failed scheduled message notification was sent.
        self.assertFalse(scheduled_message.sender.is_active)
        message_after_deactivation = most_recent_message(scheduled_message.sender)
        self.assertEqual(message_after_deactivation.content, message_before_deactivation.content)
        self.assertNotIn(expected_failure_message, message_after_deactivation.content)

    def test_failed_to_deliver_scheduled_message_unknown_exception(self) -> None:
        self.create_scheduled_message()
        scheduled_message = self.last_scheduled_message()

        more_than_scheduled_delivery_datetime = scheduled_message.scheduled_timestamp + timedelta(
            minutes=1
        )
        with (
            mock.patch(
                "zerver.actions.scheduled_messages.send_scheduled_message",
                side_effect=Exception(),
            ),
            time_machine.travel(more_than_scheduled_delivery_datetime, tick=False),
        ):
            scheduled_message = self.last_scheduled_message()
            with self.assertLogs(level="INFO") as logs:
                result = try_deliver_one_scheduled_message()
            self.assertTrue(result)
            scheduled_message.refresh_from_db()
            self.assert_length(logs.output, 2)
            self.assertEqual(
                logs.output[0],
                f"INFO:root:Sending scheduled message {scheduled_message.id} with date {scheduled_message.scheduled_timestamp} (sender: {scheduled_message.sender_id})",
            )
            self.assertTrue(
                logs.output[1].startswith(
                    f"ERROR:root:Unexpected error sending scheduled message {scheduled_message.id} (sent: {scheduled_message.delivered})\nTraceback (most recent call last)"
                )
            )
            self.assertTrue(scheduled_message.failed)

        # Verify that the user was sent a message informing them about
        # the failed scheduled message.
        realm = scheduled_message.realm
        msg = most_recent_message(scheduled_message.sender)
        self.assertEqual(msg.recipient.type, msg.recipient.DIRECT_MESSAGE_GROUP)
        self.assertEqual(msg.sender_id, self.notification_bot(realm).id)
        self.assertIn("Internal server error", msg.content)

    def test_failed_to_deliver_scheduled_message_from_system_bot(self) -> None:
        # Cross-realm system bots have no human reading their DMs, so
        # they should not receive a failure notification -- doing so
        # would leave a stray notification-bot <-> system-bot
        # DirectMessageGroup attached to no realm.
        self.create_scheduled_message()
        scheduled_message = self.last_scheduled_message()
        realm = scheduled_message.realm
        welcome_bot = get_system_bot(settings.WELCOME_BOT, realm.id)
        scheduled_message.sender = welcome_bot
        scheduled_message.save(update_fields=["sender"])

        more_than_scheduled_delivery_datetime = scheduled_message.scheduled_timestamp + timedelta(
            minutes=1
        )
        with (
            mock.patch(
                "zerver.actions.scheduled_messages.send_scheduled_message",
                side_effect=Exception(),
            ),
            time_machine.travel(more_than_scheduled_delivery_datetime, tick=False),
            self.assertLogs(level="INFO"),
        ):
            self.assertTrue(try_deliver_one_scheduled_message())
        scheduled_message.refresh_from_db()
        self.assertTrue(scheduled_message.failed)

        # No notification-bot -> welcome-bot message should have been
        # sent.  internal_prep_private_message lands such a message in
        # the sender's realm (the system bot realm) when the recipient
        # is a cross-realm bot, so scope the check there.
        notification_bot = self.notification_bot(realm)
        notify_group = get_or_create_direct_message_group(
            id_list=[welcome_bot.id, notification_bot.id]
        )
        self.assertFalse(
            Message.objects.filter(
                realm=notification_bot.realm, recipient=notify_group.recipient
            ).exists()
        )

    def test_editing_failed_send_scheduled_message(self) -> None:
        expected_failure_message = "Message could not be sent at the scheduled time."
        self.create_scheduled_message()
        scheduled_message = self.last_scheduled_message()

        too_late_to_send_message_datetime = scheduled_message.scheduled_timestamp + timedelta(
            minutes=SCHEDULED_MESSAGE_LATE_CUTOFF_MINUTES + 1
        )

        with time_machine.travel(too_late_to_send_message_datetime, tick=False):
            self.verify_deliver_scheduled_message_failure(
                scheduled_message, expected_failure_message
            )

            # After verifying the scheduled message failed to be sent:
            # Confirm not updating the scheduled delivery timestamp for
            # the scheduled message with that ID returns an error.
            payload_without_timestamp = {"topic": "Failed to send"}
            response = self.client_patch(
                f"/json/scheduled_messages/{scheduled_message.id}", payload_without_timestamp
            )
            self.assert_json_error(response, "Scheduled delivery time must be in the future.")

        # Editing the scheduled message with that ID for a future time is
        # successful and resets the `failed` and `failure_message` fields.
        new_delivery_datetime = timezone_now() + timedelta(minutes=60)
        new_delivery_timestamp = int(new_delivery_datetime.timestamp())
        scheduled_message_id = scheduled_message.id
        payload_with_timestamp = {
            "scheduled_delivery_timestamp": new_delivery_timestamp,
        }
        response = self.client_patch(
            f"/json/scheduled_messages/{scheduled_message.id}", payload_with_timestamp
        )
        self.assert_json_success(response)

        scheduled_message = self.last_scheduled_message()
        self.assertEqual(scheduled_message.id, scheduled_message_id)
        self.assertFalse(scheduled_message.failed)
        self.assertIsNone(scheduled_message.failure_message)

    def test_scheduling_in_past(self) -> None:
        # Scheduling a message in past should fail.
        content = "Test message"
        verona_stream_id = self.get_stream_id("Verona")
        scheduled_delivery_timestamp = int(time.time() - 86400)

        result = self.do_schedule_message(
            "channel", verona_stream_id, f"{content} 1", scheduled_delivery_timestamp
        )
        self.assert_json_error(result, "Scheduled delivery time must be in the future.")

    def test_edit_schedule_message(self) -> None:
        content = "Original test message"
        scheduled_delivery_timestamp = int(time.time() + 86400)
        verona_stream_id = self.get_stream_id("Verona")

        # Scheduling a message to a stream you are subscribed is successful.
        result = self.do_schedule_message(
            "channel", verona_stream_id, content, scheduled_delivery_timestamp
        )
        scheduled_message = self.last_scheduled_message()
        self.assert_json_success(result)
        self.assertEqual(scheduled_message.recipient.type, Recipient.STREAM)
        self.assertEqual(scheduled_message.content, "Original test message")
        self.assertEqual(scheduled_message.topic_name(), "Test topic")
        self.assertEqual(
            scheduled_message.scheduled_timestamp,
            timestamp_to_datetime(scheduled_delivery_timestamp),
        )
        scheduled_message_id = scheduled_message.id
        payload: dict[str, Any]

        # Edit message with other stream message type ("stream") and no other changes
        # results in no changes to the scheduled message.
        payload = {
            "type": "stream",
            "to": orjson.dumps(verona_stream_id).decode(),
            "topic": "Test topic",
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message_id}", payload)
        self.assert_json_success(result)

        scheduled_message = self.get_scheduled_message(str(scheduled_message_id))
        self.assertEqual(scheduled_message.recipient.type, Recipient.STREAM)
        self.assertEqual(scheduled_message.stream_id, verona_stream_id)
        self.assertEqual(scheduled_message.content, "Original test message")
        self.assertEqual(scheduled_message.topic_name(), "Test topic")
        self.assertEqual(
            scheduled_message.scheduled_timestamp,
            timestamp_to_datetime(scheduled_delivery_timestamp),
        )

        # Sending request with only scheduled message ID returns an error
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message_id}")
        self.assert_json_error(result, "Nothing to change")

        # Trying to edit only message `type` returns an error
        payload = {
            "type": "direct",
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message_id}", payload)
        self.assert_json_error(
            result, "Recipient required when updating type of scheduled message."
        )

        # Edit message `type` with valid `to` parameter succeeds
        othello = self.example_user("othello")
        to = [othello.id]
        payload = {"type": "direct", "to": orjson.dumps(to).decode()}
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message_id}", payload)
        self.assert_json_success(result)

        scheduled_message = self.get_scheduled_message(str(scheduled_message_id))
        self.assertNotEqual(scheduled_message.recipient.type, Recipient.STREAM)
        self.assertEqual(scheduled_message.recipient.type, Recipient.DIRECT_MESSAGE_GROUP)
        scheduled_message = self.get_scheduled_message(str(scheduled_message_id))
        self.assertEqual(scheduled_message.topic_name(), Message.DM_TOPIC)

        # Trying to edit `topic` for direct message is ignored
        payload = {
            "topic": "Direct message topic",
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message_id}", payload)
        self.assert_json_success(result)

        scheduled_message = self.get_scheduled_message(str(scheduled_message_id))
        self.assertEqual(scheduled_message.topic_name(), Message.DM_TOPIC)

        # Trying to edit `type` to stream message type without a `topic` returns an error
        payload = {
            "type": "channel",
            "to": orjson.dumps(verona_stream_id).decode(),
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message_id}", payload)
        self.assert_json_error(
            result, "Topic required when updating scheduled message type to channel."
        )

        # Edit message `type` to stream with valid `to` and `topic` succeeds
        payload = {
            "type": "channel",
            "to": orjson.dumps(verona_stream_id).decode(),
            "topic": "New test topic",
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message_id}", payload)
        self.assert_json_success(result)

        scheduled_message = self.get_scheduled_message(str(scheduled_message_id))
        self.assertEqual(scheduled_message.recipient.type, Recipient.STREAM)
        self.assertEqual(scheduled_message.topic_name(), "New test topic")

        # Trying to edit with timestamp in the past returns an error
        new_scheduled_delivery_timestamp = int(time.time() - 86400)
        payload = {
            "scheduled_delivery_timestamp": new_scheduled_delivery_timestamp,
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message_id}", payload)
        self.assert_json_error(result, "Scheduled delivery time must be in the future.")

        # Edit content and time of scheduled message succeeds
        edited_content = "Edited test message"
        new_scheduled_delivery_timestamp = scheduled_delivery_timestamp + int(
            time.time() + (3 * 86400)
        )
        payload = {
            "content": edited_content,
            "scheduled_delivery_timestamp": new_scheduled_delivery_timestamp,
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message_id}", payload)
        self.assert_json_success(result)

        scheduled_message = self.get_scheduled_message(str(scheduled_message_id))
        self.assertEqual(scheduled_message.content, edited_content)
        self.assertEqual(scheduled_message.topic_name(), "New test topic")
        self.assertEqual(
            scheduled_message.scheduled_timestamp,
            timestamp_to_datetime(new_scheduled_delivery_timestamp),
        )

        # Edit topic and content of scheduled stream message
        edited_content = "Final content edit for test"
        payload = {
            "topic": "Another topic for test",
            "content": edited_content,
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message.id}", payload)
        self.assert_json_success(result)

        scheduled_message = self.get_scheduled_message(str(scheduled_message.id))
        self.assertEqual(scheduled_message.content, edited_content)
        self.assertEqual(scheduled_message.topic_name(), "Another topic for test")

        # Edit only topic of scheduled stream message
        payload = {
            "topic": "Final topic for test",
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message.id}", payload)
        self.assert_json_success(result)

        scheduled_message = self.get_scheduled_message(str(scheduled_message.id))
        self.assertEqual(scheduled_message.recipient.type, Recipient.STREAM)
        self.assertEqual(scheduled_message.content, edited_content)
        self.assertEqual(scheduled_message.topic_name(), "Final topic for test")
        self.assertEqual(
            scheduled_message.scheduled_timestamp,
            timestamp_to_datetime(new_scheduled_delivery_timestamp),
        )

    def test_fetch_scheduled_messages(self) -> None:
        self.login("hamlet")
        # No scheduled message
        result = self.client_get("/json/scheduled_messages")
        self.assert_json_success(result)
        self.assert_length(orjson.loads(result.content)["scheduled_messages"], 0)

        verona_stream_id = self.get_stream_id("Verona")
        content = "Test message"
        scheduled_delivery_timestamp = int(time.time() + 86400)
        self.do_schedule_message("channel", verona_stream_id, content, scheduled_delivery_timestamp)

        # Single scheduled message
        result = self.client_get("/json/scheduled_messages")
        self.assert_json_success(result)
        scheduled_messages = orjson.loads(result.content)["scheduled_messages"]

        self.assert_length(scheduled_messages, 1)
        self.assertEqual(
            scheduled_messages[0]["scheduled_message_id"], self.last_scheduled_message().id
        )
        self.assertEqual(scheduled_messages[0]["content"], content)
        self.assertEqual(scheduled_messages[0]["to"], verona_stream_id)
        self.assertEqual(scheduled_messages[0]["type"], "stream")
        self.assertEqual(scheduled_messages[0]["topic"], "Test topic")
        self.assertEqual(
            scheduled_messages[0]["scheduled_delivery_timestamp"], scheduled_delivery_timestamp
        )

        othello = self.example_user("othello")
        result = self.do_schedule_message(
            "direct", [othello.id], f"{content} 3", scheduled_delivery_timestamp
        )

        # Multiple scheduled messages
        result = self.client_get("/json/scheduled_messages")
        self.assert_json_success(result)
        self.assert_length(orjson.loads(result.content)["scheduled_messages"], 2)

        # Check if another user can access these scheduled messages.
        self.logout()
        self.login("othello")
        result = self.client_get("/json/scheduled_messages")
        self.assert_json_success(result)
        self.assert_length(orjson.loads(result.content)["scheduled_messages"], 0)

    def test_delete_scheduled_messages(self) -> None:
        self.login("hamlet")

        content = "Test message"
        verona_stream_id = self.get_stream_id("Verona")
        scheduled_delivery_timestamp = int(time.time() + 86400)

        self.do_schedule_message("channel", verona_stream_id, content, scheduled_delivery_timestamp)
        scheduled_message = self.last_scheduled_message()
        self.logout()

        # Other user cannot delete it.
        othello = self.example_user("othello")
        result = self.api_delete(othello, f"/api/v1/scheduled_messages/{scheduled_message.id}")
        self.assert_json_error(result, "Scheduled message does not exist", 404)

        self.login("hamlet")
        result = self.client_delete(f"/json/scheduled_messages/{scheduled_message.id}")
        self.assert_json_success(result)

        # Already deleted.
        result = self.client_delete(f"/json/scheduled_messages/{scheduled_message.id}")
        self.assert_json_error(result, "Scheduled message does not exist", 404)

    def test_attachment_handling(self) -> None:
        self.login("hamlet")
        hamlet = self.example_user("hamlet")
        verona_stream_id = self.get_stream_id("Verona")

        attachment_file1 = StringIO("zulip!")
        attachment_file1.name = "dummy_1.txt"
        result = self.client_post("/json/user_uploads", {"file": attachment_file1})
        path_id1 = re.sub(r"/user_uploads/", "", result.json()["url"])
        attachment_object1 = Attachment.objects.get(path_id=path_id1)

        attachment_file2 = StringIO("zulip!")
        attachment_file2.name = "dummy_1.txt"
        result = self.client_post("/json/user_uploads", {"file": attachment_file2})
        path_id2 = re.sub(r"/user_uploads/", "", result.json()["url"])
        attachment_object2 = Attachment.objects.get(path_id=path_id2)

        content = f"Test [zulip.txt](http://{hamlet.realm.host}/user_uploads/{path_id1})"
        scheduled_delivery_timestamp = int(time.time() + 86400)

        # Test sending with attachment
        self.do_schedule_message("channel", verona_stream_id, content, scheduled_delivery_timestamp)
        scheduled_message = self.last_scheduled_message()
        self.assertEqual(
            list(attachment_object1.scheduled_messages.all().values_list("id", flat=True)),
            [scheduled_message.id],
        )
        self.assertEqual(scheduled_message.has_attachment, True)

        # Test editing to change attachmment
        edited_content = f"Test [zulip.txt](http://{hamlet.realm.host}/user_uploads/{path_id2})"
        payload = {
            "content": edited_content,
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message.id}", payload)

        scheduled_message = self.get_scheduled_message(str(scheduled_message.id))
        self.assertEqual(
            list(attachment_object1.scheduled_messages.all().values_list("id", flat=True)), []
        )
        self.assertEqual(
            list(attachment_object2.scheduled_messages.all().values_list("id", flat=True)),
            [scheduled_message.id],
        )
        self.assertEqual(scheduled_message.has_attachment, True)

        # Test editing to no longer reference any attachments
        edited_content = "No more attachments"
        payload = {
            "content": edited_content,
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message.id}", payload)

        scheduled_message = self.get_scheduled_message(str(scheduled_message.id))
        self.assertEqual(
            list(attachment_object1.scheduled_messages.all().values_list("id", flat=True)), []
        )
        self.assertEqual(
            list(attachment_object2.scheduled_messages.all().values_list("id", flat=True)), []
        )
        self.assertEqual(scheduled_message.has_attachment, False)

        # Test editing to now have an attachment again
        edited_content = (
            f"Attachment is back! [zulip.txt](http://{hamlet.realm.host}/user_uploads/{path_id2})"
        )
        payload = {
            "content": edited_content,
        }
        result = self.client_patch(f"/json/scheduled_messages/{scheduled_message.id}", payload)

        scheduled_message = self.get_scheduled_message(str(scheduled_message.id))
        self.assertEqual(
            list(attachment_object1.scheduled_messages.all().values_list("id", flat=True)), []
        )
        self.assertEqual(
            list(attachment_object2.scheduled_messages.all().values_list("id", flat=True)),
            [scheduled_message.id],
        )
        self.assertEqual(scheduled_message.has_attachment, True)


@skipUnless(os.environ.get("ZULIP_RUST_ACTIVITY_TEST_VHOST"), "RabbitMQ test vhost is unavailable")
class ScheduledJobDeleteHTTPTest(ZulipTransactionTestCase, LiveServerTestCase):
    def test_reminder_delete_parity(self) -> None:
        self.check_delete_parity(
            endpoint="reminders",
            id_field="reminder_id",
            target_type=ScheduledMessage.REMIND,
            other_type=ScheduledMessage.SEND_LATER,
        )

    def test_scheduled_message_delete_parity(self) -> None:
        self.check_delete_parity(
            endpoint="scheduled_messages",
            id_field="scheduled_message_id",
            target_type=ScheduledMessage.SEND_LATER,
            other_type=ScheduledMessage.REMIND,
        )

    def check_delete_parity(
        self, *, endpoint: str, id_field: str, target_type: int, other_type: int
    ) -> None:
        vhost = os.environ["ZULIP_RUST_ACTIVITY_TEST_VHOST"]
        assert vhost.startswith("zulip-rust-test-")
        password = settings.RABBITMQ_PASSWORD
        assert password is not None
        amqp_url = (
            f"amqp://{quote(settings.RABBITMQ_USERNAME, safe='')}:{quote(password, safe='')}"
            f"@{settings.RABBITMQ_HOST}:{settings.RABBITMQ_PORT}/{quote(vhost, safe='')}"
        )
        owner = self.example_user("hamlet")
        other = self.example_user("othello")
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None

        def basic(user: UserProfile) -> dict[str, str]:
            return {
                "Authorization": "Basic "
                + b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
            }

        session = SessionStore()
        session.update(
            {
                "_auth_user_id": str(owner.id),
                "_auth_user_backend": "zproject.backends.DevAuthBackend",
                "_auth_user_hash": owner.get_session_auth_hash(),
            }
        )
        session.create()
        token = "a" * 32
        cookie = (
            f"{settings.SESSION_COOKIE_NAME}={session.session_key}; "
            f"{settings.CSRF_COOKIE_NAME}={token}"
        )
        attachment = Attachment.objects.create(
            owner=owner,
            realm=owner.realm,
            file_name="scheduled-job.txt",
            path_id="scheduled-job-parity",
            size=8,
        )
        scheduled_job = ScheduledMessage.objects.filter(
            sender__realm=owner.realm, delivery_type=ScheduledMessage.SEND_LATER
        ).first()
        assert scheduled_job is not None
        fields = dict(
            sender=owner,
            recipient=scheduled_job.recipient,
            realm=owner.realm,
            content="Please remind me",
            rendered_content="<p>Please remind me</p>",
            sending_client=get_client("website"),
            scheduled_timestamp=timezone_now() + timedelta(days=1),
            read_by_sender=False,
        )
        victim_id = 2**31 + 1
        scheduled = ScheduledMessage.objects.create(delivery_type=other_type, **fields)
        attachment.scheduled_messages.add(scheduled)
        try:
            with (
                self.settings(
                    USING_RABBITMQ=True,
                    RABBITMQ_VHOST=vhost,
                    TORNADO_PROCESSES=1,
                    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
                ),
                pika.BlockingConnection(pika.URLParameters(amqp_url)) as broker,
                TemporaryDirectory() as directory,
            ):
                channel = broker.channel()
                for name in ("notify_tornado", "user_activity"):
                    channel.queue_declare(name, durable=True)
                    channel.queue_purge(name)
                secret = Path(directory) / "secret"
                secret.write_text(settings.SECRET_KEY)
                environment = {
                    (
                        "ZULIP_RUST_REMINDER_DELETES"
                        if target_type == ScheduledMessage.REMIND
                        else "ZULIP_RUST_SCHEDULED_MESSAGE_DELETES"
                    ): "1",
                    "ZULIP_RUST_AMQP_URL": amqp_url,
                    "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
                    "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                        settings.AUTHENTICATION_BACKENDS
                    ).decode(),
                }

                def create_victim() -> None:
                    job = ScheduledMessage.objects.create(
                        id=victim_id,
                        delivery_type=target_type,
                        failed=True,
                        delivered=target_type == ScheduledMessage.SEND_LATER,
                        has_attachment=True,
                        **fields,
                    )
                    attachment.scheduled_messages.add(job)

                event = {"type": endpoint, "op": "remove", id_field: victim_id}

                def check(
                    port: int,
                    prefix: str,
                    auth: dict[str, str],
                    csrf_failure: dict[str, str] | None = None,
                ) -> list[tuple[int, bytes]]:
                    create_victim()
                    cases = [
                        (scheduled.id, auth, 404, ""),
                        (2**63, auth, 404, ""),
                    ]
                    if prefix.startswith("/api/"):
                        cases.append((victim_id, basic(other), 404, ""))
                    if csrf_failure is not None:
                        cases.append((victim_id, csrf_failure, 403, ""))
                    cases += [
                        (victim_id, auth, 200, f"?unused=yes&{id_field}={scheduled.id}"),
                        (victim_id, auth, 404, ""),
                    ]
                    outcomes = []
                    pending = True
                    for job_id, headers, expected, query in cases:
                        status, body, _, _ = request_test_http(
                            port, "DELETE", owner.realm.host, headers, f"{prefix}/{job_id}{query}"
                        )
                        self.assertEqual(status, expected, body)
                        outcomes.append((status, body))
                        _, _, notification = channel.basic_get("notify_tornado", auto_ack=True)
                        if status == 200:
                            pending = False
                            assert notification is not None
                            self.assertEqual(
                                orjson.loads(notification), {"event": event, "users": [owner.id]}
                            )
                            self.assertEqual(
                                orjson.loads(body)["ignored_parameters_unsupported"],
                                [id_field, "unused"],
                            )
                            self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[2])
                        else:
                            self.assertIsNone(notification)
                        self.assertEqual(
                            ScheduledMessage.objects.filter(id=victim_id).exists(), pending
                        )
                        self.assertTrue(Attachment.objects.filter(id=attachment.id).exists())
                        self.assertCountEqual(
                            attachment.scheduled_messages.values_list("id", flat=True),
                            [scheduled.id, victim_id] if pending else [scheduled.id],
                        )
                    return outcomes

                for scheme in ("http", "https"):
                    web_auth = {"Cookie": cookie, "X-CSRFToken": token}
                    if scheme == "https":
                        web_auth.update(
                            {
                                "X-Forwarded-Proto": "https",
                                "Referer": f"https://{owner.realm.host}/",
                            }
                        )
                    else:
                        web_auth["Origin"] = f"http://{owner.realm.host}"
                    no_token = {
                        key: value for key, value in web_auth.items() if key != "X-CSRFToken"
                    }
                    routes: list[tuple[str, dict[str, str], dict[str, str] | None]] = [
                        (f"/json/{endpoint}", web_auth, no_token)
                    ]
                    if scheme == "http":
                        routes.append((f"/api/v1/{endpoint}", basic(owner), None))
                    service = (
                        rust_http_service({**environment, "ZULIP_RUST_WEB_POST_SCHEME": scheme})
                        if os.environ.get("ZULIP_RUST_HTTP_BIN")
                        else nullcontext(None)
                    )
                    with service as rust_port:
                        for prefix, auth, csrf_failure in routes:
                            with self.subTest(scheme=scheme, path=prefix):
                                baseline = check(django_port, prefix, auth, csrf_failure)
                                if rust_port is not None:
                                    self.assertEqual(
                                        check(rust_port, prefix, auth, csrf_failure), baseline
                                    )
                        if rust_port is not None and scheme == "http":
                            create_victim()
                            with (
                                rust_http_service(environment) as second_port,
                                ThreadPoolExecutor(max_workers=2) as requests,
                            ):
                                pending = [
                                    requests.submit(
                                        request_test_http,
                                        port,
                                        "DELETE",
                                        owner.realm.host,
                                        basic(owner),
                                        f"/api/v1/{endpoint}/{victim_id}",
                                    )
                                    for port in (rust_port, second_port)
                                ]
                                self.assertCountEqual(
                                    [request.result()[0] for request in pending], [200, 404]
                                )
                            self.assertFalse(ScheduledMessage.objects.filter(id=victim_id).exists())
                            self.assertEqual(
                                list(attachment.scheduled_messages.values_list("id", flat=True)),
                                [scheduled.id],
                            )
                            notification = channel.basic_get("notify_tornado", auto_ack=True)[2]
                            assert notification is not None
                            self.assertEqual(
                                orjson.loads(notification), {"event": event, "users": [owner.id]}
                            )
                            self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[2])
        finally:
            attachment.delete()
            ScheduledMessage.objects.filter(id__in=[victim_id, scheduled.id]).delete()
            session.delete()

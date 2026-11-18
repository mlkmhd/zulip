import os
import time
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import quote, urlsplit

import orjson
import pika
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import LiveServerTestCase

from zerver.actions.realm_settings import do_change_realm_permission_group_setting
from zerver.actions.user_groups import check_add_user_group
from zerver.actions.users import do_deactivate_user
from zerver.lib.drafts import (
    DraftData,
    ValidatedDraftData,
    do_create_drafts,
    do_edit_draft,
    further_validated_draft_dict,
)
from zerver.lib.test_classes import ZulipTestCase, ZulipTransactionTestCase
from zerver.lib.test_rust_http import request_test_http, rust_http_service
from zerver.lib.users import check_can_access_user
from zerver.models import (
    DirectMessageGroup,
    Draft,
    GroupGroupMembership,
    NamedUserGroup,
    Realm,
    Recipient,
    Stream,
    Subscription,
    UserProfile,
)
from zerver.models.constants import MAX_TOPIC_NAME_LENGTH
from zerver.models.groups import SystemGroups
from zerver.models.realms import get_realm
from zerver.models.recipients import get_direct_message_group, get_or_create_direct_message_group

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse


class DraftCreationTests(ZulipTestCase):
    def create_and_check_drafts_for_success(
        self,
        draft_dicts: list[dict[str, Any]],
        expected_draft_dicts: list[dict[str, Any]] | None = None,
    ) -> None:
        hamlet = self.example_user("hamlet")

        # Now send a POST request to the API endpoint.
        payload = {"drafts": orjson.dumps(draft_dicts).decode()}
        resp = self.api_post(hamlet, "/api/v1/drafts", payload)
        self.assert_json_success(resp)

        # Finally check to make sure that the drafts were actually created properly.
        new_draft_dicts = []
        for draft in Draft.objects.filter(user_profile=hamlet).order_by("last_edit_time"):
            draft_dict = draft.to_dict()
            draft_dict.pop("id")
            new_draft_dicts.append(draft_dict)
        if expected_draft_dicts is None:
            expected_draft_dicts = draft_dicts
        self.assertEqual(new_draft_dicts, expected_draft_dicts)

    def create_and_check_drafts_for_error(
        self, draft_dicts: list[dict[str, Any]], expected_message: str
    ) -> None:
        hamlet = self.example_user("hamlet")

        initial_count = Draft.objects.count()

        # Now send a POST request to the API endpoint.
        payload = {"drafts": orjson.dumps(draft_dicts).decode()}
        resp = self.api_post(hamlet, "/api/v1/drafts", payload)
        self.assert_json_error(resp, expected_message)

        # Make sure that there are no drafts in the database at the
        # end of the test. Drafts should never be created in error
        # conditions.
        self.assertEqual(Draft.objects.count(), initial_count)

    def test_require_enable_drafts_synchronization(self) -> None:
        hamlet = self.example_user("hamlet")
        hamlet.enable_drafts_synchronization = False
        hamlet.save()
        payload = {"drafts": "[]"}
        resp = self.api_post(hamlet, "/api/v1/drafts", payload)
        self.assert_json_error(resp, "User has disabled synchronizing drafts.")

    def test_create_one_stream_draft_properly(self) -> None:
        hamlet = self.example_user("hamlet")
        visible_stream_name = self.get_streams(hamlet)[0]
        visible_stream_id = self.get_stream_id(visible_stream_name)
        draft_dicts = [
            {
                "type": "stream",
                "to": [visible_stream_id],
                "topic": "sync drafts",
                "content": "Let's add backend support for syncing drafts.",
                "timestamp": 1595479019,
            }
        ]
        self.create_and_check_drafts_for_success(draft_dicts)

    def test_create_one_personal_message_draft_properly(self) -> None:
        zoe = self.example_user("ZOE")
        draft_dicts = [
            {
                "type": "private",
                "to": [zoe.id],
                "topic": "This topic should be ignored.",
                "content": "What if we made it possible to sync drafts in Zulip?",
                "timestamp": 1595479019,
            }
        ]
        # For direct messages, the topic should be ignored.
        expected_draft_dicts = [
            {
                "type": "private",
                "to": [zoe.id],
                "topic": "",
                "content": "What if we made it possible to sync drafts in Zulip?",
                "timestamp": 1595479019,
            }
        ]
        self.create_and_check_drafts_for_success(draft_dicts, expected_draft_dicts)

    def test_create_one_group_personal_message_draft_properly(self) -> None:
        zoe = self.example_user("ZOE")
        othello = self.example_user("othello")
        draft_dicts = [
            {
                "type": "private",
                "to": [zoe.id, othello.id],
                "topic": "This topic should be ignored.",
                "content": "What if we made it possible to sync drafts in Zulip?",
                "timestamp": 1595479019,
            }
        ]
        # For direct messages, the topic should be ignored.
        expected_draft_dicts = [
            {
                "type": "private",
                "to": [zoe.id, othello.id],
                "topic": "",
                "content": "What if we made it possible to sync drafts in Zulip?",
                "timestamp": 1595479019,
            }
        ]
        self.create_and_check_drafts_for_success(draft_dicts, expected_draft_dicts)

    def test_create_draft_reuses_existing_direct_message_group(self) -> None:
        owner = self.example_user("hamlet")
        othello = self.example_user("othello")
        zoe = self.example_user("ZOE")
        group = get_or_create_direct_message_group([zoe.id, owner.id, othello.id])
        draft = DraftData(type="private", to=[othello.id, zoe.id], topic="", content="Unsent")

        (created,) = do_create_drafts([draft], owner)

        assert group.recipient_id is not None
        self.assertEqual(created.recipient_id, group.recipient_id)
        members = Subscription.objects.filter(recipient_id=group.recipient_id)
        self.assertCountEqual(
            members.values_list("user_profile_id", flat=True), [owner.id, othello.id, zoe.id]
        )

    def test_create_batch_of_drafts_properly(self) -> None:
        hamlet = self.example_user("hamlet")
        visible_stream_name = self.get_streams(hamlet)[0]
        visible_stream_id = self.get_stream_id(visible_stream_name)
        zoe = self.example_user("ZOE")
        othello = self.example_user("othello")
        draft_dicts = [
            {
                "type": "stream",
                "to": [visible_stream_id],
                "topic": "sync drafts",
                "content": "Let's add backend support for syncing drafts.",
                "timestamp": 1595479019,
            },
            {
                "type": "private",
                "to": [zoe.id],
                "topic": "",
                "content": "What if we made it possible to sync drafts in Zulip?",
                "timestamp": 1595479020,
            },
            {
                "type": "private",
                "to": [zoe.id, othello.id],
                "topic": "",
                "content": "What if we made it possible to sync drafts in Zulip?",
                "timestamp": 1595479021,
            },
        ]
        self.create_and_check_drafts_for_success(draft_dicts)

    def test_create_batch_leaves_direct_message_group_after_later_error(self) -> None:
        owner = self.example_user("hamlet")
        othello = self.example_user("othello")
        zoe = self.example_user("ZOE")
        members = [owner.id, othello.id, zoe.id]
        self.assertIsNone(get_direct_message_group(members))
        direct_draft = {
            "type": "private",
            "to": [zoe.id, othello.id, zoe.id],
            "topic": "Unused",
            "content": "A new group conversation",
            "timestamp": 1595479019,
        }
        invalid_draft = {**direct_draft, "type": "stream", "to": [], "timestamp": -1}

        with self.capture_send_event_calls(expected_num_events=0):
            self.create_and_check_drafts_for_error(
                [invalid_draft, direct_draft], "Timestamp must not be negative."
            )
            self.assertIsNone(get_direct_message_group(members))
            self.create_and_check_drafts_for_error(
                [direct_draft, invalid_draft], "Timestamp must not be negative."
            )

        group = get_direct_message_group(members)
        assert group is not None
        recipient = group.recipient
        assert recipient is not None
        self.assertEqual(recipient.type, Recipient.DIRECT_MESSAGE_GROUP)
        self.assertEqual(recipient.type_id, group.id)
        self.assertCountEqual(
            Subscription.objects.filter(recipient=recipient).values_list(
                "user_profile_id", "active", "is_user_active", "color"
            ),
            [(user_id, True, True, Subscription.DEFAULT_STREAM_COLOR) for user_id in members],
        )

        self.create_and_check_drafts_for_success(
            [direct_draft], [{**direct_draft, "to": sorted([othello.id, zoe.id]), "topic": ""}]
        )
        self.assertEqual(Draft.objects.get(user_profile=owner).recipient_id, recipient.id)

    def test_create_batch_validates_all_draft_fields_before_creating_groups(self) -> None:
        owner = self.example_user("hamlet")
        members = [owner.id, self.example_user("othello").id, self.example_user("ZOE").id]
        self.assertIsNone(get_direct_message_group(members))
        draft = {"type": "private", "to": members[1:], "topic": "", "content": "Unsent"}
        with self.capture_send_event_calls(expected_num_events=0):
            response = self.api_post(
                owner,
                "/api/v1/drafts",
                {"drafts": orjson.dumps([draft, {**draft, "to": ["2"]}]).decode()},
                intentionally_undocumented=True,
            )
            self.assert_json_error(response, 'drafts[1]["to"][0] is not an integer')

        self.assertIsNone(get_direct_message_group(members))
        self.assertFalse(Draft.objects.filter(user_profile=owner).exists())

    def test_create_draft_with_duplicate_json_key(self) -> None:
        owner = self.example_user("hamlet")
        body = '[{"type":"","to":[],"topic":"","content":"first","content":"last","timestamp":42}]'
        with self.capture_send_event_calls(expected_num_events=1):
            response = self.api_post(owner, "/api/v1/drafts", {"drafts": body})
            self.assert_json_success(response)
        self.assertEqual(Draft.objects.get(user_profile=owner).content, "last")

    def test_create_draft_rolls_back_incomplete_direct_message_group(self) -> None:
        owner = self.example_user("hamlet")
        recipient = self.example_user("othello")
        members = [owner.id, recipient.id]
        self.assertIsNone(get_direct_message_group(members))
        recipient_count = Recipient.objects.count()
        draft = DraftData(type="private", to=[recipient.id], topic="", content="An unsaved draft")

        with (
            self.capture_send_event_calls(expected_num_events=0),
            self.assertRaisesRegex(IntegrityError, "reject_draft_group_subscription"),
            transaction.atomic(durable=True),
        ):
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE zulip.zerver_subscription ADD CONSTRAINT "
                    "reject_draft_group_subscription CHECK (FALSE) NOT VALID"
                )
            do_create_drafts([draft], owner)

        self.assertIsNone(get_direct_message_group(members))
        self.assertEqual(Recipient.objects.count(), recipient_count)
        self.assertFalse(Draft.objects.filter(user_profile=owner).exists())

    def test_missing_timestamps(self) -> None:
        """If a timestamp is not provided for a draft dict then it should be automatically
        filled in."""
        hamlet = self.example_user("hamlet")
        visible_stream_name = self.get_streams(hamlet)[0]
        visible_stream_id = self.get_stream_id(visible_stream_name)

        draft_dicts = [
            {
                "type": "stream",
                "to": [visible_stream_id],
                "topic": "sync drafts",
                "content": "Let's add backend support for syncing drafts.",
            }
        ]

        initial_count = Draft.objects.count()

        current_time = int(time.time())
        payload = {"drafts": orjson.dumps(draft_dicts).decode()}
        resp = self.api_post(hamlet, "/api/v1/drafts", payload)
        ids = orjson.loads(resp.content)["ids"]
        self.assert_json_success(resp)

        new_drafts = Draft.objects.filter(id__gte=ids[0])
        self.assertEqual(Draft.objects.count() - initial_count, 1)
        new_draft = new_drafts[0].to_dict()
        self.assertTrue(isinstance(new_draft["timestamp"], int))
        # Since it would be too tricky to get the same times, perform
        # a relative check.
        self.assertTrue(new_draft["timestamp"] >= current_time)

    def test_create_drafts_preserves_request_order_in_response_and_event(self) -> None:
        owner = self.example_user("hamlet")
        recipient = self.example_user("othello")
        drafts = [
            {
                "type": "private",
                "to": [recipient.id],
                "topic": "",
                "content": "First submitted 📝",
                "timestamp": 1595479021,
            },
            {
                "type": "",
                "to": [],
                "topic": "",
                "content": "Second submitted",
                "timestamp": 1595479019,
            },
        ]
        with self.capture_send_event_calls(expected_num_events=1) as events:
            response = self.api_post(
                owner, "/api/v1/drafts", {"drafts": orjson.dumps(drafts).decode()}
            )
            ids = self.assert_json_success(response)["ids"]

        first, second = ids
        persisted = Draft.objects.in_bulk(ids)
        self.assertEqual(
            [persisted[first].user_profile_id, persisted[second].user_profile_id],
            [owner.id, owner.id],
        )
        expected = [{**draft, "id": draft_id} for draft, draft_id in zip(drafts, ids, strict=True)]
        self.assertEqual([persisted[draft_id].to_dict() for draft_id in ids], expected)
        self.assertEqual(
            events,
            [{"event": {"type": "drafts", "op": "add", "drafts": expected}, "users": [owner.id]}],
        )

    def test_create_empty_batch(self) -> None:
        owner = self.example_user("hamlet")
        count = Draft.objects.count()
        with self.capture_send_event_calls(expected_num_events=1) as events:
            response = self.api_post(owner, "/api/v1/drafts", {"drafts": "[]"})
            self.assertEqual(self.assert_json_success(response)["ids"], [])
        self.assertEqual(Draft.objects.count(), count)
        self.assertEqual(
            events, [{"event": {"type": "drafts", "op": "add", "drafts": []}, "users": [owner.id]}]
        )

    def test_create_drafts_rolls_back_if_event_serialization_fails(self) -> None:
        owner = self.example_user("hamlet")
        content = "Temporary draft"
        draft = DraftData(type="", to=[], topic="", content=content, timestamp=1595479021)
        with (
            self.capture_send_event_calls(expected_num_events=0),
            patch.object(Draft, "to_dict", side_effect=ValueError("Unable to serialize draft")),
            self.assertRaisesRegex(ValueError, "Unable to serialize draft"),
        ):
            do_create_drafts([draft], owner)
        self.assertFalse(Draft.objects.filter(user_profile=owner, content=content).exists())

    def test_invalid_timestamp(self) -> None:
        draft_dicts = [
            {
                "type": "stream",
                "to": [],
                "topic": "sync drafts",
                "content": "Let's add backend support for syncing drafts.",
                "timestamp": -10.10,
            }
        ]
        self.create_and_check_drafts_for_error(draft_dicts, "Timestamp must not be negative.")

    def test_create_drafts_preserves_timestamp_precision(self) -> None:
        owner = self.example_user("hamlet")
        timestamps = {
            -0.0000005: 0,
            0.0000015: 2,
            0.0078125: 7812,
            0.0234375: 23438,
            1.9999995: 1999999,
            1595479019.1234565: 1595479019123456,
            8589934591.999999: 8589934591999999,
            8589934592.000002: 8589934592000002,
            253402300799.99997: 253402300799999969,
        }
        drafts = [
            {"type": "", "to": [], "topic": "", "content": "Draft", "timestamp": timestamp}
            for timestamp in timestamps
        ]
        response = self.api_post(
            owner,
            "/api/v1/drafts",
            {"drafts": orjson.dumps(drafts).decode()},
            intentionally_undocumented=True,
        )
        ids = self.assert_json_success(response)["ids"]
        persisted = Draft.objects.in_bulk(ids)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(
            [persisted[draft_id].last_edit_time for draft_id in ids],
            [epoch + timedelta(microseconds=micros) for micros in timestamps.values()],
        )

    def test_create_non_stream_draft_with_no_recipient(self) -> None:
        """When "to" is an empty list, the type should become "" as well."""
        draft_dicts = [
            {
                "type": "private",
                "to": [],
                "topic": "sync drafts",
                "content": "Let's add backend support for syncing drafts.",
                "timestamp": 1595479019,
            },
            {
                "type": "",
                "to": [],
                "topic": "sync drafts",
                "content": "Let's add backend support for syncing drafts.",
                "timestamp": 1595479019,
            },
        ]
        expected_draft_dicts = [
            {
                "type": "",
                "to": [],
                "topic": "",
                "content": "Let's add backend support for syncing drafts.",
                "timestamp": 1595479019,
            },
            {
                "type": "",
                "to": [],
                "topic": "",
                "content": "Let's add backend support for syncing drafts.",
                "timestamp": 1595479019,
            },
        ]
        self.create_and_check_drafts_for_success(draft_dicts, expected_draft_dicts)

    def test_create_stream_draft_with_no_recipient(self) -> None:
        draft_dicts = [
            {
                "type": "stream",
                "to": [],
                "topic": "sync drafts",
                "content": "Let's add backend support for syncing drafts.",
                "timestamp": 15954790199,
            }
        ]
        self.create_and_check_drafts_for_error(
            draft_dicts, "Must specify exactly 1 channel ID for channel messages"
        )

    def test_create_stream_draft_for_inaccessible_stream(self) -> None:
        # When the user does not have permission to access the stream:
        stream = self.make_stream("Secret Society", invite_only=True)
        draft_dicts = [
            {
                "type": "stream",
                "to": [stream.id],  # This can't be accessed by hamlet.
                "topic": "sync drafts",
                "content": "Let's add backend support for syncing drafts.",
                "timestamp": 1595479019,
            }
        ]
        self.create_and_check_drafts_for_error(draft_dicts, "Invalid channel ID")

        # When the stream itself does not exist:
        draft_dicts = [
            {
                "type": "stream",
                "to": [99999999999999],  # Hopefully, this doesn't exist.
                "topic": "sync drafts",
                "content": "Let's add backend support for syncing drafts.",
                "timestamp": 1595479019,
            }
        ]
        self.create_and_check_drafts_for_error(draft_dicts, "Invalid channel ID")
        self.create_and_check_drafts_for_error(
            [{**draft_dicts[0], "to": [2**63]}], "Invalid channel ID"
        )

    def test_create_stream_draft_with_subgroup_access(self) -> None:
        owner = self.example_user("hamlet")
        guest = self.example_user("polonius")
        self.assertTrue(guest.is_guest)
        stream = self.make_stream("Private draft channel", invite_only=True)
        members = check_add_user_group(
            owner.realm, "Draft group members", [owner, guest], acting_user=owner
        )
        subscribers = check_add_user_group(
            owner.realm, "Draft channel subscribers", [], acting_user=owner
        )
        GroupGroupMembership.objects.create(supergroup=subscribers, subgroup=members)
        stream.can_subscribe_group = subscribers
        stream.save(update_fields=["can_subscribe_group"])
        draft = {"type": "stream", "to": [stream.id], "topic": "Drafts", "content": "Unsent"}
        params = {"drafts": orjson.dumps([draft]).decode()}

        self.assert_json_success(self.api_post(owner, "/api/v1/drafts", params))
        self.assertEqual(Draft.objects.get(user_profile=owner).recipient_id, stream.recipient_id)
        self.assert_json_error(self.api_post(guest, "/api/v1/drafts", params), "Invalid channel ID")
        self.assertFalse(Draft.objects.filter(user_profile=guest).exists())

        stream.deactivated = True
        stream.save(update_fields=["deactivated"])
        self.assert_json_error(self.api_post(owner, "/api/v1/drafts", params), "Invalid channel ID")
        self.assertEqual(Draft.objects.filter(user_profile=owner).count(), 1)

    def test_guest_can_create_draft_for_unsubscribed_web_public_channel(self) -> None:
        guest = self.example_user("polonius")
        self.assertTrue(guest.is_guest)
        public = self.make_stream("Public draft channel")
        web_public = self.make_stream("Web public draft channel", is_web_public=True)
        draft = {"type": "stream", "to": [public.id], "topic": "Drafts", "content": "Unsent"}
        response = self.api_post(
            guest, "/api/v1/drafts", {"drafts": orjson.dumps([draft]).decode()}
        )
        self.assert_json_error(response, "Invalid channel ID")
        self.assertFalse(Draft.objects.filter(user_profile=guest).exists())

        draft["to"] = [web_public.id]
        response = self.api_post(
            guest, "/api/v1/drafts", {"drafts": orjson.dumps([draft]).decode()}
        )
        self.assert_json_success(response)
        self.assertEqual(
            Draft.objects.get(user_profile=guest).recipient_id, web_public.recipient_id
        )

    def test_create_stream_draft_checks_content_access(self) -> None:
        owner = self.example_user("iago")
        guest = self.example_user("polonius")
        self.assertTrue(owner.is_realm_admin)
        self.assertTrue(guest.is_guest)
        private = self.make_stream("Private draft channel", invite_only=True)
        foreign = self.make_stream("Foreign draft channel", realm=get_realm("zephyr"))
        draft = {"type": "stream", "topic": "Drafts", "content": "Unsent"}

        def post_draft(user: UserProfile, stream: Stream) -> "TestHttpResponse":
            return self.api_post(
                user,
                "/api/v1/drafts",
                {"drafts": orjson.dumps([{**draft, "to": [stream.id]}]).decode()},
            )

        initial_count = Draft.objects.filter(user_profile=owner).count()
        self.assert_json_error(post_draft(owner, private), "Invalid channel ID")
        self.assert_json_error(post_draft(owner, foreign), "Invalid channel ID")
        self.assertEqual(Draft.objects.filter(user_profile=owner).count(), initial_count)

        subscribers = check_add_user_group(
            owner.realm, "Draft channel subscriber managers", [owner], acting_user=owner
        )
        private.can_add_subscribers_group = subscribers
        private.save(update_fields=["can_add_subscribers_group"])
        self.assert_json_success(post_draft(owner, private))
        self.assertTrue(
            Draft.objects.filter(user_profile=owner, recipient=private.recipient).exists()
        )

        self.subscribe(guest, private.name)
        self.assert_json_success(post_draft(guest, private))
        self.assertEqual(Draft.objects.get(user_profile=guest).recipient_id, private.recipient_id)
        self.unsubscribe(guest, private.name)
        self.assert_json_error(post_draft(guest, private), "Invalid channel ID")
        self.assertEqual(Draft.objects.filter(user_profile=guest).count(), 1)

    def test_create_personal_message_draft_for_non_existing_user(self) -> None:
        draft_dicts = [
            {
                "type": "private",
                "to": [99999999999999],  # Hopefully, this doesn't exist either.
                "topic": "This topic should be ignored.",
                "content": "What if we made it possible to sync drafts in Zulip?",
                "timestamp": 1595479019,
            }
        ]
        self.create_and_check_drafts_for_error(draft_dicts, "Invalid user ID 99999999999999")

    def test_create_direct_message_draft_with_cross_realm_recipients(self) -> None:
        owner = self.example_user("hamlet")
        other_realm_user = self.mit_user("starnine")
        system_bot = self.notification_bot(get_realm(settings.SYSTEM_BOT_REALM))
        self.assertNotEqual(system_bot.realm_id, owner.realm_id)
        draft = {
            "type": "private",
            "to": [system_bot.id],
            "topic": "",
            "content": "Unsent",
            "timestamp": 42,
        }
        rejected_members = [owner.id, system_bot.id, other_realm_user.id]
        self.assertIsNone(get_direct_message_group(rejected_members))
        with self.capture_send_event_calls(expected_num_events=0):
            self.create_and_check_drafts_for_error(
                [{**draft, "to": [system_bot.id, other_realm_user.id]}],
                f"Invalid user ID {other_realm_user.id}",
            )
        self.assertIsNone(get_direct_message_group(rejected_members))

        with self.capture_send_event_calls(expected_num_events=1):
            self.create_and_check_drafts_for_success([draft])
        group = get_direct_message_group([owner.id, system_bot.id])
        assert group is not None
        self.assertEqual(Draft.objects.get(user_profile=owner).recipient_id, group.recipient_id)

    def test_create_direct_message_draft_with_deactivated_recipient(self) -> None:
        owner = self.example_user("hamlet")
        recipient = self.example_user("othello")
        do_deactivate_user(recipient, acting_user=None)
        members = [owner.id, recipient.id]
        self.assertIsNone(get_direct_message_group(members))
        draft = {"type": "private", "to": [recipient.id], "topic": "", "content": "Unsent"}
        with self.capture_send_event_calls(expected_num_events=0):
            self.create_and_check_drafts_for_error(
                [draft], f"'{recipient.email}' is no longer using Zulip."
            )
        self.assertIsNone(get_direct_message_group(members))

    def test_create_personal_message_draft_for_inaccessible_users(self) -> None:
        self.set_up_db_for_testing_user_access()
        polonius = self.example_user("polonius")
        # Polonius shares a channel subscription with Hamlet, but has
        # no way to access Othello.
        hamlet = self.example_user("hamlet")
        othello = self.example_user("othello")

        draft_dict = {
            "type": "private",
            "to": [othello.id],
            "topic": "",
            "content": "What if we made it possible to sync drafts in Zulip?",
            "timestamp": 1595479019,
        }
        payload = {"drafts": orjson.dumps([draft_dict]).decode()}
        resp = self.api_post(polonius, "/api/v1/drafts", payload)
        self.assert_json_error(resp, "You do not have permission to access some of the recipients.")

        draft_dict["to"] = [hamlet.id, othello.id]
        payload = {"drafts": orjson.dumps([draft_dict]).decode()}
        resp = self.api_post(polonius, "/api/v1/drafts", payload)
        self.assert_json_error(resp, "You do not have permission to access some of the recipients.")

        self.assertFalse(Draft.objects.filter(user_profile=polonius).exists())
        # Creating those drafts would have created a
        # DirectMessageGroup, and with it Subscription objects that
        # grant Polonius access to Othello; verify that didn't happen.
        self.assertFalse(check_can_access_user(othello, polonius))

        # Drafts addressed only to users Polonius can access still work.
        draft_dict["to"] = [hamlet.id]
        payload = {"drafts": orjson.dumps([draft_dict]).decode()}
        resp = self.api_post(polonius, "/api/v1/drafts", payload)
        self.assert_json_success(resp)
        draft = Draft.objects.get(user_profile=polonius)
        self.assertEqual(draft.to_dict()["to"], [hamlet.id])

    def test_guest_direct_message_draft_validates_recipients_before_creating_group(self) -> None:
        self.set_up_db_for_testing_user_access()
        guest = self.example_user("polonius")
        target = self.example_user("othello")
        draft = {"type": "private", "topic": "", "content": "Unsent"}

        def create_draft_to(*user_ids: int) -> "TestHttpResponse":
            data = {"drafts": orjson.dumps([{**draft, "to": user_ids}]).decode()}
            return self.api_post(guest, "/api/v1/drafts", data)

        missing_id = 2**32 + target.id
        do_deactivate_user(target, acting_user=None)
        with self.capture_send_event_calls(expected_num_events=0):
            self.assert_json_error(
                create_draft_to(target.id, missing_id), f"Invalid user ID {missing_id}"
            )
            self.assert_json_error(
                create_draft_to(target.id),
                "You do not have permission to access some of the recipients.",
            )
        self.assertIsNone(get_direct_message_group([guest.id, target.id]))
        self.assertFalse(Draft.objects.filter(user_profile=guest).exists())

        everyone = NamedUserGroup.objects.get(
            name=SystemGroups.EVERYONE, realm_for_sharding=guest.realm
        )
        do_change_realm_permission_group_setting(
            guest.realm, "can_access_all_users_group", everyone, acting_user=None
        )
        self.assert_json_error(
            create_draft_to(target.id), f"'{target.email}' is no longer using Zulip."
        )
        self.assertIsNone(get_direct_message_group([guest.id, target.id]))
        self.assertFalse(Draft.objects.filter(user_profile=guest).exists())

        with self.capture_send_event_calls(expected_num_events=1):
            self.assert_json_success(create_draft_to(guest.id, guest.id))
        self.assertEqual(Draft.objects.get(user_profile=guest).to_dict()["to"], [guest.id])

    def test_create_draft_with_null_bytes(self) -> None:
        draft_dicts = [
            {
                "type": "",
                "to": [],
                "topic": "sync drafts.",
                "content": "Some regular \x00 content here",
                "timestamp": 15954790199,
            }
        ]
        self.create_and_check_drafts_for_error(draft_dicts, "Message must not contain null bytes")

        draft_dicts = [
            {
                "type": "stream",
                "to": [10],
                "topic": "thinking about \x00",
                "content": "Let's add backend support for syncing drafts.",
                "timestamp": 15954790199,
            }
        ]
        self.create_and_check_drafts_for_error(draft_dicts, "Topic must not contain null bytes")

    def test_create_draft_truncates_unicode_body_and_topic(self) -> None:
        stream = self.make_stream("Unicode drafts")
        suffix = "\n[message truncated]"
        body_prefix = "پیام📝"
        limit = len(body_prefix) + len(suffix)
        topic_prefix = "ز" * (MAX_TOPIC_NAME_LENGTH - 4) + "📝"
        content = "\n" + body_prefix + "🟢" * limit + "\x1f"
        draft = {
            "type": "stream",
            "to": [stream.id],
            "topic": topic_prefix + "ب\x00" * 4,
            "content": content,
            "timestamp": 1595479019,
        }
        with self.settings(MAX_MESSAGE_LENGTH=limit):
            self.create_and_check_drafts_for_success(
                [draft], [{**draft, "topic": topic_prefix + "...", "content": body_prefix + suffix}]
            )
            self.create_and_check_drafts_for_error(
                [{**draft, "content": content + "\x00"}],
                "Message must not contain null bytes",
            )


class DraftEditTests(ZulipTestCase):
    def test_edit_does_not_recreate_a_deleted_draft(self) -> None:
        owner = self.example_user("hamlet")
        stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        original = Draft.objects.create(
            user_profile=owner, content="Original", last_edit_time=stamp
        )
        draft = DraftData(type="", to=[], topic="", content="Replacement", timestamp=1595479021)

        def validate_and_delete(data: DraftData, owner: UserProfile) -> ValidatedDraftData:
            validated = further_validated_draft_dict(data, owner)
            Draft.objects.filter(id=original.id).delete()
            return validated

        with (
            self.capture_send_event_calls(expected_num_events=0),
            patch(
                "zerver.lib.drafts.further_validated_draft_dict", side_effect=validate_and_delete
            ),
            self.assertRaisesRegex(
                DatabaseError, "Save with update_fields did not affect any rows"
            ),
        ):
            do_edit_draft(original.id, draft, owner)

        self.assertFalse(Draft.objects.filter(id=original.id).exists())

    def test_edit_rolls_back_if_event_serialization_fails(self) -> None:
        owner = self.example_user("hamlet")
        stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        original = Draft.objects.create(
            user_profile=owner, content="Original", last_edit_time=stamp
        )
        draft = DraftData(type="", to=[], topic="", content="Replacement", timestamp=1595479021)

        with (
            self.capture_send_event_calls(expected_num_events=0),
            patch.object(Draft, "to_dict", side_effect=ValueError("Unable to serialize draft")),
            self.assertRaisesRegex(ValueError, "Unable to serialize draft"),
        ):
            do_edit_draft(original.id, draft, owner)

        original.refresh_from_db()
        self.assertEqual(original.content, "Original")
        self.assertEqual(original.last_edit_time, stamp)

    def test_require_enable_drafts_synchronization(self) -> None:
        hamlet = self.example_user("hamlet")
        hamlet.enable_drafts_synchronization = False
        hamlet.save()
        resp = self.api_patch(hamlet, "/api/v1/drafts/1", {"draft": {}})
        self.assert_json_error(resp, "User has disabled synchronizing drafts.")

    def test_edit_draft_successfully(self) -> None:
        hamlet = self.example_user("hamlet")
        visible_streams = self.get_streams(hamlet)
        stream_a = self.get_stream_id(visible_streams[0])
        stream_b = self.get_stream_id(visible_streams[1])

        # Create a draft.
        draft_dict = {
            "type": "stream",
            "to": [stream_a],
            "topic": "drafts",
            "content": "The API should be good",
            "timestamp": 1595505700,
        }
        resp = self.api_post(
            hamlet, "/api/v1/drafts", {"drafts": orjson.dumps([draft_dict]).decode()}
        )
        self.assert_json_success(resp)
        new_draft_id = orjson.loads(resp.content)["ids"][0]

        # Change the draft data.
        draft_dict["content"] = "The API needs to be structured yet simple to use."
        draft_dict["to"] = [stream_b]
        draft_dict["topic"] = "designing drafts"
        draft_dict["timestamp"] = 1595505800

        # Update this change in the backend.
        resp = self.api_patch(
            hamlet, f"/api/v1/drafts/{new_draft_id}", {"draft": orjson.dumps(draft_dict).decode()}
        )
        self.assert_json_success(resp)

        # Now make sure that the change was made successfully.
        new_draft = Draft.objects.get(id=new_draft_id, user_profile=hamlet)
        new_draft_dict = new_draft.to_dict()
        new_draft_dict.pop("id")
        self.assertEqual(new_draft_dict, draft_dict)

    def test_edit_draft_preserves_timestamp_precision(self) -> None:
        owner = self.example_user("hamlet")
        draft = DraftData(type="", to=[], topic="", content="Unsent", timestamp=1595505700)
        (created,) = do_create_drafts([draft], owner)

        draft.timestamp = 1595505800.1234565
        do_edit_draft(created.id, draft, owner)
        created.refresh_from_db()
        self.assertEqual(
            created.last_edit_time,
            datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=1595505800123456),
        )

    def test_edit_draft_to_add_inaccessible_user(self) -> None:
        self.set_up_db_for_testing_user_access()
        polonius = self.example_user("polonius")
        hamlet = self.example_user("hamlet")
        othello = self.example_user("othello")

        draft_dict = {
            "type": "private",
            "to": [hamlet.id],
            "topic": "",
            "content": "The API should be good",
            "timestamp": 1595505700,
        }
        resp = self.api_post(
            polonius, "/api/v1/drafts", {"drafts": orjson.dumps([draft_dict]).decode()}
        )
        self.assert_json_success(resp)
        draft_id = orjson.loads(resp.content)["ids"][0]

        draft_dict["to"] = [hamlet.id, othello.id]
        resp = self.api_patch(
            polonius, f"/api/v1/drafts/{draft_id}", {"draft": orjson.dumps(draft_dict).decode()}
        )
        self.assert_json_error(resp, "You do not have permission to access some of the recipients.")

        draft = Draft.objects.get(id=draft_id, user_profile=polonius)
        self.assertEqual(draft.to_dict()["to"], [hamlet.id])
        self.assertFalse(check_can_access_user(othello, polonius))

    def test_edit_non_existent_draft(self) -> None:
        hamlet = self.example_user("hamlet")

        initial_count = Draft.objects.count()

        # Try to update a draft that doesn't exist.
        draft_dict = {
            "type": "stream",
            "to": [10],
            "topic": "drafts",
            "content": "The API should be good",
            "timestamp": 1595505700,
        }
        resp = self.api_patch(
            hamlet, "/api/v1/drafts/999999999", {"draft": orjson.dumps(draft_dict).decode()}
        )
        self.assert_json_error(resp, "Draft does not exist", status_code=404)

        # Now make sure that no changes were made.
        self.assertEqual(Draft.objects.count() - initial_count, 0)

    def test_edit_unowned_draft(self) -> None:
        hamlet = self.example_user("hamlet")
        visible_streams = self.get_streams(hamlet)
        stream_id = self.get_stream_id(visible_streams[0])

        # Create a draft.
        draft_dict = {
            "type": "stream",
            "to": [stream_id],
            "topic": "drafts",
            "content": "The API should be good",
            "timestamp": 1595505700,
        }
        resp = self.api_post(
            hamlet, "/api/v1/drafts", {"drafts": orjson.dumps([draft_dict]).decode()}
        )
        self.assert_json_success(resp)
        new_draft_id = orjson.loads(resp.content)["ids"][0]

        # Change the draft data.
        modified_draft_dict = deepcopy(draft_dict)
        modified_draft_dict["content"] = "???"

        # Update this change in the backend as a different user.
        zoe = self.example_user("ZOE")
        resp = self.api_patch(
            zoe, f"/api/v1/drafts/{new_draft_id}", {"draft": orjson.dumps(draft_dict).decode()}
        )
        self.assert_json_error(resp, "Draft does not exist", status_code=404)

        # Now make sure that no changes were made.
        existing_draft = Draft.objects.get(id=new_draft_id, user_profile=hamlet)
        existing_draft_dict = existing_draft.to_dict()
        existing_draft_dict.pop("id")
        self.assertEqual(existing_draft_dict, draft_dict)


class DraftDeleteTests(ZulipTestCase):
    def test_require_enable_drafts_synchronization(self) -> None:
        hamlet = self.example_user("hamlet")
        hamlet.enable_drafts_synchronization = False
        hamlet.save()
        resp = self.api_delete(hamlet, "/api/v1/drafts/1")
        self.assert_json_error(resp, "User has disabled synchronizing drafts.")

    def test_delete_draft_successfully(self) -> None:
        hamlet = self.example_user("hamlet")
        visible_streams = self.get_streams(hamlet)
        stream_id = self.get_stream_id(visible_streams[0])

        # Make sure that there are no drafts at the start of this test.
        initial_count = Draft.objects.count()

        # Create a draft.
        draft_dict = {
            "type": "stream",
            "to": [stream_id],
            "topic": "drafts",
            "content": "The API should be good",
            "timestamp": 1595505700,
        }
        resp = self.api_post(
            hamlet, "/api/v1/drafts", {"drafts": orjson.dumps([draft_dict]).decode()}
        )
        self.assert_json_success(resp)
        new_draft_id = orjson.loads(resp.content)["ids"][0]

        # Make sure that exactly 1 draft exists now.
        self.assertEqual(Draft.objects.count() - initial_count, 1)

        # Update this change in the backend.
        resp = self.api_delete(hamlet, f"/api/v1/drafts/{new_draft_id}")
        self.assert_json_success(resp)

        # Now make sure that there are no more drafts.
        self.assertEqual(Draft.objects.count() - initial_count, 0)

    def test_delete_non_existent_draft(self) -> None:
        hamlet = self.example_user("hamlet")

        # Make sure that no draft exists in the first place.
        initial_count = Draft.objects.count()

        # Try to delete a draft that doesn't exist.
        resp = self.api_delete(hamlet, "/api/v1/drafts/9999999999")
        self.assert_json_error(resp, "Draft does not exist", status_code=404)

        # Now make sure that no drafts were made for whatever reason.
        self.assertEqual(Draft.objects.count() - initial_count, 0)

    def test_delete_unowned_draft(self) -> None:
        hamlet = self.example_user("hamlet")
        visible_streams = self.get_streams(hamlet)
        stream_id = self.get_stream_id(visible_streams[0])

        # Make sure that there are no drafts at the start of this test.
        initial_count = Draft.objects.count()

        # Create a draft.
        draft_dict = {
            "type": "stream",
            "to": [stream_id],
            "topic": "drafts",
            "content": "The API should be good",
            "timestamp": 1595505700,
        }
        resp = self.api_post(
            hamlet, "/api/v1/drafts", {"drafts": orjson.dumps([draft_dict]).decode()}
        )
        self.assert_json_success(resp)
        new_draft_id = orjson.loads(resp.content)["ids"][0]

        # Delete this draft in the backend as a different user.
        zoe = self.example_user("ZOE")
        resp = self.api_delete(zoe, f"/api/v1/drafts/{new_draft_id}")
        self.assert_json_error(resp, "Draft does not exist", status_code=404)

        # Make sure that the draft was not deleted.
        self.assertEqual(Draft.objects.count() - initial_count, 1)

        # Now make sure that no changes were made either.
        existing_draft = Draft.objects.get(id=new_draft_id, user_profile=hamlet)
        existing_draft_dict = existing_draft.to_dict()
        existing_draft_dict.pop("id")
        self.assertEqual(existing_draft_dict, draft_dict)


class DraftFetchTest(ZulipTestCase):
    def test_require_enable_drafts_synchronization(self) -> None:
        hamlet = self.example_user("hamlet")
        hamlet.enable_drafts_synchronization = False
        hamlet.save()
        resp = self.api_get(hamlet, "/api/v1/drafts")
        self.assert_json_error(resp, "User has disabled synchronizing drafts.")

    def test_fetch_drafts(self) -> None:
        initial_count = Draft.objects.count()

        hamlet = self.example_user("hamlet")
        zoe = self.example_user("ZOE")
        othello = self.example_user("othello")
        visible_stream_id = self.get_stream_id(self.get_streams(hamlet)[0])
        draft_dicts = [
            {
                "type": "stream",
                "to": [visible_stream_id],
                "topic": "thinking out loud",
                "content": "What if pigs really could fly?",
                "timestamp": 15954790197,
            },
            {
                "type": "private",
                "to": [zoe.id, othello.id],
                "topic": "",
                "content": "What if made it possible to sync drafts in Zulip?",
                "timestamp": 15954790198,
            },
            {
                "type": "private",
                "to": [zoe.id],
                "topic": "",
                "content": "What if made it possible to sync drafts in Zulip?",
                "timestamp": 15954790199,
            },
        ]
        payload = {"drafts": orjson.dumps(draft_dicts).decode()}
        resp = self.api_post(hamlet, "/api/v1/drafts", payload)
        self.assert_json_success(resp)

        self.assertEqual(Draft.objects.count() - initial_count, 3)

        zoe_draft_dicts = [
            {
                "type": "private",
                "to": [hamlet.id],
                "topic": "",
                "content": "Hello there!",
                "timestamp": 15954790200,
            },
        ]
        payload = {"drafts": orjson.dumps(zoe_draft_dicts).decode()}
        resp = self.api_post(zoe, "/api/v1/drafts", payload)
        self.assert_json_success(resp)

        self.assertEqual(Draft.objects.count() - initial_count, 4)

        # Now actually fetch the drafts. Make sure that hamlet gets only
        # his drafts and exactly as he made them.
        resp = self.api_get(hamlet, "/api/v1/drafts")
        self.assert_json_success(resp)
        data = orjson.loads(resp.content)
        self.assertEqual(data["count"], 3)

        first_draft_id = Draft.objects.filter(user_profile=hamlet).order_by("id")[0].id
        expected_draft_contents: list[dict[str, object]] = [
            {"id": first_draft_id + i, **draft_dicts[i]} for i in range(3)
        ]

        self.assertEqual(data["drafts"], expected_draft_contents)


class DraftReadHTTPTest(ZulipTransactionTestCase, LiveServerTestCase):
    def test_fetch_parity_with_rust(self) -> None:
        user = self.example_user("hamlet")
        other = self.example_user("othello")
        third = self.example_user("ZOE")
        stream = Stream.objects.get(realm=user.realm, name="Denmark")
        created_groups: list[DirectMessageGroup] = []

        def group_recipient(users: list[UserProfile]) -> Recipient:
            ids = [member.id for member in users]
            group = get_direct_message_group(ids)
            if group is None:
                group = get_or_create_direct_message_group(ids)
                created_groups.append(group)
            assert group.recipient is not None
            return group.recipient

        session = SessionStore()
        session.update(
            {
                "_auth_user_id": str(user.id),
                "_auth_user_backend": "zproject.backends.DevAuthBackend",
                "_auth_user_hash": user.get_session_auth_hash(),
            }
        )
        session.create()
        api_headers = {
            "Authorization": "Basic "
            + b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
        }
        web_headers = {"Cookie": f"{settings.SESSION_COOKIE_NAME}={session.session_key}"}
        api = "/api/v1/drafts"
        web = "/json/drafts"
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None
        original_sync = user.enable_drafts_synchronization
        created_drafts: list[Draft] = []
        try:
            group = group_recipient([user, other, third])
            self_dm = group_recipient([user])
            one_to_one = group_recipient([user, other])
            items = [
                (user, group, "", "group", datetime(2025, 1, 1, tzinfo=timezone.utc)),
                (
                    user,
                    None,
                    "old topic",
                    "missing recipient",
                    datetime(1969, 12, 31, 23, 59, 59, 500000, tzinfo=timezone.utc),
                ),
                (
                    user,
                    stream.recipient,
                    "سلام",
                    "stream ☃",
                    datetime(2024, 1, 1, tzinfo=timezone.utc),
                ),
                (user, self_dm, "", "self", datetime(2025, 2, 1, tzinfo=timezone.utc)),
                (other, group, "", "other user's draft", datetime(2025, 3, 1, tzinfo=timezone.utc)),
                (user, one_to_one, "", "direct", datetime(2025, 4, 1, tzinfo=timezone.utc)),
            ]
            created_drafts = Draft.objects.bulk_create(
                [
                    Draft(
                        user_profile=owner,
                        recipient=recipient,
                        topic=topic,
                        content=content,
                        last_edit_time=timestamp,
                    )
                    for owner, recipient, topic, content, timestamp in items
                ]
            )
            with TemporaryDirectory() as directory:
                secret = Path(directory) / "secret"
                secret.write_text(settings.SECRET_KEY)
                env = {
                    "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
                    "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                        settings.AUTHENTICATION_BACKENDS
                    ).decode(),
                }
                service = (
                    rust_http_service(env)
                    if os.environ.get("ZULIP_RUST_HTTP_BIN")
                    else nullcontext(None)
                )
                with service as rust_port:

                    def check_request(
                        method: str,
                        path: str,
                        headers: dict[str, str],
                        status: int,
                        host: str = user.realm.host,
                    ) -> bytes:
                        django = request_test_http(django_port, method, host, headers, path)
                        self.assertEqual(django[0], status, django[1])
                        if rust_port is not None:
                            rust = request_test_http(rust_port, method, host, headers, path)
                            self.assertEqual(rust[:3], django[:3])
                        return django[1]

                    for path, headers in ((api, api_headers), (web, web_headers)):
                        with self.subTest(path=path):
                            data = orjson.loads(check_request("GET", path, headers, 200))
                            self.assertEqual(data["count"], 5)
                            self.assertEqual(
                                [
                                    (item["content"], item["type"], item["to"])
                                    for item in data["drafts"]
                                ],
                                [
                                    ("missing recipient", "", []),
                                    ("stream ☃", "stream", [stream.id]),
                                    ("group", "private", sorted([other.id, third.id])),
                                    ("self", "private", [user.id]),
                                    ("direct", "private", [other.id]),
                                ],
                            )
                            self.assertEqual(data["drafts"][0]["timestamp"], 0)
                            self.assertEqual(check_request("HEAD", path, headers, 200), b"")
                            check_request("GET", path, {}, 401)
                    other_host = Realm.objects.get(string_id="zephyr").host
                    check_request("GET", api, api_headers, 401, other_host)
                    user.enable_drafts_synchronization = False
                    user.save(update_fields=["enable_drafts_synchronization"])
                    for path, headers in ((api, api_headers), (web, web_headers)):
                        data = orjson.loads(check_request("GET", path, headers, 400))
                        self.assertEqual(data["msg"], "User has disabled synchronizing drafts.")
                    ids = [draft.id for draft in created_drafts]
                    self.assertEqual(Draft.objects.filter(id__in=ids).count(), len(ids))
        finally:
            user.enable_drafts_synchronization = original_sync
            user.save(update_fields=["enable_drafts_synchronization"])
            Draft.objects.filter(id__in=[draft.id for draft in created_drafts]).delete()
            for created_group in created_groups:
                recipient_id = created_group.recipient_id
                assert recipient_id is not None
                created_group.delete()
                Recipient.objects.filter(id=recipient_id).delete()
            session.delete()


@skipUnless(os.environ.get("ZULIP_RUST_ACTIVITY_TEST_VHOST"), "RabbitMQ test vhost is unavailable")
class DraftDeleteHTTPTest(ZulipTransactionTestCase, LiveServerTestCase):
    def test_delete_event_and_ownership_parity(self) -> None:
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
        stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        victim = Draft.objects.create(user_profile=user, content="delete me", last_edit_time=stamp)
        private = Draft.objects.create(
            user_profile=other, content="other draft", last_edit_time=stamp
        )
        original_sync = user.enable_drafts_synchronization
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
        basic = {
            "Authorization": "Basic "
            + b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
        }
        cookie = {
            "Cookie": f"{settings.SESSION_COOKIE_NAME}={session.session_key}; {settings.CSRF_COOKIE_NAME}={token}"
        }
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None
        use_rust = os.environ.get("ZULIP_RUST_HTTP_BIN")
        api = "/api/v1/drafts"
        web = "/json/drafts"
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
                secret = Path(directory) / "secret"
                secret.write_text(settings.SECRET_KEY)
                environment = {
                    "ZULIP_RUST_DRAFT_DELETES": "1",
                    "ZULIP_RUST_AMQP_URL": amqp_url,
                    "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
                    "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                        settings.AUTHENTICATION_BACKENDS
                    ).decode(),
                }
                channel = broker.channel()
                for queue in ("notify_tornado", "user_activity"):
                    channel.queue_declare(queue, durable=True)
                    channel.queue_purge(queue)
                notification = {
                    "event": {"type": "drafts", "op": "remove", "draft_id": victim.id},
                    "users": [user.id],
                }

                def reset_victim() -> None:
                    Draft.objects.get_or_create(
                        id=victim.id,
                        defaults={
                            "user_profile": user,
                            "content": victim.content,
                            "last_edit_time": stamp,
                        },
                    )

                def check_port(
                    port: int, prefix: str, headers: dict[str, str], secure: bool
                ) -> list[tuple[int, bytes, dict[str, str | None]]]:
                    reset_victim()
                    outcomes = []

                    def send(draft_id: int, status: int, auth: dict[str, str] = headers) -> None:
                        result = request_test_http(
                            port, "DELETE", user.realm.host, auth, f"{prefix}/{draft_id}"
                        )
                        self.assertEqual(result[0], status, result[1])
                        outcomes.append(result[:3])
                        _, _, event = channel.basic_get("notify_tornado", auto_ack=True)
                        if status == 200:
                            self.assertFalse(Draft.objects.filter(id=victim.id).exists())
                            assert event is not None
                            self.assertEqual(orjson.loads(event), notification)
                            self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[2])
                        else:
                            self.assertIsNone(event)
                        self.assertTrue(
                            Draft.objects.filter(id=private.id, user_profile=other).exists()
                        )

                    send(private.id, 404)
                    send(2**63, 404)
                    if prefix == web:
                        send(
                            victim.id,
                            403,
                            {key: value for key, value in headers.items() if key != "X-CSRFToken"},
                        )
                        if secure:
                            send(
                                victim.id,
                                403,
                                {key: value for key, value in headers.items() if key != "Referer"},
                            )
                    user.enable_drafts_synchronization = False
                    user.save(update_fields=["enable_drafts_synchronization"])
                    send(victim.id, 400)
                    self.assertTrue(Draft.objects.filter(id=victim.id).exists())
                    user.enable_drafts_synchronization = True
                    user.save(update_fields=["enable_drafts_synchronization"])
                    send(victim.id, 200)
                    send(victim.id, 404)
                    return outcomes

                for scheme in ("http", "https"):
                    secure = scheme == "https"
                    web_auth = {**cookie, "X-CSRFToken": token}
                    if secure:
                        web_auth.update(
                            {"X-Forwarded-Proto": "https", "Referer": f"https://{user.realm.host}/"}
                        )
                    else:
                        web_auth["Origin"] = f"http://{user.realm.host}"
                    routes = [(web, web_auth)] if secure else [(api, basic), (web, web_auth)]
                    service = (
                        rust_http_service({**environment, "ZULIP_RUST_WEB_POST_SCHEME": scheme})
                        if use_rust
                        else nullcontext(None)
                    )
                    with service as rust_port:
                        for prefix, headers in routes:
                            with self.subTest(scheme=scheme, path=prefix):
                                django = check_port(django_port, prefix, headers, secure)
                                if rust_port is not None:
                                    self.assertEqual(
                                        check_port(rust_port, prefix, headers, secure), django
                                    )
                        if rust_port is not None and not secure:
                            reset_victim()
                            target = f"{api}/{victim.id}"
                            with ThreadPoolExecutor(max_workers=2) as requests:
                                pending = [
                                    requests.submit(
                                        request_test_http,
                                        rust_port,
                                        "DELETE",
                                        user.realm.host,
                                        basic,
                                        target,
                                    )
                                    for _ in range(2)
                                ]
                                self.assertCountEqual(
                                    [response.result()[0] for response in pending], [200, 404]
                                )
                            self.assertFalse(Draft.objects.filter(id=victim.id).exists())
                            _, _, event = channel.basic_get("notify_tornado", auto_ack=True)
                            assert event is not None
                            self.assertEqual(orjson.loads(event), notification)
                            self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[2])
                for queue in ("notify_tornado", "user_activity"):
                    channel.queue_purge(queue)
        finally:
            user.enable_drafts_synchronization = original_sync
            user.save(update_fields=["enable_drafts_synchronization"])
            Draft.objects.filter(id__in=[victim.id, private.id]).delete()
            session.delete()

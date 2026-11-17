import os
from importlib import import_module
from typing import Any, cast

from django.db import models
from typing_extensions import override

from zerver.lib.display_recipient import get_display_recipient, get_recipient_ids
from zerver.models.constants import MAX_TOPIC_NAME_LENGTH
from zerver.models.recipients import Recipient
from zerver.models.users import UserProfile

_rust_draft_serialization = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_DRAFT_SERIALIZATION") == "1"
    else None
)


class Draft(models.Model):
    """Server-side storage model for storing drafts so that drafts can be synced across
    multiple clients/devices.
    """

    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE)
    recipient = models.ForeignKey(Recipient, null=True, on_delete=models.SET_NULL)
    topic = models.CharField(max_length=MAX_TOPIC_NAME_LENGTH, db_index=True)
    content = models.TextField()  # Length should not exceed MAX_MESSAGE_LENGTH
    last_edit_time = models.DateTimeField(db_index=True)

    @override
    def __str__(self) -> str:
        return f"{self.user_profile.email} / {self.id} / {self.last_edit_time}"

    def to_dict(self) -> dict[str, Any]:
        if _rust_draft_serialization is not None:
            channel_id = None
            dm_members = None
            if self.recipient is not None:
                if self.recipient.type == Recipient.STREAM:
                    channel_id = self.recipient.type_id
                else:
                    dm_members = [member["id"] for member in get_display_recipient(self.recipient)]
            return cast(
                dict[str, object],
                _rust_draft_serialization.serialize_draft(
                    self.id,
                    self.user_profile_id,
                    channel_id,
                    dm_members,
                    self.topic,
                    self.content,
                    int(self.last_edit_time.timestamp()),
                ),
            )

        to, recipient_type_str = get_recipient_ids(self.recipient, self.user_profile_id)
        return {
            "id": self.id,
            "type": recipient_type_str,
            "to": to,
            "topic": self.topic,
            "content": self.content,
            "timestamp": int(self.last_edit_time.timestamp()),
        }

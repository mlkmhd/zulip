import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import wraps
from importlib import import_module
from typing import Annotated, Concatenate, Literal, Never, TypedDict, cast

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, transaction
from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _
from pydantic import BaseModel, ConfigDict, Json, ValidatorFunctionWrapHandler, WrapValidator
from pydantic import ValidationError as PydanticValidationError
from typing_extensions import ParamSpec

from zerver.lib.addressee import get_user_profiles_by_ids
from zerver.lib.event_types import DraftFields, DraftsAddEvent, DraftsRemoveEvent, DraftsUpdateEvent
from zerver.lib.exceptions import JsonableError, ResourceNotFoundError
from zerver.lib.message import normalize_body, truncate_topic
from zerver.lib.recipient_users import (
    check_sender_can_access_recipients,
    recipient_for_user_profiles,
)
from zerver.lib.streams import access_stream_by_id
from zerver.lib.timestamp import timestamp_to_datetime
from zerver.lib.typed_endpoint import RequiredStringConstraint
from zerver.models import Draft, UserProfile
from zerver.models.constants import MAX_TOPIC_NAME_LENGTH
from zerver.models.recipients import (
    get_or_create_direct_message_group,
    get_or_create_direct_message_group_recipient_in_rust,
)
from zerver.tornado.django_api import send_event_on_commit

ParamT = ParamSpec("ParamT")
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_rust_draft_text = (
    import_module("_zulip_backend") if os.environ.get("ZULIP_RUST_DRAFT_TEXT") == "1" else None
)
_rust_draft_timestamp = (
    import_module("_zulip_backend") if os.environ.get("ZULIP_RUST_DRAFT_TIMESTAMP") == "1" else None
)
_rust_draft_input = (
    import_module("_zulip_backend") if os.environ.get("ZULIP_RUST_DRAFT_INPUT") == "1" else None
)
_rust_draft_channel_access = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_DRAFT_CHANNEL_ACCESS") == "1"
    else None
)
_rust_draft_direct_access = (
    import_module("_zulip_backend")
    if os.environ.get("ZULIP_RUST_DRAFT_DIRECT_ACCESS") == "1"
    else None
)
_rust_draft_creation = (
    import_module("_zulip_backend") if os.environ.get("ZULIP_RUST_DRAFT_CREATION") == "1" else None
)
_rust_draft_edit = (
    import_module("_zulip_backend") if os.environ.get("ZULIP_RUST_DRAFT_EDIT") == "1" else None
)
_rust_draft_groups = os.environ.get("ZULIP_RUST_DRAFT_GROUPS") == "1"


def normalize_draft_body(content: str) -> str:
    if _rust_draft_text is None or settings.MAX_MESSAGE_LENGTH < 0:
        return normalize_body(content)
    normalized, error = cast(
        tuple[str | None, Literal["empty", "null_byte"] | None],
        _rust_draft_text.normalize_draft_body(content, settings.MAX_MESSAGE_LENGTH),
    )
    if error == "empty":
        raise JsonableError(_("Message must not be empty"))
    if error == "null_byte":
        raise JsonableError(_("Message must not contain null bytes"))
    assert normalized is not None
    return normalized


def normalize_draft_topic(topic: str) -> str:
    if _rust_draft_text is None:
        truncated = truncate_topic(topic)
        if "\0" not in truncated:
            return truncated
    else:
        normalized = cast(
            str | None, _rust_draft_text.normalize_draft_topic(topic, MAX_TOPIC_NAME_LENGTH)
        )
        if normalized is not None:
            return normalized
    raise JsonableError(_("Topic must not contain null bytes"))


def normalize_draft_timestamp(timestamp: float) -> datetime:
    if _rust_draft_timestamp is not None:
        try:
            micros, negative = cast(
                tuple[int | None, bool], _rust_draft_timestamp.normalize_draft_timestamp(timestamp)
            )
        except OverflowError:
            # An arbitrary-size integer can overflow while crossing into Rust.
            pass
        else:
            if negative:
                raise JsonableError(_("Timestamp must not be negative."))
            if micros is not None:
                return _UNIX_EPOCH + timedelta(microseconds=micros)

    # Preserve datetime's handling of non-finite and out-of-range values.
    timestamp = round(timestamp, 6)
    if timestamp < 0:
        raise JsonableError(_("Timestamp must not be negative."))
    return timestamp_to_datetime(timestamp)


@dataclass
class ValidatedDraftData:
    recipient_id: int | None
    topic: str
    content: str
    last_edit_time: datetime


DraftType = Literal["private", "stream", ""]


class DraftData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: DraftType
    to: list[int]
    topic: str
    content: Annotated[str, RequiredStringConstraint()]
    timestamp: int | float | None = None


class RustDraftInput(TypedDict):
    type: DraftType
    to: list[int]
    topic: str
    content: str
    timestamp: int | float | None


RustDraftInputError = tuple[str, list[str | int]]


def raise_draft_input_error(error: RustDraftInputError) -> Never:
    kind, path = error
    context: dict[str, object] = {}
    if kind == "model_type":
        context["class_name"] = "DraftData"
    elif kind == "literal_error":
        context["expected"] = "'private', 'stream' or ''"
    elif kind == "string_too_short":
        context["min_length"] = 1
    raise PydanticValidationError.from_exception_data(
        "DraftData", [{"type": kind, "loc": tuple(path), "input": None, "ctx": context}]
    )


def parse_draft_input(value: object, handler: ValidatorFunctionWrapHandler) -> DraftData:
    if _rust_draft_input is not None and isinstance(value, str):
        draft, error = cast(
            tuple[RustDraftInput | None, RustDraftInputError | None],
            _rust_draft_input.parse_draft(value),
        )
        if error is not None:
            raise_draft_input_error(error)
        if draft is not None:
            return DraftData.model_construct(**draft)
    return cast(DraftData, handler(value))


def parse_draft_batch_input(
    value: object, handler: ValidatorFunctionWrapHandler
) -> list[DraftData]:
    if _rust_draft_input is not None and isinstance(value, str):
        drafts, error = cast(
            tuple[list[RustDraftInput] | None, RustDraftInputError | None],
            _rust_draft_input.parse_draft_batch(value),
        )
        if error is not None:
            raise_draft_input_error(error)
        if drafts is not None:
            return [DraftData.model_construct(**draft) for draft in drafts]
    return cast(list[DraftData], handler(value))


DraftInput = Annotated[Json[DraftData], WrapValidator(parse_draft_input)]
DraftBatchInput = Annotated[Json[list[DraftData]], WrapValidator(parse_draft_batch_input)]


def get_draft_channel_recipient_id(user_profile: UserProfile, channel_id: int) -> int:
    if _rust_draft_channel_access is None or not (-(2**63) <= channel_id < 2**63):
        stream, _sub = access_stream_by_id(user_profile, channel_id)
        assert stream.recipient_id is not None
        return stream.recipient_id

    with connection.cursor() as cursor:
        row = cast(
            tuple[int | None] | None,
            _rust_draft_channel_access.draft_channel_recipient(
                cursor, channel_id, user_profile.realm_id, user_profile.id, user_profile.is_guest
            ),
        )
    if row is None:
        raise JsonableError(_("Invalid channel ID"))
    (recipient_id,) = row
    assert recipient_id is not None
    return recipient_id


def get_draft_direct_recipient_id(user_profile: UserProfile, to: list[int]) -> int | None:
    user_ids = set(to)
    if _rust_draft_direct_access is None or any(
        not (-(2**63) <= user_id < 2**63) for user_id in user_ids
    ):
        to_users = get_user_profiles_by_ids(user_ids, user_profile.realm)
        check_sender_can_access_recipients(user_profile, to_users)
        try:
            return recipient_for_user_profiles(
                to_users, None, user_profile, use_rust_for_group_creation=_rust_draft_groups
            ).id
        except ValidationError as e:  # nocoverage
            raise JsonableError(e.messages[0])

    with connection.cursor() as cursor:
        error = cast(
            tuple[Literal["invalid_id", "inaccessible", "deactivated", "cross_realm"], str] | None,
            _rust_draft_direct_access.validate_draft_direct_recipients(
                cursor,
                user_profile.id,
                user_profile.realm_id,
                user_profile.email,
                user_profile.is_guest,
                list(user_ids),
                settings.CROSS_REALM_BOT_EMAILS,
            ),
        )
    if error is not None:
        kind, value = error
        if kind == "invalid_id":
            raise JsonableError(_("Invalid user ID {user_id}").format(user_id=value))
        if kind == "inaccessible":
            raise JsonableError(_("You do not have permission to access some of the recipients."))
        if kind == "deactivated":
            raise JsonableError(_("'{email}' is no longer using Zulip.").format(email=value))
        raise JsonableError(_("You can't send direct messages outside of your organization."))

    # Create subscriptions only after validating access to the existing users.
    user_ids.add(user_profile.id)
    if _rust_draft_groups:
        return get_or_create_direct_message_group_recipient_in_rust(list(user_ids)).id
    return get_or_create_direct_message_group(list(user_ids)).recipient_id


def further_validated_draft_dict(
    draft_dict: DraftData, user_profile: UserProfile
) -> ValidatedDraftData:
    """Take a DraftData object that was already validated by the @typed_endpoint
    decorator then further sanitize, validate, and transform it.
    Ultimately return this "further validated" draft dict.
    It will have a slightly different set of keys the values
    for which can be used to directly create a Draft object."""

    content = normalize_draft_body(draft_dict.content)

    timestamp = draft_dict.timestamp
    if timestamp is None:
        timestamp = time.time()
    last_edit_time = normalize_draft_timestamp(timestamp)

    topic_name = ""
    recipient_id = None
    to = draft_dict.to
    if draft_dict.type == "stream":
        topic_name = normalize_draft_topic(draft_dict.topic)
        if len(to) != 1:
            raise JsonableError(_("Must specify exactly 1 channel ID for channel messages"))
        recipient_id = get_draft_channel_recipient_id(user_profile, to[0])
    elif draft_dict.type == "private" and len(to) != 0:
        recipient_id = get_draft_direct_recipient_id(user_profile, to)

    return ValidatedDraftData(
        recipient_id=recipient_id,
        topic=topic_name,
        content=content,
        last_edit_time=last_edit_time,
    )


def draft_endpoint(
    view_func: Callable[Concatenate[HttpRequest, UserProfile, ParamT], HttpResponse],
) -> Callable[Concatenate[HttpRequest, UserProfile, ParamT], HttpResponse]:
    @wraps(view_func)
    def draft_view_func(
        request: HttpRequest,
        user_profile: UserProfile,
        /,
        *args: ParamT.args,
        **kwargs: ParamT.kwargs,
    ) -> HttpResponse:
        if not user_profile.enable_drafts_synchronization:
            raise JsonableError(_("User has disabled synchronizing drafts."))
        return view_func(request, user_profile, *args, **kwargs)

    return draft_view_func


def do_create_drafts(drafts: list[DraftData], user_profile: UserProfile) -> list[Draft]:
    """Create drafts in bulk for a given user based on the DraftData objects. Since
    currently, the only place this method is being used (apart from tests) is from
    the create_draft view, we assume that these are syntactically valid
    (i.e. they satisfy the @typed_endpoint validation for DraftData)."""
    draft_objects = []
    for draft_data in drafts:
        valid_draft_dict = further_validated_draft_dict(draft_data, user_profile)
        draft_objects.append(
            Draft(
                user_profile=user_profile,
                recipient_id=valid_draft_dict.recipient_id,
                topic=valid_draft_dict.topic,
                content=valid_draft_dict.content,
                last_edit_time=valid_draft_dict.last_edit_time,
            )
        )

    with transaction.atomic(durable=True):
        if _rust_draft_creation is None:
            Draft.objects.bulk_create(draft_objects)
        elif draft_objects:
            with connection.cursor() as cursor:
                rows = cast(
                    list[tuple[int]],
                    _rust_draft_creation.insert_drafts(
                        cursor,
                        user_profile.id,
                        [draft.recipient_id for draft in draft_objects],
                        [draft.topic for draft in draft_objects],
                        [draft.content for draft in draft_objects],
                        [draft.last_edit_time for draft in draft_objects],
                    ),
                )
            for draft, (draft_id,) in zip(draft_objects, rows, strict=True):
                draft.id = draft_id
                draft._state.adding = False
                draft._state.db = connection.alias

        event = DraftsAddEvent(drafts=[DraftFields(**draft.to_dict()) for draft in draft_objects])
        send_event_on_commit(user_profile.realm, event, [user_profile.id])

    return draft_objects


def do_edit_draft(draft_id: int, draft: DraftData, user_profile: UserProfile) -> None:
    """Edit/update a single draft for a given user. Since the only place this method is being
    used from (apart from tests) is the edit_draft view, we assume that the DraftData object
    is syntactically valid (i.e. it satisfies the @typed_endpoint validation for DraftData)."""
    try:
        draft_object = Draft.objects.get(id=draft_id, user_profile=user_profile)
    except Draft.DoesNotExist:
        raise ResourceNotFoundError(_("Draft does not exist"))
    valid_draft_dict = further_validated_draft_dict(draft, user_profile)
    draft_object.content = valid_draft_dict.content
    draft_object.topic = valid_draft_dict.topic
    draft_object.recipient_id = valid_draft_dict.recipient_id
    draft_object.last_edit_time = valid_draft_dict.last_edit_time

    with transaction.atomic(durable=True):
        if _rust_draft_edit is None:
            draft_object.save(update_fields=["content", "topic", "recipient_id", "last_edit_time"])
        else:
            with connection.cursor() as cursor:
                updated = _rust_draft_edit.update_draft(
                    cursor,
                    user_profile.id,
                    draft_id,
                    draft_object.recipient_id,
                    draft_object.topic,
                    draft_object.content,
                    draft_object.last_edit_time,
                )
            if not updated:
                raise DatabaseError("Save with update_fields did not affect any rows.")

        event = DraftsUpdateEvent(draft=DraftFields(**draft_object.to_dict()))
        send_event_on_commit(user_profile.realm, event, [user_profile.id])


@transaction.atomic(durable=True)
def do_delete_draft(draft_id: int, user_profile: UserProfile) -> None:
    """Delete a draft belonging to a particular user."""
    try:
        draft_object = Draft.objects.get(id=draft_id, user_profile=user_profile)
    except Draft.DoesNotExist:
        raise ResourceNotFoundError(_("Draft does not exist"))

    draft_id = draft_object.id
    draft_object.delete()

    event = DraftsRemoveEvent(draft_id=draft_id)
    send_event_on_commit(user_profile.realm, event, [user_profile.id])

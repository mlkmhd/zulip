from uuid import uuid4

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseNotFound
from django.utils.translation import gettext as _

from zerver.actions.streams import set_stream_property
from zerver.context_processors import get_valid_realm_from_request
from zerver.lib.channel_icon import channel_icon_path, channel_icon_url
from zerver.lib.exceptions import JsonableError
from zerver.lib.response import json_success
from zerver.lib.streams import (
    access_stream_by_id,
    access_stream_for_delete_or_update_requiring_metadata_access,
    access_web_public_stream,
)
from zerver.lib.thumbnail import resize_avatar
from zerver.lib.typed_endpoint import PathOnly, typed_endpoint
from zerver.lib.upload import attachment_source, store_message_attachment
from zerver.models import Stream, UserProfile


@typed_endpoint
def serve_channel_icon(
    request: HttpRequest,
    user_profile: UserProfile | AnonymousUser,
    *,
    stream_id: PathOnly[int],
    v: str | None = None,
) -> HttpResponse:
    if isinstance(user_profile, UserProfile):
        stream, _subscription = access_stream_by_id(
            user_profile, stream_id, require_active_channel=False, require_content_access=False
        )
    else:
        stream = access_web_public_stream(stream_id, get_valid_realm_from_request(request))

    if stream.icon_key is None or (v is not None and v != str(stream.icon_key)):
        return HttpResponseNotFound()

    path = channel_icon_path(stream.realm_id, stream.id, stream.icon_key)
    reader = attachment_source(path).reader()
    try:
        return HttpResponse(reader.read(), content_type="image/png")
    finally:
        reader.close()


def update_channel_icon(
    user_profile: UserProfile, stream_id: int, image: bytes | None
) -> str | None:
    with transaction.atomic(durable=True):
        # Serialize changes so the audit log and events reflect the previous picture.
        Stream.objects.select_for_update().filter(id=stream_id, realm=user_profile.realm).first()
        stream, _subscription = access_stream_for_delete_or_update_requiring_metadata_access(
            user_profile, stream_id
        )
        if stream.deactivated:
            raise JsonableError(_("This channel is archived."))

        icon_key = None
        if image is not None:
            icon_key = uuid4()
            path = channel_icon_path(stream.realm_id, stream.id, icon_key)
            store_message_attachment(
                path, "channel-icon.png", "image/png", image, user_profile, None
            )

        set_stream_property(stream, "icon_key", icon_key, user_profile)

    return channel_icon_url(stream_id, icon_key)


@typed_endpoint
def upload_channel_icon(
    request: HttpRequest, user_profile: UserProfile, *, stream_id: PathOnly[int]
) -> HttpResponse:
    access_stream_for_delete_or_update_requiring_metadata_access(user_profile, stream_id)
    files = [file for _, values in request.FILES.lists() for file in values]
    if len(files) != 1:
        raise JsonableError(_("You must upload exactly one icon."))

    [file] = files
    assert file.size is not None
    if file.size > settings.MAX_ICON_FILE_SIZE_MIB * 1024 * 1024:
        raise JsonableError(
            _("Uploaded file is larger than the allowed limit of {max_size} MiB").format(
                max_size=settings.MAX_ICON_FILE_SIZE_MIB,
            )
        )

    icon_url = update_channel_icon(user_profile, stream_id, resize_avatar(file.read()))
    return json_success(request, data={"icon_url": icon_url})


@typed_endpoint
def delete_channel_icon(
    request: HttpRequest, user_profile: UserProfile, *, stream_id: PathOnly[int]
) -> HttpResponse:
    update_channel_icon(user_profile, stream_id, None)
    return json_success(request)

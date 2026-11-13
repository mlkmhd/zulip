import re
from uuid import UUID

CHANNEL_ICON_PATH = re.compile(
    r"channel-icons/([0-9]+)/([0-9]+)/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\.png"
)


def channel_icon_url(stream_id: int, icon_key: UUID | None) -> str | None:
    if icon_key is None:
        return None
    return f"/json/streams/{stream_id}/icon?v={icon_key}"


def channel_icon_path(realm_id: int, stream_id: int, icon_key: UUID) -> str:
    return f"channel-icons/{realm_id}/{stream_id}/{icon_key}.png"

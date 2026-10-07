"""Link previews (TDLib linkPreview in messageText, or from getLinkPreview): what a card under
the text shows. Qt-free.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .media import _minithumbnail, _photo, _thumbnail_file, duration_text

MAX_DESCRIPTION = 300
_KIND_LABELS = {
    "linkPreviewTypeVideo": "Video", "linkPreviewTypeEmbeddedVideoPlayer": "Video",
    "linkPreviewTypeExternalVideo": "Video", "linkPreviewTypeAnimation": "GIF",
    "linkPreviewTypeAudio": "Audio", "linkPreviewTypeEmbeddedAudioPlayer": "Audio",
    "linkPreviewTypeDocument": "File", "linkPreviewTypeSticker": "Sticker",
    "linkPreviewTypeStickerSet": "Sticker set", "linkPreviewTypeUser": "Person",
    "linkPreviewTypeChat": "Chat", "linkPreviewTypeMessage": "Message",
    "linkPreviewTypeVoiceNote": "Voice message", "linkPreviewTypeVideoNote": "Video message",
}


@dataclass
class LinkPreview:
    url: str
    site: str = ""
    title: str = ""
    description: str = ""
    image: dict[str, Any] | None = None  # TDLib file to show (photo size, thumbnail)
    width: int = 0
    height: int = 0
    minithumbnail: bytes | None = None
    large: bool = False  # the image goes full width under the text, not a square on the side
    label: str = ""  # "Video · 3:20" etc. for media links


def message_link_preview(content: dict[str, Any]) -> LinkPreview | None:
    if content.get("@type") != "messageText":
        return None
    return parse(content.get("link_preview") or content.get("web_page"))


def parse(raw: dict[str, Any] | None) -> LinkPreview | None:
    if not raw or not raw.get("url"):
        return None
    description = raw.get("description") or ""
    if isinstance(description, dict):
        description = description.get("text", "")
    description = " ".join(description.split())
    if len(description) > MAX_DESCRIPTION:
        description = description[:MAX_DESCRIPTION].rstrip() + "…"
    kind = raw.get("type") or {}
    preview = LinkPreview(
        url=raw["url"], site=raw.get("site_name", "") or raw.get("display_url", ""),
        title=raw.get("title", "") or raw.get("author", ""), description=description,
        large=bool(raw.get("show_large_media", raw.get("has_large_media", False))),
    )
    _set_image(preview, kind if kind.get("@type") else raw)  # older TDLib: photo on webPage
    label = _KIND_LABELS.get(kind.get("@type", ""), "")
    duration = _duration(kind)
    preview.label = f"{label} · {duration_text(duration)}" if label and duration else label
    if not (preview.title or preview.description or preview.image):
        return None  # nothing to show beyond the link itself
    return preview


def _set_image(preview: LinkPreview, kind: dict[str, Any]) -> None:
    for key in ("photo", "cover", "thumbnail"):
        photo = kind.get(key)
        if isinstance(photo, dict) and photo.get("sizes"):
            media = _photo(photo)
            preview.image, preview.width, preview.height = media.preview, media.width, media.height
            preview.minithumbnail = media.minithumbnail
            return
    for key in ("video", "animation", "document", "audio", "sticker", "video_note"):
        item = kind.get(key)
        if not isinstance(item, dict):
            continue
        thumbnail = item.get("thumbnail") or item.get("album_cover_thumbnail")
        image = _thumbnail_file(thumbnail)
        if image is not None:
            preview.image = image
            preview.width = (thumbnail or {}).get("width", 0) or item.get("width", 0)
            preview.height = (thumbnail or {}).get("height", 0) or item.get("height", 0)
            preview.minithumbnail = _minithumbnail(item.get("minithumbnail"))
            return
    photo = kind.get("photo")  # chatPhoto of a user or chat link: small square
    if isinstance(photo, dict) and isinstance(photo.get("small"), dict):
        preview.image, preview.width, preview.height = photo["small"], 160, 160
        preview.large = False


def _duration(kind: dict[str, Any]) -> int:
    if kind.get("duration"):
        return int(kind["duration"])
    for key in ("video", "audio", "animation", "voice_note", "video_note"):
        item = kind.get(key)
        if isinstance(item, dict) and item.get("duration"):
            return int(item["duration"])
    return 0

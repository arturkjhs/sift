"""Extract what the UI needs to show a message's media, independent of Qt.

kind:    photo | video | animation | videoNote | sticker | document | audio | voice | ""
file:    the main file (what "open"/"play" acts on)
preview: an image file to show inline (photo size, static sticker, thumbnail), if any
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any

_IMAGE_THUMBNAILS = {
    "thumbnailFormatJpeg", "thumbnailFormatPng", "thumbnailFormatWebp", "thumbnailFormatGif",
}
_PHOTO_MAX_SIDE = 1280  # good enough for inline display; the largest size is used for "open"


@dataclass
class Media:
    kind: str
    file: dict[str, Any] | None = None
    preview: dict[str, Any] | None = None
    width: int = 0
    height: int = 0
    minithumbnail: bytes | None = None
    duration: int = 0
    file_name: str = ""
    mime_type: str = ""
    title: str = ""
    waveform: bytes = b""
    emoji: str = ""


def extract(content: dict[str, Any]) -> Media | None:
    match content.get("@type"):
        case "messagePhoto":
            return _photo(content.get("photo") or {})
        case "messageVideo":
            return _video("video", content.get("video") or {}, "video")
        case "messageAnimation":
            return _video("animation", content.get("animation") or {}, "animation")
        case "messageVideoNote":
            note = content.get("video_note") or {}
            media = _video("videoNote", note, "video")
            media.width = media.height = note.get("length", 0) or 240
            return media
        case "messageSticker":
            return _sticker(content.get("sticker") or {})
        case "messageDocument":
            doc = content.get("document") or {}
            return Media(
                kind="document", file=doc.get("document"),
                preview=_thumbnail_file(doc.get("thumbnail")),
                minithumbnail=_minithumbnail(doc.get("minithumbnail")),
                file_name=doc.get("file_name", ""), mime_type=doc.get("mime_type", ""),
            )
        case "messageAudio":
            audio = content.get("audio") or {}
            performer, title = audio.get("performer", ""), audio.get("title", "")
            return Media(
                kind="audio", file=audio.get("audio"), duration=audio.get("duration", 0),
                file_name=audio.get("file_name", ""), mime_type=audio.get("mime_type", ""),
                title=" – ".join(p for p in (performer, title) if p),
            )
        case "messageVoiceNote":
            voice = content.get("voice_note") or {}
            return Media(
                kind="voice", file=voice.get("voice"), duration=voice.get("duration", 0),
                mime_type=voice.get("mime_type", ""), waveform=_bytes(voice.get("waveform")),
            )
    return None


def _photo(photo: dict[str, Any]) -> Media:
    sizes = sorted(
        (s for s in photo.get("sizes", []) if s.get("photo")),
        key=lambda s: max(s.get("width", 0), s.get("height", 0)),
    )
    if not sizes:
        return Media(kind="photo", minithumbnail=_minithumbnail(photo.get("minithumbnail")))
    fitting = [s for s in sizes if max(s["width"], s["height"]) <= _PHOTO_MAX_SIDE]
    display = fitting[-1] if fitting else sizes[0]
    return Media(
        kind="photo", file=sizes[-1]["photo"], preview=display["photo"],
        width=display.get("width", 0), height=display.get("height", 0),
        minithumbnail=_minithumbnail(photo.get("minithumbnail")),
    )


def _video(kind: str, video: dict[str, Any], file_key: str) -> Media:
    thumbnail = video.get("thumbnail") or {}
    return Media(
        kind=kind, file=video.get(file_key), preview=_thumbnail_file(thumbnail),
        width=video.get("width", 0) or thumbnail.get("width", 0),
        height=video.get("height", 0) or thumbnail.get("height", 0),
        minithumbnail=_minithumbnail(video.get("minithumbnail")),
        duration=video.get("duration", 0), file_name=video.get("file_name", ""),
        mime_type=video.get("mime_type", ""),
    )


def _sticker(sticker: dict[str, Any]) -> Media:
    is_static = (sticker.get("format") or {}).get("@type") == "stickerFormatWebp"
    # Animated stickers (TGS/WebM) are shown by their static thumbnail for now.
    preview = sticker.get("sticker") if is_static else _thumbnail_file(sticker.get("thumbnail"))
    return Media(
        kind="sticker", file=sticker.get("sticker"), preview=preview,
        width=sticker.get("width", 512) or 512, height=sticker.get("height", 512) or 512,
        emoji=sticker.get("emoji", ""),
    )


def _thumbnail_file(thumbnail: dict[str, Any] | None) -> dict[str, Any] | None:
    if not thumbnail or (thumbnail.get("format") or {}).get("@type") not in _IMAGE_THUMBNAILS:
        return None
    return thumbnail.get("file")


def _minithumbnail(mini: dict[str, Any] | None) -> bytes | None:
    data = _bytes((mini or {}).get("data"))
    return data or None


def _bytes(value: Any) -> bytes:
    """TDLib JSON encodes `bytes` fields as base64 strings."""
    if not value:
        return b""
    try:
        return base64.b64decode(value)
    except (binascii.Error, ValueError, TypeError):
        return b""


def decode_waveform(data: bytes, bars: int = 40) -> list[float]:
    """Telegram packs 5-bit amplitude samples little-endian; resample to `bars` values in 0..1."""
    count = len(data) * 8 // 5
    samples = []
    for i in range(count):
        bit = i * 5
        chunk = int.from_bytes(data[bit // 8 : bit // 8 + 2].ljust(2, b"\0"), "little")
        samples.append((chunk >> (bit % 8)) & 31)
    if not samples:
        return [0.15] * bars
    resampled = []
    for b in range(bars):
        lo = b * len(samples) // bars
        hi = max(lo + 1, (b + 1) * len(samples) // bars)
        resampled.append(max(samples[lo:hi]))
    peak = max(resampled) or 1
    return [max(0.12, v / peak) for v in resampled]


def fit(width: int, height: int, max_width: int, max_height: int,
        min_side: int = 80) -> tuple[int, int]:
    """Scale (width, height) to fit the box, keeping aspect; tiny/unknown sizes get a sane box."""
    if width <= 0 or height <= 0:
        return max_width, max_width * 3 // 4
    scale = min(max_width / width, max_height / height)
    w, h = round(width * scale), round(height * scale)
    return max(w, min_side), max(h, min_side // 2)


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def duration_text(seconds: int) -> str:
    minutes, secs = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"

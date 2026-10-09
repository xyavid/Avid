"""The single vocabulary and rule set for image parts in message content.

Part shape is ``{"type": "image", "mime", "bytes", "data"(base64), "name"?}``; MIME comes from
magic bytes, never the client's claim. Plain text content stays a str; an image renders as one
marker line at a fixed character cost, so base64 never reaches summaries or the index, and bytes
are only validated, never re-encoded (the kernel's runtime deps stay httpx-only).
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterable, Mapping
from typing import Any

#: Types all three protocols accept; anything else is rejected (svg is a script carrier).
ALLOWED_MIMES = ("image/png", "image/jpeg", "image/webp", "image/gif")

#: One image: fits an uncompressed phone photo without pushing a session line to tens of MB.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
#: Images per message: more than this should be several messages.
MAX_IMAGES_PER_MESSAGE = 8
#: Per-message image total: leaves body room under Anthropic's ~32MB request cap.
MAX_TOTAL_BYTES = 16 * 1024 * 1024
#: Wire-format base64 cap: coarse pre-filter, looser than the byte rule so the 400 comes from here.
MAX_BASE64_CHARS = 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 1_000_000
#: Display cap for file names; they label markers and the UI, never locate anything.
MAX_NAME_CHARS = 200
#: Character cost of one image (~1600 tokens): fixed, since the budget counts characters, not bytes.
IMAGE_CHAR_COST = 6_000

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class AttachmentError(ValueError):
    """An invalid attachment; its message is user-facing and may enter a 400 response."""


def sniff_mime(data: bytes) -> str | None:
    """Identify the type from magic bytes; None means unknown, and callers must reject it."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    # RIFF is a container: only WEBP qualifies, WAVE/AVI start with it too.
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_part(data: bytes, *, name: str | None = None) -> dict[str, Any]:
    """Collect bytes into one image part; AttachmentError when oversize or unrecognized."""
    if len(data) > MAX_IMAGE_BYTES:
        raise AttachmentError(
            f"图片大于 {human_bytes(MAX_IMAGE_BYTES)}（这张 {human_bytes(len(data))}）"
        )
    mime = sniff_mime(data)
    if mime is None:
        raise AttachmentError("只收 png / jpeg / webp / gif 图片（按文件头判断）")
    part: dict[str, Any] = {
        "type": "image",
        "mime": mime,
        "bytes": len(data),
        "data": base64.b64encode(data).decode("ascii"),
    }
    cleaned = clean_name(name)
    if cleaned is not None:
        part["name"] = cleaned
    return part


def build_user_content(
    text: str, images: Iterable[Mapping[str, Any]]
) -> str | list[dict[str, Any]]:
    """Wire-format input (text + images) to stored content; without images the text stays a str."""
    payloads = list(images)
    if not payloads:
        return text
    if len(payloads) > MAX_IMAGES_PER_MESSAGE:
        raise AttachmentError(
            f"一条消息最多 {MAX_IMAGES_PER_MESSAGE} 张图，收到 {len(payloads)} 张"
        )
    parts: list[dict[str, Any]] = []
    if text.strip():
        parts.append({"type": "text", "text": text})
    total = 0
    for index, item in enumerate(payloads, start=1):
        raw = _decode(item.get("data"), index)
        if not raw:
            raise AttachmentError(f"第 {index} 张图是空的（0 字节）")
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise AttachmentError(
                f"一条消息的图片合计最多 {human_bytes(MAX_TOTAL_BYTES)}"
                f"（前 {index} 张已经 {human_bytes(total)}）"
            )
        parts.append(image_part(raw, name=_optional_str(item.get("name"))))
    return parts


def check_content(content: Any) -> str | None:
    """Last gate before disk: a problem description, or None when every stored part renders."""
    if content is None or isinstance(content, str):
        return None
    if not isinstance(content, (list, tuple)):
        return f"消息内容只能是字符串或分块数组，拿到 {type(content).__name__}"
    if not content:
        return "消息内容的分块数组是空的"
    images = 0
    total = 0
    for index, part in enumerate(content, start=1):
        if not isinstance(part, Mapping):
            return f"第 {index} 个内容块不是对象"
        kind = part.get("type")
        if kind == "image":
            problem = _check_image(part, index)
            if problem is not None:
                return problem
            images += 1
            total += int(part.get("bytes") or 0)
        elif kind == "text":
            if not isinstance(part.get("text"), str):
                return f"第 {index} 个文本块缺 text"
        elif isinstance(part.get("text"), str):
            continue  # Other providers' text blocks (output_text etc.): accept any text string
        else:
            return f"第 {index} 个内容块的类型不认识：{kind!r}"
    if images > MAX_IMAGES_PER_MESSAGE:
        return f"一条消息最多 {MAX_IMAGES_PER_MESSAGE} 张图，收到 {images} 张"
    if total > MAX_TOTAL_BYTES:
        return f"一条消息的图片合计最多 {human_bytes(MAX_TOTAL_BYTES)}"
    return None


def render_content_text(content: Any) -> str:
    """Content to text for summaries, the index and hooks: images become one marker line."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, (list, tuple)):
        return ""
    pieces: list[str] = []
    for part in content:
        if not isinstance(part, Mapping):
            continue
        if is_image_part(part):
            pieces.append(_image_marker(part))
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            pieces.append(text)
    return "\n".join(pieces)


def content_chars(content: Any) -> int:
    """Character cost of content: text by length, images at a fixed cost."""
    if isinstance(content, str):
        return len(content)
    if content is None or not isinstance(content, (list, tuple)):
        return 0
    total = 0
    for part in content:
        if not isinstance(part, Mapping):
            continue
        if is_image_part(part):
            total += IMAGE_CHAR_COST
            continue
        text = part.get("text")
        if isinstance(text, str):
            total += len(text)
    return total


def is_image_part(part: Any) -> bool:
    return isinstance(part, Mapping) and part.get("type") == "image"


def image_bytes(part: Any) -> bytes | None:
    """Raw bytes of a part; None when it is not an image part or its base64 is broken."""
    if not is_image_part(part):
        return None
    data = part.get("data")
    if not isinstance(data, str):
        return None
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None


def image_count(content: Any) -> int:
    if not isinstance(content, (list, tuple)):
        return 0
    return sum(1 for part in content if is_image_part(part))


def ref_of(part: Mapping[str, Any], index: int) -> dict[str, Any]:
    """Image part to wire-format ref: bytes dropped, ``index`` kept for the byte-read endpoint."""
    ref: dict[str, Any] = {
        "type": "image",
        "mime": part.get("mime"),
        "bytes": part.get("bytes"),
        "index": index,
    }
    name = part.get("name")
    if isinstance(name, str) and name:
        ref["name"] = name
    return ref


def strip_message_bytes(message: dict[str, Any]) -> dict[str, Any]:
    """Message to wire form: image parts become refs; messages without images pass through."""
    content = message.get("content")
    if not isinstance(content, (list, tuple)) or not any(
        is_image_part(part) for part in content
    ):
        return message
    return {
        **message,
        "content": [
            ref_of(part, index) if is_image_part(part) else part
            for index, part in enumerate(content)
        ],
    }


def clean_name(name: Any) -> str | None:
    """File names are display-only: collapsed to one line, control chars stripped, capped."""
    if not isinstance(name, str):
        return None
    collapsed = " ".join(_CONTROL_CHARS.sub(" ", name).split())[:MAX_NAME_CHARS].strip()
    return collapsed or None


def human_bytes(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size // 1024}KB"
    return f"{size / (1024 * 1024):.1f}MB"


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _check_image(part: Mapping[str, Any], index: int) -> str | None:
    mime = part.get("mime")
    if mime not in ALLOWED_MIMES:
        return f"第 {index} 张图的类型不认：{mime!r}（只收 {'/'.join(ALLOWED_MIMES)}）"
    raw = image_bytes(part)
    if raw is None:
        return f"第 {index} 张图的 base64 解不开"
    claimed = part.get("bytes")
    if claimed is not None and claimed != len(raw):
        return f"第 {index} 张图的字节数与 base64 长度不符：{claimed} vs {len(raw)}"
    if len(raw) > MAX_IMAGE_BYTES:
        return f"第 {index} 张图大于 {human_bytes(MAX_IMAGE_BYTES)}"
    return None


def _image_marker(part: Mapping[str, Any]) -> str:
    bits = ["图片"]
    name = part.get("name")
    if isinstance(name, str) and name:
        bits.append(name)
    mime = part.get("mime")
    if isinstance(mime, str) and mime:
        bits.append(mime)
    size = part.get("bytes")
    if isinstance(size, int):
        bits.append(human_bytes(size))
    return "[" + " ".join(bits) + "]"


def _decode(value: Any, index: int) -> bytes:
    if not isinstance(value, str):
        raise AttachmentError(f"第 {index} 张图缺 base64 数据")
    try:
        return base64.b64decode("".join(value.split()), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AttachmentError(f"第 {index} 张图的 base64 解不开：{exc}") from exc


__all__ = [
    "ALLOWED_MIMES",
    "IMAGE_CHAR_COST",
    "MAX_BASE64_CHARS",
    "MAX_IMAGE_BYTES",
    "MAX_IMAGES_PER_MESSAGE",
    "MAX_NAME_CHARS",
    "MAX_TOTAL_BYTES",
    "AttachmentError",
    "build_user_content",
    "check_content",
    "clean_name",
    "content_chars",
    "human_bytes",
    "image_bytes",
    "image_count",
    "image_part",
    "is_image_part",
    "ref_of",
    "render_content_text",
    "sniff_mime",
    "strip_message_bytes",
]

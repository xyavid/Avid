"""图片附件：唯一一份词汇与规则。

图片是**消息内容**的一种分块（part），不是另一种消息。所以这里只管三件事，
而且只管这三件：

1. **形状**：`{"type": "image", "mime", "name"?, "bytes", "data"(base64)}`。
   存储形态是中立的——`image_url` / `source.base64` / `input_image` 这些 wire 形状
   只活在 `providers/` 的三个适配器里，换 provider 不动历史。
2. **合法性**：按字节判型（不看客户端声明），三档上限（单图 / 单条张数 / 单条合计）。
   入口（web 路由）与会话落盘前（`session.validate_message`）各过一道——
   最后一道在写盘之前，所以日志里永远没有渲染不出来的块。
3. **退化**：把内容渲染成文本（摘要、索引、hook 共用），以及把消息投影成线格式
   （图片块换成 ref，字节另走端点）。

两条口径值得单独记下来，因为它们是**成本与检索**的地基：

- 图片在「字符」口径下按固定成本计（``IMAGE_CHAR_COST``）。base64 长度是字节的
  4/3，与 token 量没有关系；按它计，一张 1568px 截图（约 40 万 base64 字符）会把
  压缩阈值直接打爆，而它在模型那边只值约 1600 token。
- 渲染出的是一行标记（含文件名与类型），既让「哪个会话里有截图」搜得到，
  又保证 base64 永不进摘要请求与索引。

纯文本消息**不变成数组**（`content` 仍是 str）：老会话零迁移，绝大多数路径零分支。

为什么不做服务端重编码：那要引 Pillow（内核运行期依赖只有 httpx 这条线要守），
而客户端本来就拿得到原图。缩放交给浏览器在超限时做，服务端只判合法性；
真出现「某家只收 JPEG」这类硬约束时，再在 prepare 层加转换——那时它才有依据。
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterable, Mapping
from typing import Any

#: 三家协议都认的图片类型；不在表里的一律拒（含 svg：它是脚本载体，不是图片）。
ALLOWED_MIMES = ("image/png", "image/jpeg", "image/webp", "image/gif")

#: 单图上限：够放一张未压缩的手机照片，同时不把一条会话行撑到几十 MB。
MAX_IMAGE_BYTES = 8 * 1024 * 1024
#: 单条消息的图片张数：再多就该分几条发（也避免一次请求把上下文挤满）。
MAX_IMAGES_PER_MESSAGE = 8
#: 单条消息的图片合计：给 Anthropic 约 32MB 的单请求上限留出正文余量。
MAX_TOTAL_BYTES = 16 * 1024 * 1024
#: 线格式的 base64 长度上限：**粗筛**，不是规则本身（规则是按字节判的单图上限）。
#: 留 1MB 余量，好让「稍微超一点」的图走到 attachments 的 400（带中文原因），
#: 而不是在 pydantic 那里变成一句 schema 不符。
MAX_BASE64_CHARS = 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 1_000_000
#: 文件名的显示上限；它只用于标记与界面，不参与定位。
MAX_NAME_CHARS = 200
#: 图片的字符成本（≈1600 token）：压缩触发线与用量环的货币是「字符」。
IMAGE_CHAR_COST = 6_000

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class AttachmentError(ValueError):
    """一条附件不合法；消息是给人看的，可以直接进 400 响应。"""


def sniff_mime(data: bytes) -> str | None:
    """按文件头判型；认不出返回 None（调用方拒收，不信客户端的声明）。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    # RIFF 是容器：只有 WEBP 那种才认，WAVE/AVI 同样以 RIFF 开头。
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_part(data: bytes, *, name: str | None = None) -> dict[str, Any]:
    """把字节收成一块图片；超限或类型不认就抛 AttachmentError。"""
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
    """线格式的输入（文本 + 图片数组）→ 存储形态的内容。

    没有图片就原样返回字符串——纯文本消息的形态不变。有图片时按「文本在前、
    图片按用户给的顺序在后」拼成分块数组。
    """
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
    """落盘前的形状校验：返回问题描述，None 表示合法。

    判据是「存下来的块一定能渲染」：类型认识、图片字段自洽（声明的字节数与
    base64 实际长度相符）、三档上限不超。
    """
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
            continue  # 别家的文本块形状（output_text 等）：有 text 就认，渲染时取它
        else:
            return f"第 {index} 个内容块的类型不认识：{kind!r}"
    if images > MAX_IMAGES_PER_MESSAGE:
        return f"一条消息最多 {MAX_IMAGES_PER_MESSAGE} 张图，收到 {images} 张"
    if total > MAX_TOTAL_BYTES:
        return f"一条消息的图片合计最多 {human_bytes(MAX_TOTAL_BYTES)}"
    return None


def render_content_text(content: Any) -> str:
    """内容 → 文本（摘要、索引、hook 的 ``prompt`` 字段共用）。

    文本块按出现顺序换行拼接；图片块渲染成一行标记；别家形状的块取 ``text``；
    不认识的块静默跳过（它没有任何可渲染的语义，但不该让整条路径炸掉）。
    """
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
    """内容的字符成本：文本按字面长度，图片按固定成本（见模块注释）。"""
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
    """取回块里的原始字节；不是图片块或 base64 坏了都返回 None。"""
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
    """图片块 → 线格式 ref：去掉字节，留下显示与定位用的事实。

    ``index`` 是该块在内容数组里的下标——读侧端点靠 (会话, 条目, 下标) 定位字节。
    """
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
    """消息 → 线格式消息：图片块换成 ref，其余原样。无图的消息返回同一个对象。"""
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
    """文件名只用于显示与标记：压成一行、去掉控制字符、封顶长度。"""
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

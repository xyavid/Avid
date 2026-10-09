"""附件词汇（avid/attachments.py）的单元测试：先于实现编写（§6 约定）。

这个模块是「图片附件」的唯一规则处，所以用例按规则分类，而不是按函数：

* 判型：只看字节（魔数），不看客户端声明；
* 上限：单图字节 / 单条张数 / 单条合计，三档都在入口挡住；
* 渲染：任何路径把内容变成文本，图片只留一行标记，base64 永不出现；
* 成本：按固定字符成本计入（base64 长度不是 token 量）；
* 线格式：读侧 ref 去掉字节但保留显示事实，纯文本消息原样返回。

图片字节用真的 PNG/JPEG/WebP/GIF 头拼出来（不引 Pillow，也不依赖外部文件）。
"""

from __future__ import annotations

import base64

import pytest

from avid import attachments
from avid.attachments import AttachmentError

# 四种允许格式的最小可判头部（只看魔数，后面补零即可）。
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF" + (40).to_bytes(4, "little") + b"WEBP" + b"\x00" * 32


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def wire_image(data: bytes = PNG, name: str | None = "shot.png") -> dict:
    return {"name": name, "data": b64(data)}


# ---- 判型 ----


def test_sniff_mime_recognizes_the_four_allowed_types():
    assert attachments.sniff_mime(PNG) == "image/png"
    assert attachments.sniff_mime(JPEG) == "image/jpeg"
    assert attachments.sniff_mime(GIF) == "image/gif"
    assert attachments.sniff_mime(WEBP) == "image/webp"


def test_sniff_mime_rejects_unknown_bytes():
    assert attachments.sniff_mime(b"not an image at all") is None
    assert attachments.sniff_mime(b"") is None
    # RIFF 家族的近亲（WebP 之外）不能混进来
    assert attachments.sniff_mime(b"RIFF" + (40).to_bytes(4, "little") + b"WAVE") is None


# ---- 构造 ----


def test_image_part_carries_mime_size_and_base64():
    part = attachments.image_part(PNG, name="shot.png")
    assert part["type"] == "image"
    assert part["mime"] == "image/png"
    assert part["name"] == "shot.png"
    assert part["bytes"] == len(PNG)
    assert part["data"] == b64(PNG)


def test_image_part_omits_a_missing_name():
    part = attachments.image_part(PNG)
    assert "name" not in part


def test_image_part_collapses_control_characters_in_the_name():
    part = attachments.image_part(PNG, name="  shot\n.png\t")
    assert part["name"] == "shot .png"


def test_image_part_rejects_bytes_over_the_per_image_cap():
    big = PNG + b"\x00" * attachments.MAX_IMAGE_BYTES
    with pytest.raises(AttachmentError, match="大于"):
        attachments.image_part(big, name="huge.png")


def test_image_part_rejects_a_foreign_type():
    with pytest.raises(AttachmentError, match="只收"):
        attachments.image_part(b"BM" + b"\x00" * 32, name="bmp.bmp")


# ---- 组装一条用户内容 ----


def test_build_user_content_keeps_plain_text_as_a_string():
    assert attachments.build_user_content("看这个", []) == "看这个"


def test_build_user_content_orders_text_before_images():
    content = attachments.build_user_content("看这两张", [wire_image(PNG), wire_image(GIF, "b.gif")])
    assert isinstance(content, list)
    assert [part["type"] for part in content] == ["text", "image", "image"]
    assert content[0]["text"] == "看这两张"
    assert content[2]["mime"] == "image/gif"


def test_build_user_content_allows_an_image_only_message():
    content = attachments.build_user_content("", [wire_image()])
    assert isinstance(content, list)
    assert [part["type"] for part in content] == ["image"]


def test_build_user_content_rejects_more_than_the_image_cap():
    images = [wire_image() for _ in range(attachments.MAX_IMAGES_PER_MESSAGE + 1)]
    with pytest.raises(AttachmentError, match="最多"):
        attachments.build_user_content("太多", images)


def test_build_user_content_rejects_a_total_over_the_cap():
    # 每张都在单图上限内，但合起来超过单条上限
    each = attachments.MAX_IMAGE_BYTES
    count = attachments.MAX_TOTAL_BYTES // each + 1
    payload = PNG + b"\x00" * (each - len(PNG))
    images = [{"name": f"{i}.png", "data": b64(payload)} for i in range(count)]
    with pytest.raises(AttachmentError, match="合计"):
        attachments.build_user_content("太沉", images)


def test_build_user_content_rejects_bad_base64():
    with pytest.raises(AttachmentError, match="base64"):
        attachments.build_user_content("坏的", [{"name": "x.png", "data": "!!!not-base64!!!"}])


def test_build_user_content_rejects_an_empty_payload():
    with pytest.raises(AttachmentError, match="空"):
        attachments.build_user_content("空的", [{"name": "x.png", "data": ""}])


# ---- 落盘前的形状校验 ----


def test_check_content_accepts_strings_parts_and_none():
    assert attachments.check_content("普通文本") is None
    assert attachments.check_content(None) is None
    part = attachments.image_part(PNG)
    assert attachments.check_content([{"type": "text", "text": "看"}, part]) is None


def test_check_content_rejects_a_lying_byte_count():
    part = attachments.image_part(PNG)
    part["bytes"] = part["bytes"] + 1
    message = attachments.check_content([part])
    assert message is not None and "字节数" in message


def test_check_content_rejects_an_unknown_part_type():
    message = attachments.check_content([{"type": "audio", "data": "x"}])
    assert message is not None and "audio" in message


def test_check_content_rejects_a_foreign_mime():
    part = attachments.image_part(PNG)
    part["mime"] = "image/tiff"
    message = attachments.check_content([part])
    assert message is not None and "tiff" in message


def test_check_content_rejects_a_text_part_without_text():
    message = attachments.check_content([{"type": "text"}])
    assert message is not None


# ---- 渲染成文本（摘要、索引、hook 共用） ----


def test_render_content_text_passes_plain_strings_through():
    assert attachments.render_content_text("就是这句话") == "就是这句话"


def test_render_content_text_marks_images_without_base64():
    part = attachments.image_part(PNG, name="shot.png")
    text = attachments.render_content_text([{"type": "text", "text": "看这个"}, part])
    assert "看这个" in text
    assert "shot.png" in text and "image/png" in text
    assert part["data"] not in text
    assert len(text) < 200  # 标记是一行，不是把字节摊开


def test_render_content_text_accepts_foreign_text_parts():
    # 有些 provider 的响应块是 output_text 之类；取 text 字段，别渲染成标记
    assert attachments.render_content_text([{"type": "output_text", "text": "模型说的话"}]) == "模型说的话"


def test_render_content_text_ignores_unknown_parts_silently():
    assert attachments.render_content_text([{"type": "weird", "payload": 1}]) == ""


# ---- 成本口径 ----


def test_content_chars_counts_text_literally():
    assert attachments.content_chars("四个字符") == 4
    assert attachments.content_chars(None) == 0


def test_content_chars_counts_an_image_at_the_fixed_cost():
    part = attachments.image_part(PNG + b"\x00" * 100_000)
    cost = attachments.content_chars([{"type": "text", "text": "短"}, part])
    assert cost == 1 + attachments.IMAGE_CHAR_COST
    assert cost < part["bytes"]  # base64 长度绝不出现在成本里


# ---- 线格式：读侧 ref ----


def test_ref_of_drops_bytes_but_keeps_display_facts():
    part = attachments.image_part(PNG, name="shot.png")
    ref = attachments.ref_of(part, 2)
    assert ref == {
        "type": "image",
        "mime": "image/png",
        "name": "shot.png",
        "bytes": len(PNG),
        "index": 2,
    }
    assert "data" not in ref


def test_strip_message_bytes_leaves_text_only_messages_identical():
    message = {"role": "user", "content": "一句话"}
    assert attachments.strip_message_bytes(message) is message


def test_strip_message_bytes_replaces_image_parts_with_refs():
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "看"},
            attachments.image_part(PNG, name="a.png"),
            attachments.image_part(GIF, name="b.gif"),
        ],
    }
    wire = attachments.strip_message_bytes(message)
    assert wire["content"][0] == {"type": "text", "text": "看"}
    assert wire["content"][1]["index"] == 1
    assert wire["content"][2]["index"] == 2
    assert "data" not in wire["content"][1]
    # 原消息不变：ref 是给线的投影，不是就地改写
    assert "data" in message["content"][1]


def test_image_bytes_roundtrips():
    part = attachments.image_part(PNG, name="a.png")
    assert attachments.image_bytes(part) == PNG
    assert attachments.image_bytes({"type": "text", "text": "x"}) is None

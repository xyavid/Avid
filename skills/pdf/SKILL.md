---
name: pdf
description: 读取与提取 PDF 内容——先判断有没有文本层，再决定用解析还是 OCR
---

# 处理 PDF

## 第一步：先判断这份 PDF 属于哪一类

```bash
python -c "
import sys
try:
    import pypdf
except ImportError:
    sys.exit('缺依赖：uv add pypdf')
r = pypdf.PdfReader('文件.pdf')
text = '\n'.join((p.extract_text() or '') for p in r.pages[:3])
print('页数:', len(r.pages))
print('前三页文本长度:', len(text.strip()))
"
```

- **文本长度接近 0** → 扫描件，没有文本层，走 OCR。
- **文本长度正常** → 有文本层，直接抽取，不要上 OCR（慢且会引入错字）。

## 有文本层：抽取

- 按页抽，保留页码，方便回答"第几页说了什么"。
- 表格用 `pdfplumber` 的 `extract_tables()`，比正则可靠。
- 抽出后先统计总字符数：超过上下文预算就分批读，别一次性全塞进来。

## 扫描件：OCR

1. `pdftoppm -r 300 -png 文件.pdf 页` 转图（`poppler-utils`）。
2. `tesseract 页-1.png out -l chi_sim+eng`。
3. OCR 结果**必须抽样核对**——数字、专有名词、表格最常出错，不要在没核对的情况下当成原文引用。

## 常见坑

- **加密 PDF**：`pypdf` 会抛异常，先用 `reader.is_encrypted` 判断。
- **排版错乱**：多栏文档按页抽取会串行，按栏坐标抽。
- **页码偏移**：PDF 内部页码 ≠ 印刷页码，引用时说明用的是哪个。
- **不要凭文件大小判断内容量**：图片扫描件很大但几乎没有文本。

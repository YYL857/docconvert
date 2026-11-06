# -*- coding: utf-8 -*-
"""链式转换 —— 两段式兜底，救「LibreOffice 打不开但内容完好」的文档。

为什么需要这个模块
------------------
腾讯文档（Tencent office）/ WPS 导出的 docx，`word/styles.xml` 可能只有一个空标签：

    <w:styles xmlns:w="..."/>

文件本身完全正常（python-docx 能读出 24 段落 11 个表格，XML 也合法），
但 LibreOffice 加载时直接报 `source file could not be loaded`。
强制 `--infilter=writer_MS_Word_2007_XML` 也救不回来。

Pandoc 对残缺 docx 的容错性远好于 LO，所以降级走两段：

    docx --(pandoc)--> html --(libreoffice)--> pdf

代价是版式（页边距、页眉页脚、精确字号）会损失一部分 —— 必须在 warn 里如实说明，
不能让用户以为拿到的是原样排版。
"""
import os
import re
import shutil
import tempfile
from pathlib import Path

from .utils import ConvResult

# 走 html 中转能保住内容的组合；未列出的不尝试，避免瞎折腾
CHAIN_SRC = ("docx", "doc", "odt", "rtf", "wps")
CHAIN_DST = ("pdf", "png", "jpg", "jpeg", "docx", "odt", "rtf")

# 内联到每个表格元素上的样式。
#
# 🔴 LibreOffice 的 HTML 导入器**不解析 <style> 块里的选择器规则**。
# 实测同一张 2x2 表：
#     <style>td{border:1px solid #000}</style>   → PDF 里 0 条线
#     <td style="border:1px solid #000">         → PDF 里 6 条线
# 所以必须逐个元素注入。指望样式表，得到的就是一张「只有文字对齐、
# 没有格子线」的表 —— 表单类文档这样等于废掉。
_TAG_STYLE = {
    "table": "border-collapse:collapse;margin:8px 0",
    "td": "border:1px solid #000;padding:3px 5px;vertical-align:top",
    "th": "border:1px solid #000;padding:3px 5px;background:#f2f2f2;font-weight:bold",
}

BODY_STYLE = ("font-family:'Microsoft YaHei','SimSun',sans-serif;"
              "font-size:10.5pt;line-height:1.55;color:#000")

HTML_CSS = """
@page { size: A4; margin: 18mm 15mm; }
body { font-family: "Microsoft YaHei", "SimSun", sans-serif;
       font-size: 10.5pt; line-height: 1.55; color: #000; }
p { margin: 4px 0; }
table { border-collapse: collapse; margin: 8px 0; }
td, th { border: 1px solid #000; padding: 3px 5px; vertical-align: top; }
th { background: #f2f2f2; font-weight: bold; }
hr { border: 0; border-top: 1px solid #000; margin: 6px 0; }
img { max-width: 100%; }
strong { font-weight: bold; }
"""

HTML_TPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{title}</title>
<style>{css}</style>
</head><body style="{body_style}">
{body}
</body></html>
"""


def _inline_table_borders(html):
    """把表格边框写成每个元素的内联 style（LO 只认这种）。"""
    for tag, style in _TAG_STYLE.items():
        pattern = re.compile(rf"<{tag}((?:\s[^>]*)?)>", re.I)

        def repl(m, _tag=tag, _style=style):
            attrs = m.group(1) or ""
            found = re.search(r'style\s*=\s*"([^"]*)"', attrs, re.I)
            if found:
                merged = found.group(1).rstrip("; ") + ";" + _style
                attrs = attrs[:found.start()] + f'style="{merged}"' + attrs[found.end():]
            else:
                attrs = attrs + f' style="{_style}"'
            # 🔴 LO 不认 CSS 的 width（实测 style="width:100%" 完全无效，
            #    表格仍按内容收缩到 5% 页宽），只认传统 HTML 属性。
            if _tag == "table" and not re.search(r"\bwidth\s*=", attrs, re.I):
                attrs = ' width="100%"' + attrs
            return f"<{_tag}{attrs}>"

        html = pattern.sub(repl, html)
    return html


def _center_title(html):
    """把开头的加粗单段当作标题并居中。

    pandoc 的 docx→html 不保留段落对齐方式，原文居中的标题会变成左对齐。
    这类表单文档的标题几乎都是「第一段 + 整段加粗」，所以只处理这一种情况：
    位置在开头 400 字符内、整段只有一个 <strong>。不做更激进的推断，
    免得把正文里的强调句也居中。
    """
    m = re.search(r"<p>\s*<strong>(.*?)</strong>\s*</p>", html, re.S)
    if not m or m.start() > 400:
        return html
    styled = (f'<p style="text-align:center;font-size:16pt;font-weight:bold;'
              f'margin:12px 0 8px">{m.group(1)}</p>')
    return html[:m.start()] + styled + html[m.end():]


def _wrap_html(fragment, title):
    """把 pandoc 输出的 HTML 片段包成完整文档，并注入内联表格样式。"""
    body = _center_title(_inline_table_borders(fragment))
    return HTML_TPL.format(title=title, css=HTML_CSS,
                           body_style=BODY_STYLE, body=body)


def via_html(src, dst, engines, **opts):
    """两段式转换。不适用时返回 None，适用但失败时返回 ok=False 的结果。"""
    s = Path(src).suffix.lower().lstrip(".")
    d = Path(dst).suffix.lower().lstrip(".")
    if s not in CHAIN_SRC or d not in CHAIN_DST or s == d:
        return None

    by = {e.name: e for e in engines}
    pandoc, lo = by.get("pandoc"), by.get("libreoffice")
    if not pandoc or not lo:
        return None
    if not (pandoc.available()[0] and lo.available()[0]):
        return None

    tmp = Path(tempfile.mkdtemp(prefix="dc-chain-"))
    frag = tmp / "frag.html"
    full = tmp / "full.html"
    try:
        r1 = pandoc.convert(str(src), str(frag))
        if not r1.ok:
            return ConvResult(False, str(src), str(dst), "chain",
                              message=f"HTML 中转失败: {r1.message}")

        # 注入样式后再交给 LO —— 这一步决定了表格有没有框
        feed = frag
        try:
            body = frag.read_text(encoding="utf-8", errors="ignore")
            full.write_text(_wrap_html(body, Path(src).stem), encoding="utf-8")
            feed = full
        except OSError:
            pass               # 包装失败就退回原始片段，至少别断链

        os.makedirs(os.path.dirname(os.path.abspath(dst)) or ".", exist_ok=True)
        r2 = lo.convert(str(feed), str(dst))
        if not r2.ok:
            return ConvResult(False, str(src), str(dst), "chain",
                              message=f"HTML 转 {d} 失败: {r2.message}")

        outs = r2.outputs or [str(dst)]
        return ConvResult(
            True, str(src), str(outs[0]), "chain:pandoc→libreoffice",
            seconds=(r1.seconds or 0) + (r2.seconds or 0),
            outputs=outs,
            warn=list(r2.warn) + [
                "直转失败（源文档结构不完整，常见于腾讯文档 / WPS 导出），"
                "已改用 HTML 中转，版式与原文档会有差异"
            ],
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

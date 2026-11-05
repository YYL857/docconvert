# -*- coding: utf-8 -*-
"""PyMuPDF 引擎 —— PDF 出口（文本/图片/HTML）与图片合成 PDF。

定位：PDF 是「终点格式」，反向提取只有三条出路，本引擎全包：
    pdf -> txt    纯文本（最快，多栏可能串行）
    pdf -> md     带标题层级（按字号聚类推断，比纯文本可读）
    pdf -> html   保留段落与基本排版的 HTML
    pdf -> png/jpg 逐页渲染（用于视觉校对、OCR 前置）
    pdf -> pdf    图片型 PDF 压缩/降采样
"""
import os
import re
from collections import Counter
from pathlib import Path

from ..utils import ConvResult, ensure_dir
from .base import Engine, EngineInfo

try:                       # PyMuPDF >= 1.24 用 pymupdf，老的用 fitz
    import pymupdf as fitz
except ImportError:        # pragma: no cover
    import fitz


class PdfEngine(Engine):
    info = EngineInfo(
        name="pymupdf",
        label="PyMuPDF",
        priority=60,
        note="PDF 提取文本/图片/HTML；逐页渲染；纯 Python 依赖，无需外部程序",
    )
    src_exts = ("pdf", "xps", "epub", "mobi", "fb2", "cbz")
    dst_exts = ("txt", "md", "html", "png", "jpg", "jpeg")

    def __init__(self, cfg=None):
        cfg = cfg or {}
        self.dpi = int(cfg.get("pdf_dpi", 200))

    def available(self):
        v = getattr(fitz, "__version__", None) or getattr(fitz, "VersionBind", "未知")
        return True, f"内置 {fitz.__name__} {v}"

    # ---------- 主入口 ----------
    def convert(self, src, dst, **opts):
        import time
        t0 = time.time()
        src, dst = os.path.abspath(src), os.path.abspath(dst)
        d_ext = Path(dst).suffix.lower().lstrip(".")
        ensure_dir(dst)
        try:
            doc = fitz.open(src)
        except Exception as e:
            return ConvResult(False, src, dst, self.name, message=f"打不开源文件: {e}")

        try:
            if d_ext in ("png", "jpg", "jpeg"):
                return self._to_images(doc, src, dst, d_ext, opts, t0)
            if d_ext == "txt":
                return self._to_text(doc, src, dst, t0)
            if d_ext == "md":
                return self._to_markdown(doc, src, dst, t0)
            if d_ext == "html":
                return self._to_html(doc, src, dst, t0)
            return ConvResult(False, src, dst, self.name,
                              message=f"PyMuPDF 不支持输出 .{d_ext}")
        finally:
            doc.close()

    # ---------- 各出口 ----------
    def _to_text(self, doc, src, dst, t0):
        import time
        parts = []
        for i, page in enumerate(doc, 1):
            t = page.get_text("text")
            if len(doc) > 1:
                parts.append(f"\n\n===== 第 {i} 页 =====\n")
            parts.append(t)
        text = "".join(parts)

        # 扫描件 PDF 没有文本层：宁可失败让路由降级到 OCR，也不交付空文件
        if len(text.strip()) < 10:
            return ConvResult(False, src, dst, self.name, seconds=time.time() - t0,
                              message="该 PDF 无文本层（扫描件/纯图片），"
                                      "请加 --ocr 走 Tesseract，或先装 tesseract")

        Path(dst).write_text(text, encoding="utf-8")
        warn = ["多栏排版的 PDF 抽成纯文本时可能出现左右栏串行，需人工核对"] if len(doc) else []
        return ConvResult(True, src, dst, self.name, seconds=time.time() - t0,
                          outputs=[dst], warn=warn)

    def _to_markdown(self, doc, src, dst, t0):
        """按字号聚类推标题层级：正文=最高频字号，>1.25x 记为标题。"""
        import time
        blocks = []          # (page_no, y0, x0, size, text)
        for pno, page in enumerate(doc, 1):
            d = page.get_text("dict")
            for b in d.get("blocks", []):
                if b.get("type") != 0:
                    continue
                for line in b.get("lines", []):
                    spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                    if not spans:
                        continue
                    size = round(max(s["size"] for s in spans), 1)
                    txt = "".join(s["text"] for s in spans).strip()
                    bb = line.get("bbox", (0, 0, 0, 0))
                    blocks.append((pno, bb[1], bb[0], size, txt))

        if not blocks:
            return ConvResult(False, src, dst, self.name, seconds=time.time() - t0,
                              message="该 PDF 无文本层（扫描件/纯图片），"
                                      "请加 --ocr 走 Tesseract，或先装 tesseract")

        sizes = Counter(b[3] for b in blocks)
        body = sizes.most_common(1)[0][0]
        # 标题判据：比正文大 15% 以上，且不是长段落（长段落放大只是强调，不是标题）
        heads = sorted({b[3] for b in blocks
                        if b[3] > body * 1.15 and len(b[4]) <= 40}, reverse=True)
        lvl_of = {s: min(i + 1, 3) for i, s in enumerate(heads)}

        out, prev = [], None
        for pno, y, x, size, txt in blocks:
            if prev is not None and pno != prev:
                if len(doc) > 1:
                    out.append(f"\n\n---\n\n<!-- 第 {pno} 页 -->\n")
            prev = pno
            lv = lvl_of.get(size)
            if lv:
                out.append("\n" + "#" * lv + " " + txt + "\n")
            else:
                out.append(txt + "\n")

        Path(dst).write_text("".join(out), encoding="utf-8")
        return ConvResult(True, src, dst, self.name, seconds=time.time() - t0,
                          outputs=[dst],
                          warn=["标题层级由字号推断，非原文语义，复杂版式需人工校正"])

    def _to_html(self, doc, src, dst, t0):
        import time
        chunks = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
                  f'<title>{Path(src).stem}</title>'
                  "<style>body{font-family:system-ui,'Microsoft YaHei',sans-serif;"
                  "max-width:820px;margin:2rem auto;line-height:1.75;color:#222}"
                  ".page{border-bottom:1px dashed #ddd;padding-bottom:2rem;margin-bottom:2rem}"
                  "</style></head><body>"]
        for i, page in enumerate(doc, 1):
            chunks.append(f'<section class="page" data-page="{i}">')
            chunks.append(page.get_text("html"))
            chunks.append("</section>")
        chunks.append("</body></html>")
        Path(dst).write_text("".join(chunks), encoding="utf-8")
        return ConvResult(True, src, dst, self.name, seconds=time.time() - t0, outputs=[dst])

    def _to_images(self, doc, src, dst, ext, opts, t0):
        import time
        dpi = int(opts.get("dpi") or self.dpi)
        stem = Path(dst).with_suffix("")
        outs = []
        multi = len(doc) > 1
        for i, page in enumerate(doc, 1):
            pix = page.get_pixmap(dpi=dpi)
            target = f"{stem}-{i}.{ext}" if multi else str(dst)
            pix.save(target)
            outs.append(target)
        warn = []
        if dpi < 150:
            warn.append(f"dpi={dpi} 偏低，文字可能发虚（建议 200+）")
        return ConvResult(True, src, outs[0], self.name, seconds=time.time() - t0,
                          outputs=outs, warn=warn)

    # ---------- 额外的：图片合成 PDF ----------
    def images_to_pdf(self, images, dst):
        """多张图片合成一个 PDF（按给定顺序，每页一张）。"""
        import time
        t0 = time.time()
        ensure_dir(dst)
        out = fitz.open()
        for img in images:
            img = os.path.abspath(img)
            try:
                pix = fitz.open(img)
            except Exception:
                # 走 PIL 转一次，兼容 webp/heic 等 fitz 不认的格式
                from PIL import Image
                tmp = str(Path(img).with_suffix(".png"))
                Image.open(img).convert("RGB").save(tmp)
                pix = fitz.open(tmp)
                img = tmp
            rect = pix[0].rect
            page = out.new_page(width=rect.width, height=rect.height)
            page.insert_image(rect, filename=img)
            pix.close()
        out.save(dst)
        out.close()
        return ConvResult(True, images[0] if images else "", dst, self.name,
                          seconds=time.time() - t0, outputs=[dst])

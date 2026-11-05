# -*- coding: utf-8 -*-
"""Tesseract OCR 引擎 —— 扫描件/图片转文字。

场景：PDF 没有文本层（扫描件）、手机拍的合同照片、截图里的表格。
做法：PDF 先用 PyMuPDF 按 dpi 渲染成图，再逐页 OCR，最后拼回文本。

依赖：tesseract 可执行 + 中文语言包 chi_sim
    Windows: winget install UB-Mannheim.TesseractOCR
             再下 chi_sim.traineddata 放进 tessdata 目录
"""
import os
import subprocess
import tempfile
from pathlib import Path

from ..utils import BIN, ConvResult, ensure_dir, find_exe, run_timed
from .base import Engine, EngineInfo

OCR_SRC = ("pdf", "png", "jpg", "jpeg", "webp", "bmp", "tiff", "tif")


class OcrEngine(Engine):
    info = EngineInfo(
        name="tesseract",
        label="Tesseract OCR",
        priority=30,
        note="扫描件 / 图片 -> 文字；需装 tesseract 与中文语言包",
    )
    src_exts = OCR_SRC
    dst_exts = ("txt", "md")

    def __init__(self, cfg=None):
        cfg = cfg or {}
        self.bin = self._find_bin(cfg.get("tesseract_path"))
        self.lang = cfg.get("ocr_lang", "chi_sim+eng")
        self.dpi = int(cfg.get("ocr_dpi", 300))

    @staticmethod
    def _find_bin(explicit=None):
        system = []
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.environ.get("LOCALAPPDATA", "")):
            if base:
                system.append(os.path.join(base, "Tesseract-OCR", "tesseract.exe"))
        system += ["/usr/bin/tesseract", "/usr/local/bin/tesseract", "/opt/homebrew/bin/tesseract"]
        if explicit and os.path.isfile(explicit):
            return explicit
        return find_exe("tesseract", local_rels=("tesseract/tesseract.exe", "tesseract.exe"),
                        system_paths=system)

    def available(self):
        if not self.bin:
            return False, ("未安装。Windows：winget install UB-Mannheim.TesseractOCR，"
                           "再把 chi_sim.traineddata 放进 tessdata/。"
                           "注意该安装包要求管理员权限，普通用户运行会报 WinError 740。")
        local_td = BIN / "tesseract" / "tessdata"
        cmd = [self.bin, "--list-langs"]
        if local_td.is_dir():
            cmd += ["--tessdata-dir", str(local_td)]
        r, _ = run_timed(cmd, timeout=30)

        # --list-langs 输出形如：
        #   List of available languages in "D:/.../tessdata/" (3):
        #   chi_sim
        #   eng
        # 首行是标题，之后每行一个语言 —— 不能按空格切，否则标题词会被当成语言名。
        raw = (r.out or "") + (r.err or "")
        langs = []
        for line in raw.splitlines():
            s = line.strip()
            if not s or s.startswith(("List", "Error", "Warning", "Please", "Failed")):
                continue
            langs.append(s)

        need = [x for x in self.lang.split("+") if x]
        miss = [x for x in need if x not in langs]
        note = f"{self.bin}；语言包: {', '.join(langs) or '无'}"
        if miss:
            note += f"；缺 {', '.join(miss)}（该语种会退化成英文识别）"
        return True, note

    def convert(self, src, dst, **opts):
        import time
        t0 = time.time()
        src, dst = os.path.abspath(src), os.path.abspath(dst)
        d_ext = Path(dst).suffix.lower().lstrip(".")
        ensure_dir(dst)

        if not self.bin:
            return ConvResult(False, src, dst, self.name, message="Tesseract 未安装")
        lang = opts.get("lang") or self.lang
        tmpdir = Path(tempfile.mkdtemp(prefix="ocr-"))
        pages = []

        try:
            if Path(src).suffix.lower() == ".pdf":
                import pymupdf as fitz
                doc = fitz.open(src)
                for i, page in enumerate(doc, 1):
                    img = tmpdir / f"p{i}.png"
                    page.get_pixmap(dpi=int(opts.get("dpi") or self.dpi)).save(str(img))
                    pages.append(img)
                doc.close()
            else:
                pages = [Path(src)]

            texts = []
            # 语言包目录：优先用工具自带的 bin/tesseract/tessdata，
            # 这样即使 tesseract.exe 装在 Program Files（没权限往里拷语言包），
            # 也能用上我们自备的 chi_sim.traineddata。
            extra_args = []
            local_td = BIN / "tesseract" / "tessdata"
            first_lang = lang.split("+")[0]
            if (local_td / f"{first_lang}.traineddata").is_file():
                extra_args = ["--tessdata-dir", str(local_td)]

            for i, img in enumerate(pages, 1):
                r, _ = run_timed(
                    [self.bin, str(img), "stdout", "-l", lang, "--psm", "3"] + extra_args,
                    timeout=180,
                )
                body = (r.out or "").strip()
                if not body:
                    body = f"[第 {i} 页未识别出文字]"
                if len(pages) > 1:
                    texts.append(f"===== 第 {i} 页 =====\n{body}")
                else:
                    texts.append(body)

            text = "\n\n".join(texts)
            if d_ext == "md":
                text = "\n".join(("" if not ln.strip() else ln) for ln in text.splitlines())
            Path(dst).write_text(text, encoding="utf-8")
            warn = ["OCR 结果必有错字，数字/编号/金额务必逐项核对"]
            if d_ext == "md" and "未识别出文字" in text:
                warn.append("部分页面无文字输出，可能是纯图形页或 dpi 不足")
            return ConvResult(True, src, dst, self.name, seconds=time.time() - t0,
                              outputs=[dst], warn=warn)
        finally:
            for p in tmpdir.glob("*"):
                try:
                    p.unlink()
                except OSError:
                    pass
            try:
                tmpdir.rmdir()
            except OSError:
                pass

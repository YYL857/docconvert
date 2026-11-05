# -*- coding: utf-8 -*-
"""Pandoc 引擎 —— 标记语言/纯文本族的最强项。

典型用途：md <-> docx <-> html <-> rst <-> epub <-> ipynb
优势：标题层级、列表、表格、脚注、公式（LaTeX）能结构化保留；
      比 LibreOffice 更适合「要结构不要版式」的场景。
短板：不管样式皮肤（要好看得给 --reference-doc 模板），不能输出 pdf（需 LaTeX）。
"""
import os
import shutil
from pathlib import Path

from ..utils import ConvResult, ensure_dir, find_exe, run_timed
from .base import Engine, EngineInfo

# 扩展名 -> pandoc 格式名
PANDOC_FMT = {
    "md": "gfm", "markdown": "markdown", "mdown": "markdown",
    "html": "html", "htm": "html",
    "docx": "docx", "odt": "odt", "rtf": "rtf",
    "txt": "plain", "text": "plain",
    "rst": "rst", "org": "org",
    "tex": "latex", "latex": "latex",
    "epub": "epub", "ipynb": "ipynb", "json": "json",
    "csv": "csv", "tsv": "tsv",
}

PANDOC_SRC = tuple(PANDOC_FMT)
PANDOC_DST = tuple(k for k in PANDOC_FMT if k != "csv")  # pandoc 不吐 csv


class PandocEngine(Engine):
    info = EngineInfo(
        name="pandoc",
        label="Pandoc",
        priority=70,
        note="md/html/docx/rst/epub 互转，结构保真好；不管版式皮肤",
    )
    src_exts = PANDOC_SRC
    dst_exts = PANDOC_DST

    def __init__(self, cfg=None):
        cfg = cfg or {}
        self.bin = self._find_bin(cfg.get("pandoc_path"))
        self.timeout = int(cfg.get("timeout", 180))

    @staticmethod
    def _find_bin(explicit=None):
        system = []
        for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")):
            if base:
                system.append(os.path.join(base, "Pandoc", "pandoc.exe"))
                system.append(os.path.join(base, "pandoc", "pandoc.exe"))
        system += ["/usr/local/bin/pandoc", "/usr/bin/pandoc", "/opt/homebrew/bin/pandoc"]
        if explicit and os.path.isfile(explicit):
            return explicit
        return find_exe("pandoc", local_rels=("pandoc/pandoc.exe", "pandoc.exe"),
                        system_paths=system)

    def available(self):
        if not self.bin:
            return False, ("未安装。Windows：winget install JohnMacFarlane.Pandoc；"
                           "或从 pandoc.org 下单个 exe 放进 PATH。")
        r, _ = run_timed([self.bin, "--version"], timeout=30)
        if r.code != 0:
            return False, f"{self.bin} 执行失败: {r.err.strip()[:120]}"
        ver = r.out.strip().splitlines()[0] if r.out.strip() else "未知版本"
        return True, f"{self.bin} ({ver})"

    def convert(self, src, dst, **opts):
        import time
        t0 = time.time()
        src = os.path.abspath(src)
        dst = os.path.abspath(dst)
        s_ext = Path(src).suffix.lower().lstrip(".")
        d_ext = Path(dst).suffix.lower().lstrip(".")
        ensure_dir(dst)

        if not self.bin:
            return ConvResult(False, src, dst, self.name, message="Pandoc 未安装")
        if s_ext not in PANDOC_FMT or d_ext not in PANDOC_FMT:
            return ConvResult(False, src, dst, self.name,
                              message=f"Pandoc 不支持 {s_ext} -> {d_ext}")

        media_dir = Path(dst).with_suffix("").as_posix() + "_media"
        cmd = [self.bin,
               "-f", PANDOC_FMT[s_ext],
               "-t", PANDOC_FMT[d_ext],
               "-o", dst,
               "--wrap=" + ("none" if opts.get("wrap_none", True) else "auto")]

        # 抽出内嵌图片，否则 docx->md 会丢图或塞 base64
        if s_ext in ("docx", "odt", "epub") and d_ext in ("md", "markdown", "html", "rst"):
            cmd.append(f"--extract-media={media_dir}")

        if opts.get("reference_doc"):
            cmd.append(f"--reference-doc={os.path.abspath(opts['reference_doc'])}")
        if opts.get("toc"):
            cmd.append("--toc")

        cmd.append(src)
        r, _ = run_timed(cmd, timeout=self.timeout)

        if not os.path.exists(dst) or os.path.getsize(dst) == 0:
            msg = (r.err or r.out or "").strip()[:300] or "未生成产物"
            return ConvResult(False, src, dst, self.name, seconds=time.time() - t0,
                              message=f"转换失败: {msg}")

        outs = [dst]
        if os.path.isdir(media_dir):
            outs.append(media_dir)
        warn = []
        if d_ext in ("md", "markdown") and s_ext == "docx":
            warn.append("docx->md 只保留结构（标题/列表/表格），字体字号颜色全部丢失")
        return ConvResult(True, src, dst, self.name, seconds=time.time() - t0,
                          outputs=outs, warn=warn)

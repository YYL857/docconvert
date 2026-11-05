# -*- coding: utf-8 -*-
"""路由层 —— 决定「这个转换该由谁来干」。

核心不是「谁能转」，而是「谁转得最好」：
同一对 (docx, txt) 有 pandoc / officedoc / libreoffice 三家都能接，
但分数不同 —— pandoc 保留表格与脚注最完整，officedoc 差一档，
LibreOffice 走一次排版再导出纯属浪费。所以这里用 score() 做任务特化排序，
而不是简单按引擎优先级。
"""
import os
from pathlib import Path

from .engines.ffmpeg_engine import FFmpegEngine
from .engines.libreoffice import LibreOfficeEngine
from .engines.ocr import OcrEngine
from .engines.officedoc import OfficeDocEngine
from .engines.pandoc import PandocEngine
from .engines.pillow_engine import PillowEngine
from .engines.pymupdf_engine import PdfEngine
from .utils import ConvResult

# 格式族（用于能力矩阵与路由判断）
# 注意 txt/csv/tsv 单独成「文本」族：它们是多数转换的终点，
# 若并进「文档」「表格」族，矩阵里会出现「文档→文档」这种假通路。
FAMILY = {
    "文档": ("doc", "docx", "docm", "odt", "rtf", "wps"),
    "表格": ("xls", "xlsx", "xlsm", "ods"),
    "演示": ("ppt", "pptx", "pps", "ppsx", "odp"),
    "PDF": ("pdf",),
    "标记": ("md", "markdown", "html", "htm", "rst", "tex", "latex", "epub", "org"),
    "文本": ("txt", "csv", "tsv"),
    "图片": ("png", "jpg", "jpeg", "webp", "bmp", "tiff", "tif", "gif", "ico", "ppm"),
    "音视频": ("mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "mp3",
              "wav", "aac", "flac", "ogg", "m4a", "opus"),
}
EXT2FAM = {e: f for f, exts in FAMILY.items() for e in exts}


def family_of(ext):
    return EXT2FAM.get(ext.lower(), "其他")


def build_engines(cfg=None):
    """按配置实例化全部引擎（不论是否可用，可用性由 available() 判定）。"""
    cfg = cfg or {}
    ecfg = cfg.get("engines", {}) if isinstance(cfg.get("engines"), dict) else {}
    return [
        LibreOfficeEngine({**ecfg.get("libreoffice", {}),
                           "timeout": cfg.get("timeout", 300)}),
        PandocEngine({**ecfg.get("pandoc", {})}),
        PdfEngine({"pdf_dpi": cfg.get("pdf_dpi", 200)}),
        PillowEngine({"image_quality": cfg.get("image_quality", 92)}),
        OfficeDocEngine(),
        OcrEngine({**ecfg.get("tesseract", {}),
                   "ocr_dpi": cfg.get("ocr_dpi", 300)}),
        FFmpegEngine({**ecfg.get("ffmpeg", {}),
                      "media_timeout": cfg.get("media_timeout", 900)}),
    ]


def score(eng, s, d):
    """给 (源, 目标) 下的引擎打分。数字越大越优先。"""
    name, base = eng.name, eng.info.priority

    # ---- PDF 出口：PyMuPDF 直接读内容流，远优于 LibreOffice 的 Draw 导入 ----
    if s == "pdf":
        if d in ("txt", "md", "html"):
            return {"pymupdf": 200, "libreoffice": 40, "tesseract": 30}.get(name, base)
        if d in ("png", "jpg", "jpeg"):
            return {"pymupdf": 200, "libreoffice": 50}.get(name, base)
        if d in ("docx", "doc", "odt", "pptx", "ppt", "odp"):
            return {"libreoffice": 150, "pymupdf": 10}.get(name, base)

    # ---- 标记语言族：pandoc 结构化最强 ----
    if s in ("md", "markdown", "rst", "org", "tex", "latex") and \
            d in ("docx", "odt", "html", "epub", "rst", "tex"):
        return {"pandoc": 200, "libreoffice": 100}.get(name, base)

    # ---- html -> 办公文档：LibreOffice 的 CSS 支持比 pandoc 好 ----
    if s in ("html", "htm") and d in ("docx", "pdf", "odt"):
        return {"libreoffice": 180, "pandoc": 120}.get(name, base)

    # ---- docx 出口文本：pandoc 连表格/脚注都带走 ----
    if s == "docx" and d in ("md", "txt", "html"):
        return {"pandoc": 180, "officedoc": 100, "libreoffice": 80}.get(name, base)

    # ---- 表格转 CSV：原生读写能精确选 sheet，比走 LO 可控 ----
    if s in ("xlsx", "xlsm") and d in ("csv", "tsv"):
        return {"officedoc": 180, "libreoffice": 100}.get(name, base)

    # ---- 图片互转 ----
    if s in FAMILY["图片"] and d in FAMILY["图片"]:
        return {"pillow": 200}.get(name, base)

    # ---- 剩下的交给引擎自身优先级 ----
    return base


def plan(src_ext, dst_ext, engines, force=None, ocr=False):
    """返回候选引擎列表（已排序，仅含声明支持的引擎）。

    force: 指定引擎名，只返回它（不存在则空列表）
    ocr:   强制把 OCR 引擎顶到最前（扫描件场景）
    """
    s, d = src_ext.lower(), dst_ext.lower()
    cands = []

    if s == d and not force:
        return []          # 同格式互转没有意义（要重新保存请显式指定引擎）

    if force:
        for e in engines:
            if e.name == force:
                if not e.supports(s, d):
                    # 图片合成 PDF 这种特例也放行，交给引擎自己判断
                    if not (e.name == "pymupdf" and d == "pdf"):
                        return []
                cands.append(e)
        return cands

    for e in engines:
        if e.supports(s, d):
            cands.append(e)

    cands.sort(key=lambda e: score(e, s, d), reverse=True)

    if ocr:
        cands.sort(key=lambda e: 0 if e.name == "tesseract" else 1)
    return cands


def convert(src, dst, engines, force=None, ocr=False, **opts):
    """执行转换：按候选顺序尝试，前一个失败则降级到下一个。

    返回 ConvResult；全失败时 message 汇总每个引擎的失败原因（不掩盖问题）。
    """
    s = Path(src).suffix.lower().lstrip(".")
    d = Path(dst).suffix.lower().lstrip(".")

    cands = plan(s, d, engines, force=force, ocr=ocr)
    if not cands:
        tried = []
        for e in engines:
            ok, note = e.available()
            tried.append(f"{e.name}{'✓' if ok else '✗'}")
        return ConvResult(False, src, dst, "", message=(
            f"没有引擎能处理 {s} -> {d}。已探测: {', '.join(tried)}。"
            f"若是办公文档，装 LibreOffice 可解；若是扫描件，用 --ocr。"))

    errs = []
    for e in cands:
        ok, note = e.available()
        if not ok:
            errs.append(f"{e.name}: 不可用（{note[:80]}）")
            continue
        r = e.convert(src, dst, **opts)
        if r.ok:
            return r
        errs.append(f"{e.name}: {r.message}")

    # 全部失败：把每一家的失败原因摊开，别只说"转换失败"
    last = ConvResult(False, src, dst, cands[0].name,
                      message="所有引擎均失败 → " + " | ".join(errs))
    return last


def matrix(engines, only_available=False, avail=None):
    """生成 族×族 能力矩阵：每个格子给出最佳引擎名（或 None）。

    avail: {引擎名: 是否可用} 预计算结果。
        🔴 必须传 —— 不传时这里会对**每个格式组合**重新调 available()，
        而 available() 可能启动进程（ffmpeg -version / pandoc --version），
        实测 8 族×8 族跑下来要 13.6 秒，Web UI 直接超时。
        预计算后同样的矩阵 0.01 秒。
    """
    def is_ok(e):
        if avail is not None:
            return bool(avail.get(e.name, False))
        return e.available()[0]

    fams = list(FAMILY)
    table = {}
    for fs in fams:
        for fd in fams:
            best, best_score = None, -1
            for s in FAMILY[fs]:
                for d in FAMILY[fd]:
                    if s == d:
                        continue
                    for e in engines:
                        if not e.supports(s, d):
                            continue
                        if only_available and not is_ok(e):
                            continue
                        sc = score(e, s, d)
                        if sc > best_score:
                            best, best_score = e.name, sc
            table[(fs, fd)] = best
    return fams, table

# -*- coding: utf-8 -*-
"""LibreOffice headless 引擎 —— 办公文档族的主力。

覆盖面（离线、无 Office 也能跑）：
    doc/docx/odt/rtf/txt/html/wps  <->  doc/docx/pdf/odt/rtf/txt/html
    xls/xlsx/ods/csv               <->  xls/xlsx/csv/pdf/html/ods
    ppt/pptx/odp                   <->  ppt/pptx/pdf/odp/png/jpg

已知硬边界（不粉饰）：
    1. PDF 进来只能走 Draw 导入，导出 docx/pptx 是「每页一个文本框/图片」，
       版式像、但不可编辑 —— 属于低保真。要真可编辑必须走 OCR 重建。
    2. 97-2003 的 .doc/.xls/.ppt 能读，但导出时若目标也是老格式会丢部分特性。
    3. 同一 profile 并发调用会互相锁死，本实现每次用独立 profile 目录规避。
"""
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from ..utils import ConvResult, ensure_dir, find_exe, run_timed
from .base import Engine, EngineInfo

# 目标扩展名 -> LibreOffice --convert-to 的 filter 串
# 带 filter 名是为了锁定输出变体（否则 LO 可能按默认变体输出，如 txt 用系统编码）
LO_FILTERS = {
    "pdf": "pdf",
    "docx": "docx:MS Word 2007 XML",
    "doc": "doc:MS Word 97",
    "odt": "odt",
    "rtf": "rtf",
    "txt": "txt:Text (encoded):UTF8",
    "html": "html:HTML (StarWriter)",
    "htm": "html:HTML (StarWriter)",
    "xlsx": "xlsx:Calc MS Excel 2007 XML",
    "xls": "xls:MS Excel 97",
    "ods": "ods",
    "csv": 'csv:Text - txt - csv (StarCalc):44,34,76,1,,0,false,true,false',
    "pptx": "pptx:Impress MS PowerPoint 2007 XML",
    "ppt": "ppt:MS PowerPoint 97",
    "odp": "odp",
    "png": "png",
    "jpg": "jpg",
    "jpeg": "jpg",
    "svg": "svg",
    "epub": "epub",
}

# 源扩展名 -> 允许的目标扩展名（留空表示不限制，交给 LO 自己试）
LO_SRC = (
    "doc", "docx", "docm", "odt", "rtf", "txt", "html", "htm", "wps",
    "xls", "xlsx", "xlsm", "ods", "csv", "tsv",
    "ppt", "pptx", "pps", "ppsx", "odp", "fodp",
    "pdf", "odg", "svg", "epub",
)


def _probe_textboxes(docx_path):
    """探测 docx 是否把内容全塞进文本框（PDF 导入的典型形态）。

    返回 (文本框数, 正文层字符数)。
    正文层 = 去掉所有 w:txbxContent 之后的 w:t 文本 —— 这才是 python-docx /
    pandoc 能读到的部分。文本框里的字它们在 doc.paragraphs 里看不到。
    """
    try:
        import zipfile
        with zipfile.ZipFile(docx_path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        boxes = xml.count("<w:txbxContent")
        body = re.sub(r"<w:txbxContent.*?</w:txbxContent>", "", xml, flags=re.S)
        texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", body, flags=re.S)
        return boxes, len("".join(texts))
    except Exception:
        return 0, -1


def _fit_xlsx_columns(src):
    """把 xlsx 列宽调到能装下内容，返回临时文件路径（不改源文件）。

    Excel 默认列宽 8.43 字符，长 URL / 邮箱地址远超这个宽度。在 Excel 里
    它会「溢出」到右侧空白单元格，看着正常；但转成 PDF 后右侧单元格有内容，
    就变成文字互相覆盖 —— 一列一列粘在一起，完全没法看。

    这里按内容估算宽度重存一份给 LO 用。只在「需要加宽」时动，
    已有列宽设置不会被压窄。
    """
    try:
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter
    except ImportError:
        return None

    try:
        wb = load_workbook(src)
    except Exception:
        return None

    changed = False
    for ws in wb.worksheets:
        # 列变宽后容易超页宽，LO 会把右侧几列甩到第二页，表格被横向切断。
        # 让 LO 缩放到一页宽。列数太多时不强求（会缩到看不清）。
        if (ws.max_column or 0) <= 8:
            try:
                from openpyxl.worksheet.properties import PageSetupProperties
                ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
                ws.page_setup.fitToWidth = 1
                ws.page_setup.fitToHeight = 0
                changed = True
            except Exception:
                pass

        for col in range(1, (ws.max_column or 0) + 1):
            best = 0
            for row in range(1, min(ws.max_row or 1, 300) + 1):
                v = ws.cell(row, col).value
                if v is None:
                    continue
                # 中文按 2 个字符宽估算
                w = sum(2 if ord(ch) > 127 else 1 for ch in str(v))
                if w > best:
                    best = w
            if not best:
                continue
            letter = get_column_letter(col)
            cur = ws.column_dimensions[letter].width or 8.43
            if best + 2 > cur:
                ws.column_dimensions[letter].width = min(best + 2, 60)
                changed = True

    if not changed:
        wb.close()
        return None

    tmp = Path(tempfile.gettempdir()) / f"dc-fit-{uuid.uuid4().hex[:8]}.xlsx"
    try:
        wb.save(tmp)
    finally:
        wb.close()
    return tmp


class LibreOfficeEngine(Engine):
    info = EngineInfo(
        name="libreoffice",
        label="LibreOffice headless",
        priority=80,
        note="办公/表格/演示/PDF 的主力引擎；离线、无需 MS Office",
    )
    src_exts = LO_SRC
    dst_exts = tuple(LO_FILTERS)

    def __init__(self, cfg=None):
        cfg = cfg or {}
        self.bin = self._find_bin(cfg.get("libreoffice_path"))
        self.timeout = int(cfg.get("timeout", 300))

    # ---------- 定位 ----------
    @staticmethod
    def _find_bin(explicit=None):
        system = []
        if os.name == "nt":
            for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                         os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
                system.append(os.path.join(base, "LibreOffice", "program", "soffice.exe"))
            system.append(os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                       "Programs", "LibreOffice", "program", "soffice.exe"))
        elif os.name == "darwin":
            system.append("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        else:
            system += ["/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice",
                       "/snap/bin/libreoffice", "/usr/local/bin/soffice"]

        if explicit and os.path.isfile(explicit):
            return explicit
        # 绿色部署优先：bin/libreoffice/program/soffice.exe
        return find_exe("soffice",
                        local_rels=("libreoffice/program/soffice.exe",
                                    "LibreOffice/program/soffice.exe",
                                    "soffice.exe"),
                        system_paths=system)

    def _read_version(self):
        """从安装目录的 ini 读版本，不启动进程。

        🔴 不要用 `soffice --version` 探测：Windows 上 soffice 是 GUI 子系统程序，
        控制台拿不到输出、进程也不退出，会一直挂到超时（实测 2026-11-05 卡死 120s，
        连带 doctor 整个挂住）。读 version.ini / bootstrap.ini 是零风险的等价做法。
        """
        prog = Path(self.bin).parent
        for name, key in (("version.ini", "UpdateID"), ("bootstrap.ini", "ProductKey")):
            f = prog / name
            if not f.is_file():
                continue
            try:
                for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.startswith(key + "="):
                        val = line.split("=", 1)[1].strip()
                        if key == "UpdateID":
                            m = re.search(r"LibreOffice_(\d+)", val)
                            return f"LibreOffice {m.group(1)}" if m else "LibreOffice"
                        return val[:32]
            except OSError:
                continue
        return "LibreOffice"

    def available(self):
        if not self.bin:
            return False, ("未安装。Windows 装法：winget install TheDocumentFoundation.LibreOffice "
                           "（或到 libreoffice.org 下 .msi）。装完重跑 doctor 即可。")
        # 硬检查：soffice.bin 与 share/registry 缺一不可（msiexec /a 解包不完整时会有后者缺失）
        prog = Path(self.bin).parent
        if not (prog / "soffice.bin").is_file():
            return False, f"{self.bin} 存在但同目录缺 soffice.bin，安装不完整"
        if not (prog.parent / "share" / "registry").is_dir():
            return False, f"{prog.parent} 下缺 share/registry，解包不完整，需重新部署"
        return True, f"{self.bin} ({self._read_version()})"

    # ---------- 转换 ----------
    def convert(self, src, dst, **opts):
        import time
        t0 = time.time()
        src = os.path.abspath(src)
        dst = os.path.abspath(dst)
        src_ext = Path(src).suffix.lower().lstrip(".")
        dst_ext = Path(dst).suffix.lower().lstrip(".")
        ensure_dir(dst)

        if not self.bin:
            return ConvResult(False, src, dst, self.name,
                              message="LibreOffice 未安装，无法转换办公文档")
        if dst_ext not in LO_FILTERS:
            return ConvResult(False, src, dst, self.name,
                              message=f"LibreOffice 引擎不支持输出 .{dst_ext}")

        # 每次独立 profile：规避「同一 profile 并发被锁 + 残留锁文件导致静默失败」
        profile = Path(tempfile.gettempdir()) / f"lo-prof-{uuid.uuid4().hex[:8]}"
        profile.mkdir(parents=True, exist_ok=True)
        profile_url = profile.resolve().as_uri()

        # 表格转 PDF：先按内容适配列宽（Excel 默认列宽装不下长 URL，会挤成一坨）
        feed_src = src
        fitted = None
        if src_ext in ("xlsx", "xlsm") and dst_ext in ("pdf", "png", "jpg", "jpeg"):
            fitted = _fit_xlsx_columns(src)
            if fitted is not None:
                feed_src = str(fitted)

        outdir = str(Path(dst).parent)
        cmd = [
            self.bin,
            f"-env:UserInstallation={profile_url}",
            "--headless", "--norestore", "--nologo", "--nolockcheck",
        ]
        # PDF 默认按 Draw 打开，Writer/Impress 导出滤镜会对空模型报
        # Io/Write:16 或 "no export filter"。强制用对应 PDF 导入滤镜。
        # （msiexec /a 绿色包实测 2026-11-05：无 infilter 时 pdf→docx 必败，
        #  有 --infilter=writer_pdf_import 则能出非空 docx。）
        if src_ext == "pdf":
            if dst_ext in ("docx", "doc", "odt", "rtf", "txt", "html", "htm", "epub"):
                cmd += ["--infilter=writer_pdf_import"]
            elif dst_ext in ("pptx", "ppt", "odp"):
                cmd += ["--infilter=impress_pdf_import"]
        cmd += [
            "--convert-to", LO_FILTERS[dst_ext],
            "--outdir", outdir,
            feed_src,
        ]
        r, secs = run_timed(cmd, timeout=self.timeout)

        # LO 的产物名恒为「源 basename + 目标扩展名」，与用户要的文件名可能不同
        produced = Path(outdir) / (Path(feed_src).stem + "." + dst_ext)
        if not produced.exists():
            # 大小写/变体兜底：在同目录里找刚生成的同扩展名文件
            recent = sorted(Path(outdir).glob(f"*.{dst_ext}"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
            produced = recent[0] if recent else None

        # Windows 下 soffice 的退出码不可靠，以产物为准
        if not produced or not produced.exists() or produced.stat().st_size == 0:
            msg = (r.err or r.out or "").strip()[:300] or "未生成产物（引擎无输出）"
            if r.timed_out:
                msg = f"超时 {self.timeout}s，已终止"
            shutil.rmtree(profile, ignore_errors=True)
            return ConvResult(False, src, dst, self.name, seconds=time.time() - t0,
                              message=f"转换失败: {msg}")

        # 名字对齐用户要求
        final = produced
        if os.path.abspath(str(produced)) != dst:
            try:
                if os.path.exists(dst):
                    os.remove(dst)
                shutil.move(str(produced), dst)
                final = Path(dst)
            except OSError:
                final = produced  # 挪不动就交付原位置，别丢产物

        shutil.rmtree(profile, ignore_errors=True)
        if fitted is not None:
            try:
                fitted.unlink()
            except OSError:
                pass
        warn = []
        if src_ext == "pdf" and dst_ext in ("docx", "doc", "odt", "rtf"):
            boxes, body_chars = _probe_textboxes(str(final))
            if boxes > 0 and body_chars < 20:
                # 这是 PDF 导入的真实形态：内容全在文本框里，正文层空白。
                # Word 里看着正常，但 python-docx / pandoc 读正文会拿到空字符串，
                # 后续再转 md/txt 会丢光内容 —— 必须显式告知，不能只说「低保真」。
                warn.append(
                    f"内容全部落在 {boxes} 个文本框里（正文层仅 {body_chars} 字符）："
                    "Word 里能看能改，但程序读取正文会得到空，"
                    "拿它再转 md/txt 会丢内容，需先人工重排"
                )
            else:
                warn.append("PDF 还原为可编辑文档属低保真，版式与原文不会完全一致")
        return ConvResult(True, src, str(final), self.name,
                          seconds=time.time() - t0,
                          outputs=[str(final)],
                          message="", warn=warn)

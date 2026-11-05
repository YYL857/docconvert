# -*- coding: utf-8 -*-
"""Office 原生读写引擎 —— 无任何外部程序也能用的兜底。

只做「单向提取」（Office 文件 -> 文本/CSV），不做排版：
    docx -> txt / md / html     保留标题层级、列表、表格（按正文真实顺序）
    xlsx -> csv / tsv / txt     可指定 sheet，默认全部 sheet 各导一份
    pptx -> txt                 按页导出每页文本框内容

为什么单独做这一层：LibreOffice / pandoc 一个都没装时，工具不能是废的。
这条路纯 Python 依赖，随 pip 装好就能跑。
"""
import csv
import os
from pathlib import Path

from ..utils import ConvResult, ensure_dir
from .base import Engine, EngineInfo


# 精确到「谁 -> 谁能转」：docx 转 csv、pptx 转 html 这类无意义组合不放行，
# 否则能力矩阵会画出虚假的通路。
OFFICEDOC_PAIRS = {
    "docx": ("txt", "md", "html"),
    "xlsx": ("csv", "tsv", "txt"),
    "xlsm": ("csv", "tsv", "txt"),
    "pptx": ("txt", "md"),
}


class OfficeDocEngine(Engine):
    info = EngineInfo(
        name="officedoc",
        label="Office 原生读写",
        priority=40,
        note="docx/xlsx/pptx 单向提取为文本/CSV，无外部依赖；不做排版还原",
    )
    pairs = OFFICEDOC_PAIRS
    src_exts = tuple(OFFICEDOC_PAIRS)
    dst_exts = ("txt", "md", "html", "csv", "tsv")

    def available(self):
        got, miss = [], []
        for mod, label in (("docx", "python-docx"), ("openpyxl", "openpyxl"),
                           ("pptx", "python-pptx")):
            try:
                __import__(mod)
                got.append(label)
            except ImportError:
                miss.append(label)
        if not got:
            return False, "未安装：pip install python-docx openpyxl python-pptx"
        note = "已装 " + " / ".join(got)
        if miss:
            note += "；缺 " + " / ".join(miss) + "（对应格式不可用）"
        return True, note

    def convert(self, src, dst, **opts):
        import time
        t0 = time.time()
        src, dst = os.path.abspath(src), os.path.abspath(dst)
        s_ext = Path(src).suffix.lower().lstrip(".")
        d_ext = Path(dst).suffix.lower().lstrip(".")
        ensure_dir(dst)

        try:
            if s_ext == "docx":
                return self._docx(src, dst, d_ext, t0, opts)
            if s_ext in ("xlsx", "xlsm"):
                return self._xlsx(src, dst, d_ext, t0, opts)
            if s_ext == "pptx":
                return self._pptx(src, dst, d_ext, t0, opts)
        except ImportError as e:
            return ConvResult(False, src, dst, self.name,
                              message=f"缺少依赖: {e}（pip install python-docx openpyxl python-pptx）")
        except Exception as e:
            return ConvResult(False, src, dst, self.name, seconds=time.time() - t0,
                              message=f"读取失败: {e}")
        return ConvResult(False, src, dst, self.name, message=f"不支持 .{s_ext}")

    # ---------- docx ----------
    def _docx(self, src, dst, d_ext, t0, opts):
        import time
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        doc = Document(src)
        blocks = []          # ("h", level, text) / ("p", text) / ("li", text) / ("table", rows)

        # 按 body 真实子元素顺序遍历，避免「所有段落在前、所有表格在后」的错位
        for child in doc.element.body.iterchildren():
            tag = child.tag.split("}")[-1]
            if tag == "p":
                p = Paragraph(child, doc)
                txt = p.text.strip()
                if not txt:
                    continue
                style = (p.style.name or "") if p.style is not None else ""
                if style.lower().startswith("heading"):
                    digits = "".join(c for c in style if c.isdigit())
                    blocks.append(("h", min(int(digits or 1), 6), txt))
                elif style.lower().startswith(("list", "bullet")) or txt.startswith(("- ", "• ")):
                    blocks.append(("li", txt.lstrip("-• ").strip()))
                else:
                    blocks.append(("p", txt))
            elif tag == "tbl":
                t = Table(child, doc)
                rows = [[c.text.strip().replace("\n", " ") for c in r.cells] for r in t.rows]
                if rows:
                    blocks.append(("table", rows))

        if d_ext == "txt":
            lines = []
            for b in blocks:
                if b[0] == "table":
                    lines.extend("\t".join(r) for r in b[1])
                elif b[0] == "h":
                    lines.append(b[2])
                else:
                    lines.append(b[-1])
            Path(dst).write_text("\n".join(lines) + "\n", encoding="utf-8")

        elif d_ext == "md":
            out = []
            for b in blocks:
                if b[0] == "h":
                    out.append("#" * b[1] + " " + b[2])
                elif b[0] == "li":
                    out.append("- " + b[1])
                elif b[0] == "table":
                    rows = b[1]
                    width = max(len(r) for r in rows)
                    rows = [r + [""] * (width - len(r)) for r in rows]
                    out.append("| " + " | ".join(rows[0]) + " |")
                    out.append("| " + " | ".join(["---"] * width) + " |")
                    out.extend("| " + " | ".join(r) + " |" for r in rows[1:])
                else:
                    out.append(b[1])
                out.append("")
            Path(dst).write_text("\n".join(out), encoding="utf-8")

        elif d_ext == "html":
            esc = lambda s: (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            out = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
                   f"<title>{esc(Path(src).stem)}</title>"
                   "<style>body{font-family:system-ui,'Microsoft YaHei',sans-serif;"
                   "max-width:820px;margin:2rem auto;line-height:1.75}"
                   "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:.4em .7em}"
                   "</style></head><body>"]
            for b in blocks:
                if b[0] == "h":
                    out.append(f"<h{b[1]}>{esc(b[2])}</h{b[1]}>")
                elif b[0] == "li":
                    out.append(f"<li>{esc(b[1])}</li>")
                elif b[0] == "table":
                    out.append("<table>")
                    for i, r in enumerate(b[1]):
                        cell = "th" if i == 0 else "td"
                        out.append("<tr>" + "".join(f"<{cell}>{esc(c)}</{cell}>" for c in r) + "</tr>")
                    out.append("</table>")
                else:
                    out.append(f"<p>{esc(b[1])}</p>")
            out.append("</body></html>")
            Path(dst).write_text("".join(out), encoding="utf-8")
        else:
            return ConvResult(False, src, dst, self.name,
                              message=f"docx 不支持导出 .{d_ext}")

        return ConvResult(True, src, dst, self.name, seconds=time.time() - t0,
                          outputs=[dst],
                          warn=["此路只提取文字与结构，字体/字号/颜色/分页全部丢失"])

    # ---------- xlsx ----------
    def _xlsx(self, src, dst, d_ext, t0, opts):
        import time
        from openpyxl import load_workbook

        if d_ext not in ("csv", "tsv", "txt"):
            return ConvResult(False, src, dst, self.name,
                              message=f"xlsx 不支持导出 .{d_ext}（要转 xls/pdf 请装 LibreOffice）")

        delim = "\t" if d_ext in ("tsv", "txt") else ","
        wb = load_workbook(src, data_only=True, read_only=True)
        sheets = wb.sheetnames
        want = opts.get("sheet")
        if want is not None:
            idx = int(want) if str(want).isdigit() else sheets.index(want)
            targets = [(sheets[idx], wb[sheets[idx]])]
        else:
            targets = [(n, wb[n]) for n in sheets]

        outs = []
        if len(targets) == 1:
            self._dump_sheet(targets[0][1], dst, delim)
            outs.append(dst)
        else:
            stem = Path(dst).with_suffix("")
            for name, ws in targets:
                safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
                p = f"{stem}__{safe}.{d_ext}"
                self._dump_sheet(ws, p, delim)
                outs.append(p)
        wb.close()
        return ConvResult(True, src, outs[0], self.name, seconds=time.time() - t0,
                          outputs=outs,
                          warn=[f"源含 {len(sheets)} 个 sheet，已分别导出"] if len(outs) > 1 else [])

    @staticmethod
    def _dump_sheet(ws, path, delim):
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=delim)
            for row in ws.iter_rows(values_only=True):
                if all(c is None or str(c).strip() == "" for c in row):
                    continue
                w.writerow(["" if c is None else c for c in row])

    # ---------- pptx ----------
    def _pptx(self, src, dst, d_ext, t0, opts):
        import time
        from pptx import Presentation

        if d_ext not in ("txt", "md"):
            return ConvResult(False, src, dst, self.name,
                              message=f"pptx 不支持导出 .{d_ext}（要转 pdf 请装 LibreOffice）")

        prs = Presentation(src)
        out = []
        for i, slide in enumerate(prs.slides, 1):
            out.append(f"## 第 {i} 页" if d_ext == "md" else f"===== 第 {i} 页 =====")
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for p in shape.text_frame.paragraphs:
                        t = "".join(r.text for r in p.runs).strip()
                        if t:
                            out.append(("- " if d_ext == "md" else "  ") + t)
            out.append("")
        Path(dst).write_text("\n".join(out), encoding="utf-8")
        return ConvResult(True, src, dst, self.name, seconds=time.time() - t0,
                          outputs=[dst],
                          warn=["只提取文字，图形/图表/图片/备注未导出"])

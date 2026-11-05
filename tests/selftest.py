# -*- coding: utf-8 -*-
"""自检 —— 造样例文件，跑真实转换，验证产物不是空壳。

设计原则：**缺引擎不算失败**，只标记 skip。目的是让任何人拿到这套工具后，
一条命令就能确认「哪条链路在正常工作、哪条缺东西」，而不是靠猜。

用法：
    python tests/selftest.py            # 跑全部可跑的
    python tests/selftest.py -v         # 显示每个用例的细节
"""
import os
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from dc import router                       # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def cfg():
    p = ROOT / "config.yaml"
    if not p.exists():
        return {}
    try:
        import yaml
        with open(p, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ---------- 造样例 ----------

def make_samples(d: Path):
    made = {}

    # docx：标题 + 正文 + 列表
    from docx import Document
    doc = Document()
    doc.add_heading("测试文档", level=1)
    doc.add_paragraph("这是一段正文，用来验证中文原样往返。")
    doc.add_heading("第二节", level=2)
    doc.add_paragraph("含数字 12345 与英文 ABC 的混合内容。")
    p = d / "sample.docx"
    doc.save(p)
    made["docx"] = p

    # xlsx：两行数据
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "数据"
    ws.append(["名称", "数量", "单价"])
    ws.append(["保温杯", 12, 39.9])
    ws.append(["奶瓶", 5, 88.0])
    p = d / "sample.xlsx"
    wb.save(p)
    made["xlsx"] = p

    # png：纯色块（测图片互转）
    from PIL import Image
    img = Image.new("RGB", (320, 200), (32, 120, 200))
    p = d / "sample.png"
    img.save(p)
    made["png"] = p

    # 带文字的 png（测 OCR；用英数避免中文字体缺失导致造样例失败）
    from PIL import ImageDraw, ImageFont
    timg = Image.new("RGB", (640, 180), "white")
    dr = ImageDraw.Draw(timg)
    font = None
    for fp in ("C:/Windows/Fonts/arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, 42)
                break
            except Exception:
                pass
    dr.text((24, 60), "Hello docconvert 12345", fill="black",
            font=font or ImageFont.load_default())
    p = d / "sample_text.png"
    timg.save(p)
    made["text_png"] = p

    # md
    p = d / "sample.md"
    p.write_text("# 标题\n\n段落一。\n\n- 条目 A\n- 条目 B\n", encoding="utf-8")
    made["md"] = p

    # pdf：多段落 + 内嵌图片（刻意贴近真实文档）
    # 只放两行纯文本时，pdf->docx 会「假通过」：Draw 导入能成功导出简单 PDF，
    # 但真实带图/复杂排版的 PDF 会报 Io/Write:16。样例必须能复现后者。
    import pymupdf as fitz
    from PIL import Image as _Im, ImageDraw as _Dr
    _im = _Im.new("RGB", (220, 130), (238, 238, 238))
    _Dr.Draw(_im).rectangle([8, 8, 212, 122], outline=(120, 120, 120))
    _Dr.Draw(_im).text((18, 60), "IMG", fill=(60, 60, 60))
    img_path = d / "pdfimg.png"
    _im.save(img_path)

    pdf = fitz.open()
    for i in (1, 2):
        page = pdf.new_page()
        page.insert_text((72, 90), f"Test PDF page {i}", fontsize=20)
        page.insert_text((72, 120), "Hello docconvert 12345", fontsize=11)
        page.insert_text((72, 145), "Second paragraph with numbers 67890.", fontsize=11)
        page.insert_text((72, 170), "Third line to make layout non-trivial.", fontsize=11)
        page.insert_image(fitz.Rect(72, 200, 292, 330), filename=str(img_path))
    p = d / "sample.pdf"
    pdf.save(str(p))
    pdf.close()
    made["pdf"] = p

    return made


# ---------- 用例 ----------

def case(name, src_key, dst_ext, samples, engines, verify=None, need=None):
    """返回 dict(status, detail)。status ∈ pass/skip/fail"""
    if need and not need[0]:
        return {"status": "skip", "detail": need[1]}
    src = samples.get(src_key)
    if not src:
        return {"status": "skip", "detail": f"缺少样例 {src_key}"}

    dst = src.with_name(f"{src.stem}_{dst_ext}.{dst_ext}")
    try:
        r = router.convert(str(src), str(dst), engines)
    except Exception as e:
        return {"status": "fail", "detail": f"异常: {e}\n{traceback.format_exc()[-400:]}"}

    if not r.ok:
        return {"status": "fail", "detail": r.message[:300]}

    # 产物存在且非空
    if not r.outputs:
        return {"status": "fail", "detail": "没有产物"}
    for o in r.outputs:
        op = Path(o)
        if op.is_file() and op.stat().st_size == 0:
            return {"status": "fail", "detail": f"产物为空: {o}"}

    if verify:
        # 多产物场景（如 PDF 逐页转图）实际首文件不是 dst，要用 outputs[0]
        first = Path(r.outputs[0])
        try:
            ok, msg = verify(first)
        except Exception as e:
            return {"status": "fail", "detail": f"校验异常: {e}"}
        if not ok:
            return {"status": "fail", "detail": msg}

    first = Path(r.outputs[0])
    size = first.stat().st_size if first.is_file() else 0
    extra = f"，共 {len(r.outputs)} 个产物" if len(r.outputs) > 1 else ""
    return {"status": "pass", "detail": f"[{r.engine}] {first.name} {size}B{extra}"}


def main():
    verbose = "-v" in sys.argv
    engines = router.build_engines(cfg())
    avail = {e.name: e.available() for e in engines}
    tmp = Path(tempfile.mkdtemp(prefix="dc-selftest-"))

    print("=" * 66)
    print("docconvert 自检")
    print("=" * 66)
    print(f"临时目录: {tmp}\n")

    try:
        samples = make_samples(tmp)
    except Exception as e:
        print(f"造样例失败（依赖不全？）: {e}")
        print("先跑: pip install python-docx openpyxl pillow pymupdf")
        return 2

    lo = avail.get("libreoffice", (False, ""))[0]
    pd = avail.get("pandoc", (False, ""))[0]
    ts = avail.get("tesseract", (False, ""))[0]

    def has_txt(dst):
        t = Path(dst).read_text(encoding="utf-8", errors="ignore")
        return ("测试文档" in t or "docconvert" in t or "Hello" in t), \
               f"文本里没找到预期关键词（前 80 字: {t[:80]!r}）"

    def is_csv(dst):
        t = Path(dst).read_text(encoding="utf-8-sig", errors="ignore")
        return ("保温杯" in t and "39.9" in t), f"CSV 内容不符: {t[:80]!r}"

    def openable(dst):
        from PIL import Image
        im = Image.open(dst)
        im.load()
        return im.size[0] > 0, "图片打不开"

    def reopen_pdf(dst):
        import pymupdf as fitz
        d = fitz.open(dst)
        n = len(d)
        d.close()
        return n > 0, "PDF 页数为 0"

    def docx_readable(dst):
        """docx 必须能被 python-docx 打开，且真能读到内容。

        PDF 导入生成的 docx 内容全在文本框里 —— doc.paragraphs 读不到，
        所以「正文 0 字但有文本框」也算有内容。之前用 pymupdf 校验 docx
        属于校验错对象，会放过空壳。
        """
        import zipfile
        from docx import Document
        d = Document(dst)
        body = len("".join(p.text for p in d.paragraphs).strip())
        xml = zipfile.ZipFile(dst).read("word/document.xml").decode("utf-8", "ignore")
        boxes = xml.count("<w:txbxContent")
        if body == 0 and boxes == 0:
            return False, "docx 既无正文也无文本框，是空壳"
        return True, ""

    def docx_body(dst):
        """要求正文层有字（md -> docx 应产出正常段落，不该是文本框）。"""
        from docx import Document
        d = Document(dst)
        body = "".join(p.text for p in d.paragraphs).strip()
        return len(body) > 0, "正文为空：md 转 docx 应产生正常段落"

    cases = [
        ("docx -> txt", "docx", "txt", has_txt, None),
        ("docx -> md", "docx", "md", has_txt, None),
        ("docx -> html", "docx", "html", has_txt, None),
        ("xlsx -> csv", "xlsx", "csv", is_csv, None),
        ("png -> jpg", "png", "jpg", openable, None),
        ("png -> pdf", "png", "pdf", reopen_pdf, None),
        ("pdf -> txt", "pdf", "txt", has_txt, None),
        ("pdf -> md", "pdf", "md", has_txt, None),
        ("pdf -> png", "pdf", "png", openable, None),
        ("pdf -> docx", "pdf", "docx", docx_readable,
         (lo, "缺 LibreOffice：docx/xlsx/pptx 与 PDF 之间的互转全靠它")),
        ("md -> docx", "md", "docx", docx_body,
         (pd or lo, "缺 pandoc 与 LibreOffice：md/html 转 docx 需要其一")),
        ("docx -> pdf", "docx", "pdf", reopen_pdf,
         (lo, "缺 LibreOffice：任何格式转 PDF 的排版还原都靠它")),
        ("xlsx -> pdf", "xlsx", "pdf", reopen_pdf,
         (lo, "缺 LibreOffice：表格转 PDF 需要它")),
        ("png -> txt (OCR)", "text_png", "txt", has_txt,
         (ts, "缺 Tesseract：图片/扫描件转文字需要它 + 中文语言包")),
    ]

    rows, npass, nfail, nskip = [], 0, 0, 0
    for name, sk, de, verify, need in cases:
        r = case(name, sk, de, samples, engines, verify=verify, need=need)
        mark = {"pass": f"{GREEN}PASS{RESET}", "fail": f"{RED}FAIL{RESET}",
                "skip": f"{YELLOW}SKIP{RESET}"}[r["status"]]
        print(f"{mark}  {name:<18} {r['detail'] if verbose or r['status'] != 'pass' else ''}")
        rows.append((name, r))
        npass += r["status"] == "pass"
        nfail += r["status"] == "fail"
        nskip += r["status"] == "skip"

    print("-" * 66)
    print(f"通过 {npass} / 失败 {nfail} / 跳过 {nskip}（共 {len(cases)}）")
    if nskip:
        print(f"\n跳过项都是「缺引擎」，不是代码问题。装上后重跑即转 PASS：")
        for name, r in rows:
            if r["status"] == "skip":
                print(f"  - {name}: {r['detail']}")
    if nfail:
        print(f"\n失败项需要看细节，加 -v 重跑。")

    # 清理（保留失败现场便于排查）
    if nfail == 0:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print(f"\n失败现场保留在: {tmp}")

    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

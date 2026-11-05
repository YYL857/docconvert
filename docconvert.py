#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""docconvert —— 离线、跨平台的通用文件格式转换工具。

用法速查：
    python docconvert.py doctor                     # 体检：这台机器能转什么
    python docconvert.py convert a.docx b.pdf       # 单文件
    python docconvert.py convert a.docx --to pdf    # 输出到同目录
    python docconvert.py convert a.docx --to md -o out/   # 输出到目录
    python docconvert.py batch --to pdf *.docx      # 批量
    python docconvert.py tree ./docs --to pdf -o ./out    # 目录树递归
    python docconvert.py explain docx pdf           # 看会走哪个引擎
    python docconvert.py matrix                     # 能力矩阵
    python docconvert.py formats                    # 支持的扩展名

设计原则：
    1. 离线优先 —— 所有引擎都是本地程序/库，运行期不联网
    2. 如实报错 —— 转换失败绝不产出空壳文件，也不静默降级成「看起来成功」
    3. 可降级 —— 首选引擎不可用时自动换下一个，并把每一家的失败原因摊开
"""
import argparse
import fnmatch
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from dc import doctor, router                      # noqa: E402
from dc.router import FAMILY                       # noqa: E402
from dc.utils import ConvResult, human_size        # noqa: E402


# ---------- 配置 ----------

def load_cfg(path=None):
    cfg_path = path or os.environ.get("DOCCONVERT_CONFIG") or os.path.join(HERE, "config.yaml")
    if not os.path.isfile(cfg_path):
        return {}
    try:
        import yaml
        with open(cfg_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[warn] 读配置失败（用默认设置继续）: {e}", file=sys.stderr)
        return {}


# ---------- 输出路径推导 ----------

def resolve_dst(src, args, to=None):
    """按规则推导输出路径。

    - 显式给了完整文件名（带扩展名）→ 直接用
    - 给了目录 / 以 / 结尾 → 目录 + 源文件名换扩展
    - 什么都没给 → 源文件同目录换扩展（加 _converted 防覆盖源文件）
    """
    src = Path(src)
    target_ext = to or (Path(args.output).suffix.lstrip(".") if args.output else None)
    if not target_ext:
        raise ValueError("必须知道目标格式：给 OUT 路径（带扩展名）或 --to")

    out_arg = args.output
    if not out_arg:
        cand = src.with_suffix("." + target_ext)
        # 同扩展名转换（如 docx -> docx 换版式）会撞源文件，加后缀避开
        if cand.exists() and src.suffix.lower() == cand.suffix.lower():
            return src.with_name(src.stem + "_converted." + target_ext)
        return cand

    p = Path(out_arg)
    looks_like_dir = out_arg.endswith(("/", "\\")) or p.is_dir() or not p.suffix
    if looks_like_dir:
        p.mkdir(parents=True, exist_ok=True)
        return p / (src.stem + "." + target_ext)
    return p


# ---------- 单文件 ----------

def do_one(src, dst, engines, args):
    t0 = time.time()
    r = router.convert(
        str(src), str(dst), engines,
        force=args.engine, ocr=args.ocr,
        dpi=args.dpi, sheet=args.sheet, quality=args.quality,
        lang=args.lang, fps=args.fps, width=args.width,
        reference_doc=args.reference_doc, toc=args.toc,
    )
    r.seconds = r.seconds or (time.time() - t0)
    return r


def print_result(r, verbose=False):
    if r.ok:
        print(r.brief())
        for w in r.warn:
            print(f"     ! {w}")
        if verbose:
            for o in r.outputs[1:]:
                print(f"     + {o}")
    else:
        print(f"FAIL  {r.src}\n      {r.message}", file=sys.stderr)


# ---------- 子命令 ----------

def cmd_convert(args):
    cfg = load_cfg(args.config)
    engines = router.build_engines(cfg)

    # -o 与位置参数统一到一个出口
    if getattr(args, "out_dir", None):
        args.output = args.out_dir

    if os.path.isdir(args.input):
        if not args.to:
            print("错误: 输入是目录时必须用 --to 指定目标格式", file=sys.stderr)
            return 2
        return cmd_tree(args, engines=engines)

    if not os.path.exists(args.input):
        print(f"错误: 源文件不存在: {args.input}", file=sys.stderr)
        return 2

    try:
        dst = resolve_dst(args.input, args, to=args.to)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2

    if os.path.abspath(dst) == os.path.abspath(args.input) and not args.force:
        print("错误: 输出会覆盖源文件，加 --force 才允许", file=sys.stderr)
        return 2

    r = do_one(args.input, dst, engines, args)
    print_result(r, args.verbose)
    if args.json:
        print(json.dumps(r.as_dict(), ensure_ascii=False))
    return 0 if r.ok else 1


def cmd_tree(args, engines=None):
    cfg = load_cfg(args.config)
    engines = engines or router.build_engines(cfg)

    root = Path(args.input)
    if not root.is_dir():
        print(f"错误: 不是目录: {root}", file=sys.stderr)
        return 2

    exts = None
    if args.ext:
        exts = tuple(x.strip().lower().lstrip(".") for x in args.ext.split(",") if x.strip())

    files = []
    pattern = "**/*" if args.recursive else "*"
    for p in root.glob(pattern):
        if not p.is_file():
            continue
        e = p.suffix.lower().lstrip(".")
        if not e:
            continue
        if exts and e not in exts:
            continue
        if e == args.to:
            continue
        files.append(p)

    if not files:
        print(f"在 {root} 下没找到可转换的文件"
              + (f"（筛选扩展名: {args.ext}）" if exts else ""))
        return 1

    out_root = Path(args.output) if args.output else root / f"_{args.to}"
    out_root.mkdir(parents=True, exist_ok=True)

    out_abs = out_root.resolve()

    def in_out_dir(p):
        """输出目录本身要排除，否则第二次运行会把上次的产物再转一遍。"""
        try:
            p.resolve().relative_to(out_abs)
            return True
        except ValueError:
            return False

    files = [f for f in files if not in_out_dir(f)]

    print(f"共 {len(files)} 个文件 → {args.to}，输出根目录 {out_root}"
          f"（并发 {args.jobs}）")

    def work(p):
        rel = p.relative_to(root)
        dst = out_root / rel.with_suffix("." + args.to)
        dst.parent.mkdir(parents=True, exist_ok=True)
        return do_one(p, dst, engines, args)

    ok = fail = 0
    t0 = time.time()
    fails = []
    if args.jobs <= 1:
        for p in files:
            r = work(p)
            print_result(r, args.verbose)
            ok, fail = (ok + 1, fail) if r.ok else (ok, fail + 1)
            if not r.ok:
                fails.append((str(p), r.message))
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as ex:
            futs = {ex.submit(work, p): p for p in files}
            for fut in as_completed(futs):
                p = futs[fut]
                try:
                    r = fut.result()
                except Exception as e:
                    r = ConvResult(False, str(p), "", "", message=str(e))
                print_result(r, args.verbose)
                ok, fail = (ok + 1, fail) if r.ok else (ok, fail + 1)
                if not r.ok:
                    fails.append((str(p), r.message))

    print("-" * 60)
    print(f"成功 {ok} / 失败 {fail}，总耗时 {time.time() - t0:.1f}s")
    if fails:
        print("\n失败清单：")
        for p, msg in fails:
            print(f"  {p}\n    {msg[:200]}")
    return 0 if fail == 0 else 1


def cmd_batch(args):
    # batch 与 tree 共用逻辑，只是输入是文件列表
    cfg = load_cfg(args.config)
    engines = router.build_engines(cfg)
    files = [Path(p) for p in args.inputs if os.path.isfile(p)]
    missing = [p for p in args.inputs if not os.path.isfile(p)]
    for m in missing:
        print(f"[skip] 不存在: {m}", file=sys.stderr)
    if not files:
        print("没有可转换的文件", file=sys.stderr)
        return 2

    outdir = Path(args.output) if args.output else Path(files[0]).parent
    outdir.mkdir(parents=True, exist_ok=True)

    def work(p):
        return do_one(p, outdir / (p.stem + "." + args.to), engines, args)

    ok = fail = 0
    fails = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = {ex.submit(work, p): p for p in files}
        for fut in as_completed(futs):
            p = futs[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = ConvResult(False, str(p), "", "", message=str(e))
            print_result(r, args.verbose)
            ok, fail = (ok + 1, fail) if r.ok else (ok, fail + 1)
            if not r.ok:
                fails.append((str(p), r.message))

    print("-" * 60)
    print(f"成功 {ok} / 失败 {fail}，总耗时 {time.time() - t0:.1f}s")
    if fails:
        print("\n失败清单：")
        for p, msg in fails:
            print(f"  {p}\n    {msg[:200]}")
    return 0 if fail == 0 else 1


def cmd_serve(args):
    from dc import webui
    webui.serve(load_cfg(args.config), host=args.host, port=args.port,
                open_browser=not args.no_browser)
    return 0


def cmd_doctor(args):
    doctor.report(cfg=load_cfg(args.config), color=not args.no_color)
    return 0


def cmd_explain(args):
    cfg = load_cfg(args.config)
    engines = router.build_engines(cfg)
    if args.input and args.output:
        s = Path(args.input).suffix.lstrip(".") or args.input
        d = Path(args.output).suffix.lstrip(".") or args.output
    else:
        s, d = args.source, args.target
    doctor.explain(s, d, cfg=cfg, engines=engines)
    return 0


def cmd_matrix(args):
    cfg = load_cfg(args.config)
    engines = router.build_engines(cfg)
    doctor.report(cfg=cfg, engines=engines, color=not args.no_color)
    return 0


def cmd_formats(args):
    cfg = load_cfg(args.config)
    engines = [e for e in router.build_engines(cfg) if e.available()[0]]
    print("格式族 / 扩展名 / 这台机器上能去哪些族（只列可用引擎）\n")
    for fam, exts in FAMILY.items():
        print(f"  {fam}：{', '.join(exts)}")
        # 该族作为源，实际能到达的目标族（按引擎 targets 精确判断，不靠扩展名集合猜）
        reach = {}
        for s in exts:
            for e in engines:
                for d in e.targets(s):
                    if d in exts:
                        continue
                    reach.setdefault(router.family_of(d), set()).add(e.name)
        if reach:
            line = "  ".join(f"{k}({'/'.join(sorted(v))})" for k, v in reach.items())
            print(f"       → {line}")
        else:
            print("       → （无可用通路）")
        print()
    return 0


# ---------- 参数 ----------

def build_parser():
    p = argparse.ArgumentParser(
        prog="docconvert",
        description="离线通用文件格式转换（文档/表格/演示/PDF/图片/音视频）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例：docconvert convert 合同.docx 合同.pdf\n"
               "    docconvert convert 简历.pdf --to md\n"
               "    docconvert tree ./待转 --to pdf -o ./已转 --recursive",
    )
    p.add_argument("--config", help="配置文件路径（默认 ./config.yaml）")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--engine", help="强制指定引擎（libreoffice/pandoc/pymupdf/"
                                         "pillow/officedoc/tesseract/ffmpeg）")
        sp.add_argument("--ocr", action="store_true", help="优先走 OCR（扫描件场景）")
        sp.add_argument("--dpi", type=int, help="PDF↔图片渲染 dpi（默认 200）")
        sp.add_argument("--sheet", help="xlsx 导 CSV 时指定 sheet（名字或序号）")
        sp.add_argument("--quality", type=int, help="图片质量 1-100（默认 92）")
        sp.add_argument("--lang", help="OCR 语言（默认 chi_sim+eng）")
        sp.add_argument("--fps", type=int, help="视频转 GIF 帧率（默认 12）")
        sp.add_argument("--width", type=int, help="视频转 GIF 宽度（默认 640）")
        sp.add_argument("--reference-doc", help="pandoc 生成 docx 时的样式模板")
        sp.add_argument("--toc", action="store_true", help="生成目录（pandoc）")
        sp.add_argument("--force", action="store_true", help="允许覆盖源文件")
        sp.add_argument("-v", "--verbose", action="store_true")
        sp.add_argument("--json", action="store_true", help="末尾输出 JSON 结果")

    c = sub.add_parser("convert", help="转换单个文件（输入是目录时自动转批量）")
    c.add_argument("input")
    c.add_argument("output", nargs="?", help="输出文件路径或目录")
    c.add_argument("-o", "--out", dest="out_dir", help="输出目录（与位置参数二选一）")
    c.add_argument("--to", help="目标格式（未给 output 时必填）")
    c.add_argument("--ext", help="目录模式下只转这些扩展名，逗号分隔")
    c.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
    c.add_argument("--jobs", type=int, default=1)
    add_common(c)
    c.set_defaults(func=cmd_convert)

    t = sub.add_parser("tree", help="目录树递归批量转换")
    t.add_argument("input")
    t.add_argument("--to", required=True)
    t.add_argument("-o", "--output")
    t.add_argument("--ext", help="只转这些扩展名，如 docx,xlsx")
    t.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
    t.add_argument("--jobs", type=int, default=1)
    add_common(t)
    t.set_defaults(func=cmd_tree)

    b = sub.add_parser("batch", help="批量转换多个文件")
    b.add_argument("inputs", nargs="+")
    b.add_argument("--to", required=True)
    b.add_argument("-o", "--output", help="输出目录")
    b.add_argument("--jobs", type=int, default=1)
    add_common(b)
    b.set_defaults(func=cmd_batch)

    sv = sub.add_parser("serve", help="启动本地 Web 界面（拖拽转换）")
    sv.add_argument("--host", default="127.0.0.1",
                    help="监听地址（默认只允许本机访问，别改成 0.0.0.0）")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    sv.set_defaults(func=cmd_serve)

    d = sub.add_parser("doctor", help="环境体检")
    d.add_argument("--no-color", action="store_true")
    d.set_defaults(func=cmd_doctor)

    e = sub.add_parser("explain", help="解释某条转换会走哪个引擎")
    e.add_argument("input", nargs="?", help="源文件或其扩展名")
    e.add_argument("output", nargs="?", help="目标文件或其扩展名")
    e.add_argument("--from", dest="source", default="")
    e.add_argument("--to", dest="target", default="")
    e.set_defaults(func=cmd_explain)

    m = sub.add_parser("matrix", help="打印能力矩阵")
    m.add_argument("--no-color", action="store_true")
    m.set_defaults(func=cmd_matrix)

    f = sub.add_parser("formats", help="列出支持的扩展名")
    f.set_defaults(func=cmd_formats)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "explain" and not (args.input or args.source):
        print("用法: docconvert explain docx pdf  或  docconvert explain a.docx b.pdf")
        return 2
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())

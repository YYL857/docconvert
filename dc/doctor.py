# -*- coding: utf-8 -*-
"""环境体检 —— 回答一个只有一句话的问题：这台机器现在能转什么，不能转的怎么补。"""
import os
import platform
import sys

from . import router
from .router import FAMILY, family_of


GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def _c(txt, color, enable=True):
    return f"{color}{txt}{RESET}" if enable else str(txt)


def _pad(txt, width):
    """按显示宽度补空格：中文算 2 格。"""
    def w(s):
        return sum(2 if ord(c) > 127 else 1 for c in s)
    return txt + " " * max(width - w(txt), 1)


def report(cfg=None, engines=None, color=None):
    if color is None:
        color = sys.stdout.isatty()
    engines = engines if engines is not None else router.build_engines(cfg)

    print("=" * 74)
    print("docconvert 环境体检")
    print("=" * 74)
    print(f"系统   : {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"Python : {sys.version.split()[0]}  {sys.executable}")
    print()

    print("-" * 74)
    print("引擎状态")
    print("-" * 74)
    ok_list, miss_list = [], []
    avail = {}                    # 只探测一次，后面矩阵复用（否则要跑十几秒）
    for e in engines:
        ok, note = e.available()
        avail[e.name] = ok
        mark = _c("✓ 可用", GREEN, color) if ok else _c("✗ 缺失", RED, color)
        print(f"{mark}  {e.info.name:<12} {e.info.label}")
        print(f"        {DIM}{note}{RESET}" if color else f"        {note}")
        (ok_list if ok else miss_list).append(e)

    ready = sum(1 for e in ok_list)
    print()
    print(f"就绪 {ready}/{len(engines)} 个引擎")

    # 能力矩阵（只统计真正可用的引擎，避免画出「理论上能转」的假象）
    fams, table = router.matrix(engines, only_available=True, avail=avail)

    print()
    print("-" * 74)
    print("能转什么（族级概览；精确到扩展名用 docconvert explain 源 目标）")
    print("-" * 74)
    for fs in fams:
        reach = []
        for fd in fams:
            if fs == fd:
                continue
            v = table.get((fs, fd))
            if v:
                reach.append(f"{fd}({v})")
        if reach:
            print(f"  {_pad(fs, 6)}→  " + "   ".join(reach))
        else:
            print(f"  {_pad(fs, 6)}→  " + _c("（无可用通路）", RED, color))

    if miss_list:
        print()
        print("-" * 74)
        print("补齐缺失引擎（装完重跑 doctor 即可，无需改代码）")
        print("-" * 74)
        seen = set()
        for e in miss_list:
            _, note = e.available()
            key = e.info.name
            if key in seen:
                continue
            seen.add(key)
            print(f"[{key}] {note}")
        print()
        print("一次性装齐（Windows，需要网络；装好后本工具全程离线运行）：")
        print("  winget install TheDocumentFoundation.LibreOffice")
        print("  winget install JohnMacFarlane.Pandoc")
        print("  winget install UB-Mannheim.TesseractOCR")
        print("  winget install Gyan.FFmpeg")
        print("  # 中文 OCR 语言包：把 chi_sim.traineddata 放进 tesseract 的 tessdata/")
    else:
        print()
        print(_c("全部引擎就绪，可覆盖文档 / 表格 / 演示 / PDF / 图片 / 音视频。", GREEN, color))

    return {"ok": ok_list, "missing": miss_list, "matrix": table, "families": fams}


def explain(src, dst, cfg=None, engines=None):
    """解释一条具体转换会走哪个引擎、为什么，以及有无风险提示。"""
    engines = engines if engines is not None else router.build_engines(cfg)
    s = src.lower().lstrip(".")
    d = dst.lower().lstrip(".")
    cands = router.plan(s, d, engines)
    print(f"{s} -> {d}  ({family_of(s)} -> {family_of(d)})")
    if not cands:
        print("  没有任何引擎支持这条转换。跑 doctor 看缺什么。")
        return
    for i, e in enumerate(cands, 1):
        ok, note = e.available()
        tag = "首选" if i == 1 else f"备选{i - 1}"
        state = _c("可用", GREEN) if ok else _c("不可用", RED)
        print(f"  {tag}: {e.info.name:<12} [{state}]  {e.info.note}")
        if not ok:
            print(f"        {note}")

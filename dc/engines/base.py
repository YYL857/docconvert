# -*- coding: utf-8 -*-
"""引擎抽象：所有转换器实现同一套接口，路由层只管挑人，不管细节。"""
from dataclasses import dataclass


@dataclass
class EngineInfo:
    name: str
    label: str
    priority: int          # 同类任务里的优先级，数字越大越优先
    note: str = ""


# 语义上没意义的组合：引擎技术上能转，但产出是垃圾或会误导用户。
# 用「格式族」判断而不是逐个扩展名列举 —— 逐个列必然漏掉 doc/xls/ppt/ods/odp 这些老格式。
DOC_FAM = {"doc", "docx", "docm", "odt", "rtf", "wps"}
SHEET_FAM = {"xls", "xlsx", "xlsm", "ods"}
SLIDE_FAM = {"ppt", "pptx", "pps", "ppsx", "odp"}
TABULAR = SHEET_FAM | {"csv", "tsv"}


def is_meaningful(src_ext, dst_ext):
    """判断这条转换对人有没有意义。

    排除三类：
      1. 同格式互转（docx->docx 没意义）
      2. 文档/演示/PDF 导成表格类 —— 引擎能导出，但结果是「每段一行、没有列概念」
      3. 文档 ↔ 演示 互转 —— 结构模型完全不同，转出来是散架的

    保留的边界情况（刻意不排除）：
      - pdf -> docx：低保真但确实有人要（拿到能改的文字），代码里会警告
      - docx -> png/jpg：导出首页图，有用
      - csv -> docx：把表格做成文档，有用
    """
    if src_ext == dst_ext:
        return False
    # 文档/演示/PDF -> 表格（含 csv/tsv/xlsx/ods）
    if dst_ext in TABULAR and (src_ext in DOC_FAM or src_ext in SLIDE_FAM or src_ext == "pdf"):
        return False
    # 文档 <-> 演示
    if (src_ext in DOC_FAM and dst_ext in SLIDE_FAM) or \
       (src_ext in SLIDE_FAM and dst_ext in DOC_FAM):
        return False
    # 表格 -> 演示
    if src_ext in SHEET_FAM and dst_ext in SLIDE_FAM:
        return False
    return True


class Engine:
    """引擎基类。

    子类只需实现 available() / supports() / convert()。
    convert() 必须返回 ConvResult，且**失败要如实报错**，禁止静默产出空文件。

    精确声明两种方式，优先用 pairs：
        pairs = {"docx": ("txt", "md"), ...}   # 谁 -> 谁，精确到对有意义的组合
        src_exts / dst_exts = (...), (...)     # 集合式，宽但容易放过无意义组合
    """

    info = EngineInfo("base", "基类", 0)
    src_exts: tuple = ()
    dst_exts: tuple = ()
    pairs = None

    # ---------- 必须实现 ----------
    def available(self):
        """返回 (bool, 说明)。说明给 doctor 用，要写清缺什么、怎么补。"""
        raise NotImplementedError

    def convert(self, src, dst, **opts):
        """执行转换，返回 ConvResult。"""
        raise NotImplementedError

    # ---------- 通用实现 ----------
    def supports(self, src_ext, dst_ext):
        if not is_meaningful(src_ext, dst_ext):
            return False
        if self.pairs is not None:
            return dst_ext in self.pairs.get(src_ext, ())
        return src_ext in self.src_exts and dst_ext in self.dst_exts

    def targets(self, src_ext):
        """该引擎从某源格式能去哪些目标格式（用于 formats / 前端下拉框）。"""
        if self.pairs is not None:
            raw = set(self.pairs.get(src_ext, ()))
        elif src_ext in self.src_exts:
            raw = set(self.dst_exts)
        else:
            raw = set()
        return {x for x in raw if is_meaningful(src_ext, x)}

    @property
    def name(self):
        return self.info.name

    def __repr__(self):
        return f"<{self.info.name}>"

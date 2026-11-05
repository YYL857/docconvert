# -*- coding: utf-8 -*-
"""Pillow 引擎 —— 位图族互转 + 图片合成 PDF。

覆盖：png / jpg / jpeg / webp / bmp / tiff / tif / gif / ico / ppm
要点：
    - 透明通道转 JPG 必须铺白底，否则黑色/脏边
    - 动图 GIF 只取首帧（多帧转静态时给 warn）
    - 图片合成 PDF 用 img2pdf 质量最好，但没装则退回 Pillow
"""
import os
from pathlib import Path

from ..utils import ConvResult, ensure_dir
from .base import Engine, EngineInfo

IMG_EXTS = ("png", "jpg", "jpeg", "webp", "bmp", "tiff", "tif", "gif", "ico", "ppm")


class PillowEngine(Engine):
    info = EngineInfo(
        name="pillow",
        label="Pillow",
        priority=50,
        note="位图互转、压缩、截图式转换；纯 Python 依赖",
    )
    src_exts = IMG_EXTS
    dst_exts = IMG_EXTS + ("pdf",)

    def __init__(self, cfg=None):
        cfg = cfg or {}
        self.quality = int(cfg.get("image_quality", 92))

    def available(self):
        try:
            import PIL
            return True, f"Pillow {PIL.__version__}"
        except ImportError:
            return False, "未安装：pip install pillow"

    def convert(self, src, dst, **opts):
        import time
        from PIL import Image, ImageSequence

        t0 = time.time()
        src, dst = os.path.abspath(src), os.path.abspath(dst)
        d_ext = Path(dst).suffix.lower().lstrip(".")
        ensure_dir(dst)

        try:
            img = Image.open(src)
        except Exception as e:
            return ConvResult(False, src, dst, self.name, message=f"打不开图片: {e}")

        warn = []
        # 动图：只取首帧
        if getattr(img, "n_frames", 1) > 1:
            warn.append(f"源为 {img.n_frames} 帧动图，只转换首帧")
            img.seek(0)

        try:
            if d_ext == "pdf":
                return self._to_pdf(img, src, dst, t0, warn)

            if d_ext not in IMG_EXTS:
                return ConvResult(False, src, dst, self.name,
                                  message=f"Pillow 不支持输出 .{d_ext}")

            out = img
            # 透明 -> 白底（JPG/BMP 无 alpha 通道）
            if d_ext in ("jpg", "jpeg", "bmp") and img.mode in ("RGBA", "LA", "P"):
                out = img.convert("RGBA")
                bg = Image.new("RGB", out.size, (255, 255, 255))
                bg.paste(out, mask=out.split()[-1])
                out = bg
            elif img.mode not in ("RGB", "L"):
                out = img.convert("RGB")

            kw = {}
            if d_ext in ("jpg", "jpeg", "webp"):
                kw["quality"] = int(opts.get("quality") or self.quality)
                if d_ext in ("jpg", "jpeg"):
                    kw["optimize"] = True
            if d_ext == "ico":
                kw["sizes"] = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]

            out.save(dst, **kw)
            return ConvResult(True, src, dst, self.name, seconds=time.time() - t0,
                              outputs=[dst], warn=warn)
        except Exception as e:
            return ConvResult(False, src, dst, self.name, seconds=time.time() - t0,
                              message=f"转换失败: {e}")

    def _to_pdf(self, img, src, dst, t0, warn):
        import time
        from PIL import Image
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        img.save(dst, "PDF", resolution=150.0)
        return ConvResult(True, src, dst, self.name, seconds=time.time() - t0,
                          outputs=[dst], warn=warn)

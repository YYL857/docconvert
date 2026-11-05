# -*- coding: utf-8 -*-
"""FFmpeg 引擎 —— 音视频与动图（可选组件，与文档主干解耦）。

为什么放进来：用户的原始需求是「各种文件格式转任意格式」，
音视频是最常见的一类「文件」，缺了它就不算通用工具。
但不装也不影响文档转换。

覆盖：mp4/mkv/avi/mov/webm/flv <-> 同类；视频->音频；音频互转；视频->gif
"""
import os
from pathlib import Path

from ..utils import ConvResult, ensure_dir, find_exe, run_timed
from .base import Engine, EngineInfo

VIDEO = ("mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v", "ts", "mpg", "mpeg")
AUDIO = ("mp3", "wav", "aac", "flac", "ogg", "m4a", "opus", "wma")
GIF = ("gif",)


class FFmpegEngine(Engine):
    info = EngineInfo(
        name="ffmpeg",
        label="FFmpeg",
        priority=30,
        note="音视频互转、抽音轨、转 GIF；可选组件",
    )
    src_exts = VIDEO + AUDIO + GIF
    dst_exts = VIDEO + AUDIO + GIF

    def __init__(self, cfg=None):
        cfg = cfg or {}
        self.bin = self._find_bin(cfg.get("ffmpeg_path"))
        self.timeout = int(cfg.get("media_timeout", 900))

    @staticmethod
    def _find_bin(explicit=None):
        system = []
        for base in (os.environ.get("ProgramFiles", ""), os.environ.get("LOCALAPPDATA", "")):
            if base:
                system.append(os.path.join(base, "ffmpeg", "bin", "ffmpeg.exe"))
        system += ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"]
        if explicit and os.path.isfile(explicit):
            return explicit
        return find_exe("ffmpeg", local_rels=("ffmpeg/bin/ffmpeg.exe", "ffmpeg/ffmpeg.exe",
                                             "ffmpeg.exe"),
                        system_paths=system)

    def available(self):
        if not self.bin:
            return False, "未安装。Windows：winget install Gyan.FFmpeg（或下 builds 解压加 PATH）"
        r, _ = run_timed([self.bin, "-version"], timeout=30)
        if r.code != 0 and not r.out:
            return False, f"{self.bin} 执行异常: {r.err.strip()[:120]}"
        ver = r.out.strip().splitlines()[0] if r.out.strip() else "未知版本"
        return True, f"{self.bin} ({ver[:60]})"

    def convert(self, src, dst, **opts):
        import time
        t0 = time.time()
        src, dst = os.path.abspath(src), os.path.abspath(dst)
        s_ext = Path(src).suffix.lower().lstrip(".")
        d_ext = Path(dst).suffix.lower().lstrip(".")
        ensure_dir(dst)

        if not self.bin:
            return ConvResult(False, src, dst, self.name, message="FFmpeg 未安装")

        cmd = [self.bin, "-y", "-loglevel", "error", "-i", src]

        # 视频 -> GIF：两遍调色板质量最好，这里用单遍 + 缩放，够日常用
        if d_ext == "gif" and s_ext in VIDEO:
            fps = int(opts.get("fps") or 12)
            width = int(opts.get("width") or 640)
            cmd += ["-vf", f"fps={fps},scale={width}:-1:flags=lanczos", "-loop", "0"]
            cmd += [dst]
        elif d_ext in AUDIO and s_ext in VIDEO:
            cmd += ["-vn", "-c:a", "libmp3lame" if d_ext == "mp3" else "copy", dst]
        elif d_ext == "mp3":
            cmd += ["-c:a", "libmp3lame", "-q:a", "2", dst]
        elif d_ext == "wav":
            cmd += ["-c:a", "pcm_s16le", dst]
        elif d_ext == "mp4":
            cmd += ["-c:v", "libx264", "-crf", "20", "-preset", "medium",
                    "-c:a", "aac", "-movflags", "+faststart", dst]
        else:
            cmd += [dst]

        r, _ = run_timed(cmd, timeout=self.timeout)
        if not os.path.exists(dst) or os.path.getsize(dst) == 0:
            msg = (r.err or "").strip()[:300] or "未生成产物"
            return ConvResult(False, src, dst, self.name, seconds=time.time() - t0,
                              message=f"转换失败: {msg}")
        return ConvResult(True, src, dst, self.name, seconds=time.time() - t0, outputs=[dst])

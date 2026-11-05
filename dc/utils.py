# -*- coding: utf-8 -*-
"""公共工具：可执行查找、子进程超时、路径与结果对象。"""
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


# ---------- 路径与可执行 ----------

# 工具根目录与内置引擎目录：绿色部署时引擎放这里，随目录一起搬走
ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"


def find_exe(name, local_rels=(), system_paths=()):
    """按优先级找可执行：项目内置 bin/ > PATH > 系统常见安装路径。

    bin/ 优先是刻意的：整目录拷到没装过引擎的机器时，内置版本要能直接生效，
    不被目标机器上可能存在的旧版干扰。
    """
    for rel in local_rels:
        p = BIN / rel
        if p.is_file():
            return str(p)
    hit = shutil.which(name)
    if hit:
        return hit
    for p in system_paths:
        if p and os.path.isfile(p):
            return str(p)
    return None


def which(name, extra=()):
    """跨平台查找可执行文件：PATH 优先，其次 extra 给的候选绝对路径。"""
    hit = shutil.which(name)
    if hit:
        return hit
    for p in extra:
        if p and os.path.isfile(p):
            return str(p)
    return None


def ensure_dir(p):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    return p


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def ext_of(p):
    return Path(p).suffix.lower().lstrip(".")


def is_probably_text(p, probe=8192):
    """粗判是否纯文本，用于无扩展名/未知文件的兜底。"""
    try:
        with open(p, "rb") as f:
            chunk = f.read(probe)
    except OSError:
        return False
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
        return True
    except UnicodeDecodeError:
        try:
            chunk.decode("gbk")
            return True
        except UnicodeDecodeError:
            return False


# ---------- 子进程 ----------

@dataclass
class RunOut:
    code: int
    out: str
    err: str
    timed_out: bool = False


def _dec(b):
    """Windows 下子进程输出常为 GBK/UTF-8 混杂，按序试探解码。"""
    for enc in ("utf-8", "gbk", "cp1252"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def _kill_tree(pid):
    """强制杀掉整个进程树。

    LibreOffice / ffmpeg 这类程序会 spawn 子进程；只 kill 父进程的话，
    子进程仍持有 stdout 管道，communicate() 会一直等管道关闭 —— 表现为
    「超时设了 40 秒，实际挂了 20 分钟」。这是 Windows 上的经典陷阱。
    """
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=15)
        except Exception:
            pass
    else:
        import signal
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass


def run(cmd, timeout=300, cwd=None, env=None):
    """跑命令，带硬超时；不抛异常，统一返回 RunOut。

    超时会杀整棵进程树并强制回收管道，保证函数一定返回。
    """
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    try:
        p = subprocess.Popen(cmd, cwd=cwd, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    except FileNotFoundError as e:
        return RunOut(-2, "", f"可执行文件不存在: {e}", False)
    except OSError as e:
        return RunOut(-3, "", f"启动失败: {e}", False)

    try:
        out, err = p.communicate(timeout=timeout)
        code = p.returncode
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_tree(p.pid)
        try:
            out, err = p.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            # 管道仍被孙进程占着，直接放弃读，别让上层永远等
            try:
                p.stdout.close()
                p.stderr.close()
            except Exception:
                pass
            out, err = b"", b""
        code, timed_out = -9, True

    return RunOut(code, _dec(out or b""),
                  _dec(err or b"") or (f"超时（>{timeout}s）" if timed_out else ""),
                  timed_out)


def run_timed(cmd, timeout=300, cwd=None, env=None):
    """同 run，额外返回耗时秒数。"""
    t0 = time.time()
    r = run(cmd, timeout=timeout, cwd=cwd, env=env)
    return r, time.time() - t0


# ---------- 结果对象 ----------

@dataclass
class ConvResult:
    ok: bool
    src: str
    dst: str = ""
    engine: str = ""
    seconds: float = 0.0
    outputs: list = field(default_factory=list)   # 实际产物（可能多文件，如逐页图片）
    message: str = ""
    warn: list = field(default_factory=list)

    def as_dict(self):
        return {
            "ok": self.ok, "src": self.src, "dst": self.dst,
            "engine": self.engine, "seconds": round(self.seconds, 2),
            "outputs": self.outputs, "message": self.message, "warn": self.warn,
        }

    def brief(self):
        if self.ok:
            size = ""
            if self.outputs and os.path.isfile(self.outputs[0]):
                size = f" ({human_size(os.path.getsize(self.outputs[0]))})"
            n = f"，{len(self.outputs)} 个产物" if len(self.outputs) > 1 else ""
            return f"OK  [{self.engine}] {self.src} -> {self.dst or self.outputs[0]}{size}{n} {self.seconds:.1f}s"
        return f"FAIL[{self.engine or '?'}] {self.src}: {self.message}"


def die(msg, code=1):
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(code)

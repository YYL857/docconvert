# -*- coding: utf-8 -*-
"""本地 Web UI —— 拖拽上传、选格式、下载结果。

只为「本机自用」设计，安全边界（开源后一定有人挑，别放松）：
    1. 默认只绑 127.0.0.1，不监听局域网；想放开必须显式 --host
    2. 上传体积有上限（默认 512MB）
    3. 客户端文件名只取 basename，绝不参与路径拼接（防目录穿越）
    4. 下载凭一次性 token，不能凭路径读任意文件
    5. 输入/输出都在同一个临时目录里，服务退出时整个删掉

不引入 Flask/FastAPI —— 标准库 http.server 足够，保持「拷走就能跑」。
"""
import json
import mimetypes
import os
import shutil
import tempfile
import threading
import time
import traceback
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import router
from .utils import human_size

UI_FILE = Path(__file__).parent / "ui.html"

MAX_UPLOAD = 512 * 1024 * 1024      # 单文件上限 512MB
OUTPUT_TTL = 3600                  # 产物保留 1 小时
CLEAN_INTERVAL = 600               # 每 10 分钟清一次


# ---------------------------------------------------------------- 产物登记

class Store:
    """内存里的产物登记：token -> (path, expire_at)。

    token 是一次性的下载凭据，客户端拿不到别的 token 就读不到别的文件。
    """

    def __init__(self):
        self._items = {}
        self._lock = threading.Lock()

    def put(self, path, filename):
        token = uuid.uuid4().hex
        with self._lock:
            self._items[token] = {
                "path": str(path),
                "name": filename,
                "expire": time.time() + OUTPUT_TTL,
            }
        return token

    def get(self, token):
        with self._lock:
            item = self._items.get(token)
            if not item:
                return None
            if time.time() > item["expire"] or not os.path.isfile(item["path"]):
                self._items.pop(token, None)
                return None
            return item

    def sweep(self):
        now = time.time()
        with self._lock:
            dead = [k for k, v in self._items.items()
                    if now > v["expire"] or not os.path.isfile(v["path"])]
            for k in dead:
                self._items.pop(k, None)
        return len(dead)


# ---------------------------------------------------------------- 工具

def _safe_name(raw):
    """只保留文件名本体，去掉任何目录成分。

    这是防目录穿越的第一道闸：客户端传 ../../etc/passwd 也只会得到 passwd。
    """
    if not raw:
        return "upload.bin"
    name = os.path.basename(raw.replace("\\", "/"))
    name = name.replace("\x00", "")
    name = "".join(c for c in name if c not in '<>:"|?*')
    return name.strip(". ") or "upload.bin"


def _json(handler, obj, code=HTTPStatus.OK):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _html(handler, text, code=HTTPStatus.OK):
    body = text.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


# ---------------------------------------------------------------- 请求处理

def make_handler(engines, store, workdir):
    root = Path(workdir)

    class Handler(BaseHTTPRequestHandler):
        server_version = "docconvert"
        protocol_version = "HTTP/1.1"

        # ---------- 日志：默认太吵，只在出错时打 ----------
        def log_message(self, fmt, *args):
            pass

        def _send_file(self, path, filename, disposition="attachment"):
            p = Path(path)
            if not p.is_file():
                _json(self, {"ok": False, "error": "文件不存在或已过期"}, HTTPStatus.NOT_FOUND)
                return
            size = p.stat().st_size
            ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            # filename* 用 RFC 5987 编码，中文名不乱码
            ascii_fallback = filename.encode("ascii", "ignore").decode() or "download"
            quoted = quote_utf8(filename)
            self.send_header(
                "Content-Disposition",
                f'{disposition}; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quoted}',
            )
            self.end_headers()
            with open(p, "rb") as f:
                shutil.copyfileobj(f, self.wfile)

        # ---------- GET ----------
        def do_GET(self):
            u = urlparse(self.path)
            path = u.path

            if path in ("/", "/index.html"):
                if not UI_FILE.is_file():
                    _html(self, "<h1>ui.html 缺失</h1>", HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                _html(self, UI_FILE.read_text(encoding="utf-8"))
                return

            if path == "/api/status":
                _json(self, self._status())
                return

            if path.startswith("/api/download/"):
                token = path.rsplit("/", 1)[-1]
                item = store.get(token)
                if not item:
                    _json(self, {"ok": False, "error": "下载链接已过期，请重新转换"},
                          HTTPStatus.NOT_FOUND)
                    return
                self._send_file(item["path"], item["name"])
                return

            _json(self, {"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

        # ---------- PUT /api/convert（body 就是文件原始字节）----------
        def do_PUT(self):
            u = urlparse(self.path)
            if u.path != "/api/convert":
                _json(self, {"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)
                return

            qs = parse_qs(u.query)
            to_ext = (qs.get("to", [""])[0] or "").lower().lstrip(".")
            raw_name = unquote(qs.get("name", [""])[0])
            force_engine = qs.get("engine", [""])[0] or None
            use_ocr = qs.get("ocr", ["0"])[0] == "1"

            if not to_ext:
                _json(self, {"ok": False, "error": "缺少目标格式 ?to="}, HTTPStatus.BAD_REQUEST)
                return

            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            if length <= 0:
                _json(self, {"ok": False, "error": "空文件"}, HTTPStatus.BAD_REQUEST)
                return
            if length > MAX_UPLOAD:
                _json(self, {
                    "ok": False,
                    "error": f"文件超过上限（{human_size(length)} > {human_size(MAX_UPLOAD)}）",
                }, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return

            name = _safe_name(raw_name)
            src_ext = Path(name).suffix.lower().lstrip(".")

            # 每次转换一个独立子目录，避免同名文件互相覆盖
            job_dir = root / "in" / uuid.uuid4().hex
            job_dir.mkdir(parents=True, exist_ok=True)
            src_path = job_dir / name
            dst_path = job_dir / (Path(name).stem + "." + to_ext)

            try:
                remain = length
                with open(src_path, "wb") as f:
                    while remain > 0:
                        chunk = self.rfile.read(min(65536, remain))
                        if not chunk:
                            break
                        f.write(chunk)
                        remain -= len(chunk)
                if remain > 0:
                    raise IOError("上传中断，数据不完整")

                t0 = time.time()
                result = router.convert(
                    str(src_path), str(dst_path), engines,
                    force=force_engine, ocr=use_ocr,
                )
                elapsed = result.seconds or (time.time() - t0)

                if not result.ok:
                    _json(self, {
                        "ok": False, "engine": result.engine, "seconds": round(elapsed, 2),
                        "error": result.message,
                    })
                    return

                produced = result.outputs[0] if result.outputs else str(dst_path)
                out_name = Path(produced).name
                if len(result.outputs) > 1:
                    # 多产物（PDF 逐页转图）打包成 zip 再给
                    import zipfile
                    zpath = job_dir / (Path(name).stem + "_" + to_ext + ".zip")
                    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                        for o in result.outputs:
                            if os.path.isfile(o):
                                z.write(o, Path(o).name)
                    produced, out_name = str(zpath), zpath.name

                token = store.put(produced, out_name)
                _json(self, {
                    "ok": True,
                    "engine": result.engine,
                    "seconds": round(elapsed, 2),
                    "size": os.path.getsize(produced),
                    "size_human": human_size(os.path.getsize(produced)),
                    "output": out_name,
                    "download": f"/api/download/{token}",
                    "warn": result.warn,
                    "src_ext": src_ext,
                    "dst_ext": to_ext,
                })
            except Exception as e:
                _json(self, {
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                    "trace": traceback.format_exc()[-600:],
                }, HTTPStatus.INTERNAL_SERVER_ERROR)
            finally:
                # 输入文件即时清理；产物由 store 的 TTL 管
                try:
                    if src_path.exists():
                        src_path.unlink()
                except OSError:
                    pass

        # ---------- 状态 ----------
        def _status(self):
            eng = []
            avail = {}
            for e in engines:
                ok, note = e.available()
                avail[e.name] = ok
                eng.append({
                    "name": e.name, "label": e.info.label,
                    "ok": ok, "note": note, "priority": e.info.priority,
                })

            fams, table = router.matrix(engines, only_available=True, avail=avail)
            matrix = {}
            for (fs, fd), v in table.items():
                if v:
                    matrix.setdefault(fs, {})[fd] = v

            # 每种源扩展名实际能去哪些目标扩展名（前端据此过滤下拉框）
            reach = {}
            for fam, exts in router.FAMILY.items():
                for s in exts:
                    targets = set()
                    for e in engines:
                        if not avail.get(e.name):
                            continue
                        targets |= e.targets(s)
                    if targets:
                        reach[s] = sorted(targets)

            return {
                "engines": eng,
                "families": {k: list(v) for k, v in router.FAMILY.items()},
                "matrix": matrix,
                "reach": reach,
                "max_upload": MAX_UPLOAD,
                "max_upload_human": human_size(MAX_UPLOAD),
                "ready": sum(1 for x in eng if x["ok"]),
                "total": len(eng),
            }

    return Handler


def quote_utf8(s):
    from urllib.parse import quote
    return quote(s, safe="")


# ---------------------------------------------------------------- 启动

def serve(cfg=None, host="127.0.0.1", port=8765, open_browser=True):
    cfg = cfg or {}
    engines = router.build_engines(cfg)
    workdir = tempfile.mkdtemp(prefix="docconvert-web-")
    store = Store()

    handler = make_handler(engines, store, workdir)
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True

    def cleaner():
        while True:
            time.sleep(CLEAN_INTERVAL)
            store.sweep()
            # 清掉超过 2 小时的 job 目录
            cutoff = time.time() - 7200
            for sub in (Path(workdir) / "in").glob("*"):
                try:
                    if sub.is_dir() and sub.stat().st_mtime < cutoff:
                        shutil.rmtree(sub, ignore_errors=True)
                except OSError:
                    pass

    threading.Thread(target=cleaner, daemon=True).start()

    url = f"http://{host}:{port}/"
    ready = sum(1 for e in engines if e.available()[0])
    print(f"docconvert Web UI  →  {url}")
    print(f"引擎就绪 {ready}/{len(engines)}   上传上限 {human_size(MAX_UPLOAD)}")
    print(f"临时目录 {workdir}")
    print("Ctrl+C 停止")

    if open_browser:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭...")
    finally:
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(workdir, ignore_errors=True)
        print("已清理临时文件，退出。")

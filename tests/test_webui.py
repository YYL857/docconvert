# -*- coding: utf-8 -*-
"""Web UI 集成测试：跑通接口 + 验证安全边界。

安全项是重点 —— 开源后这几条最容易被人拿来做文章，必须用测试锁住：
    1. 目录穿越：name=../../etc/passwd 只能落到 job 目录内
    2. 缺参数：不给 ?to= 要 400，不能默认瞎转
    3. 过期 token：下载要 404，不能读到别的文件
    4. 非 PUT 打 /api/convert：不能当 GET 用

用法：python tests/test_webui.py
"""
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from dc import router, webui                      # noqa: E402

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    mark = f"{GREEN}PASS{RESET}" if cond else f"{RED}FAIL{RESET}"
    print(f"{mark}  {name}" + (f"  {detail}" if detail and not cond else ""))
    return cond


def start_server():
    engines = router.build_engines({})
    workdir = tempfile.mkdtemp(prefix="dc-webui-test-")
    store = webui.Store()
    handler = webui.make_handler(engines, store, workdir)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port, store, Path(workdir)


def req(port, path, method="GET", body=None, headers=None):
    url = f"http://127.0.0.1:{port}{path}"
    r = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=240) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def main():
    srv, port, store, workdir = start_server()
    print("=" * 66)
    print(f"Web UI 集成测试  (127.0.0.1:{port})")
    print("=" * 66)

    try:
        # ---- 1. 页面 ----
        code, body, _ = req(port, "/")
        check("GET / 返回页面", code == 200 and b"docconvert" in body,
              f"code={code}")

        # ---- 2. 状态接口 ----
        code, body, _ = req(port, "/api/status")
        st = json.loads(body) if code == 200 else {}
        check("GET /api/status", code == 200 and "engines" in st, f"code={code}")
        check("status 含能力矩阵", bool(st.get("matrix")), "matrix 为空")
        check("status 含可达目标表", bool(st.get("reach")), "reach 为空")
        check("status 报引擎就绪数", st.get("ready", 0) > 0,
              f"ready={st.get('ready')}/{st.get('total')}")

        # ---- 3. 真实转换（用 _work 里的真实文件，不用造样例）----
        src = ROOT / "_work" / "劳动合同-业务岗.docx"
        if not src.is_file():
            src = None
            for cand in (ROOT / "_work").glob("*.docx"):
                src = cand
                break
        if src:
            data = src.read_bytes()
            q = urllib.parse.quote(src.name)
            code, body, _ = req(port, f"/api/convert?to=txt&name={q}",
                                method="PUT", body=data,
                                headers={"Content-Length": str(len(data))})
            j = json.loads(body) if body else {}
            check("PUT /api/convert docx→txt", code == 200 and j.get("ok"),
                  f"code={code} err={j.get('error', '')[:60]}")
            if j.get("ok"):
                check("返回引擎名", bool(j.get("engine")), "")
                check("返回下载链接", j.get("download", "").startswith("/api/download/"), "")

                # ---- 4. 下载 ----
                code2, payload, hdrs = req(port, j["download"])
                ok_dl = code2 == 200 and len(payload) > 100
                check("下载产物非空", ok_dl, f"code={code2} len={len(payload)}")
                check("中文文件名不乱码",
                      "filename*=UTF-8" in hdrs.get("Content-Disposition", ""),
                      hdrs.get("Content-Disposition", "")[:80])
        else:
            print(f"{YELLOW}SKIP{RESET}  无真实 docx 可测（_work 为空）")

        # ---- 5. 安全：目录穿越 ----
        code, body, _ = req(port, "/api/convert?to=txt&name=..%2F..%2Fevil.docx",
                            method="PUT", body=b"hello", headers={"Content-Length": "5"})
        # 未消毒时 ../../evil.docx 会落到 workdir/evil.docx（job_dir 向上两级）
        escaped = [(workdir / "evil.docx"), (workdir / "evil.txt"),
                   (workdir.parent / "evil.docx")]
        leaked = [str(p) for p in escaped if p.exists()]
        check("目录穿越被阻断", not leaked, f"越界文件={leaked}")

        # ---- 6. 安全：缺 to 参数 ----
        code, body, _ = req(port, "/api/convert?name=x.docx", method="PUT",
                            body=b"x", headers={"Content-Length": "1"})
        check("缺 ?to= 返回 400", code == 400, f"code={code}")

        # ---- 7. 安全：不存在的 token ----
        code, body, _ = req(port, "/api/download/deadbeef")
        check("无效 token 返回 404", code == 404, f"code={code}")

        # ---- 8. 安全：GET 打转换接口 ----
        code, body, _ = req(port, "/api/convert?to=txt&name=x.docx")
        check("GET /api/convert 拒绝", code == 404, f"code={code}")

        # ---- 9. 安全：空 body ----
        code, body, _ = req(port, "/api/convert?to=txt&name=e.docx", method="PUT",
                            body=b"", headers={"Content-Length": "0"})
        check("空文件返回 400", code == 400, f"code={code}")

    finally:
        srv.shutdown()
        srv.server_close()

    npass = sum(1 for _, ok, _ in results if ok)
    nfail = len(results) - npass
    print("-" * 66)
    print(f"通过 {npass} / 失败 {nfail}（共 {len(results)}）")
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

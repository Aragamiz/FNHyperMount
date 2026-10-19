"""
FNHyperMount - Lightweight Zero-Dependency REST API & Web Server
完全基于 Python 标准库构建，无需安装任何 pip 第三方依赖
"""

import json
import os
import sys
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from config import DEFAULT_PORT, DEFAULT_HOST, PRESETS, MSG_NFS_PERMISSION_ERROR
from mount_manager import MountManager
from probe_engine import ping_host, check_nfs_port, discover_nfs_exports

# 前端静态目录
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

manager = MountManager()


class HyperMountHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def log_message(self, format, *args):
        # 精简控制台日志
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("X-Frame-Options", "ALLOWALL")
        self.send_header("Content-Security-Policy", "frame-ancestors *")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("X-Frame-Options", "ALLOWALL")
        self.send_header("Content-Security-Policy", "frame-ancestors *")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/mounts":
            mounts = manager.get_all_mounts()
            self._send_json({"ok": True, "mounts": mounts})
            return

        elif path == "/api/presets":
            self._send_json({"ok": True, "presets": PRESETS})
            return

        # 其他静态文件交给 SimpleHTTPRequestHandler
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            params = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            params = {}

        # 1. 探测对端服务器与共享目录
        if path == "/api/probe/server":
            host = params.get("host", "").strip()
            if not host:
                self._send_json({"ok": False, "error": "请输入目标机器 IP 或主机名"}, 400)
                return

            ping_res = ping_host(host, timeout=1.5)
            port_res = check_nfs_port(host, 2049, timeout=1.5) if ping_res["ok"] else {"ok": False, "msg": "网络不可达"}
            exports_res = discover_nfs_exports(host, timeout=4.0) if port_res["ok"] else {
                "ok": False,
                "exports": [],
                "error": MSG_NFS_PERMISSION_ERROR
            }

            self._send_json({
                "ok": True,
                "host": host,
                "ping": ping_res,
                "port": port_res,
                "exports": exports_res.get("exports", []),
                "export_error": exports_res.get("error")
            })
            return

        # 2. 向导添加新挂载
        elif path == "/api/mounts/add":
            server = params.get("server", "").strip()
            export_path = params.get("export", "").strip()
            mountpoint = params.get("mountpoint", "").strip()
            preset = params.get("preset", "automount")
            custom_opts = params.get("options", "").strip()

            if not server or not export_path or not mountpoint:
                self._send_json({"ok": False, "error": "请完整填写目标服务器、远端共享目录和本地挂载路径"}, 400)
                return

            res = manager.add_nfs_mount(server, export_path, mountpoint, preset, custom_opts)
            self._send_json(res)
            return

        # 3. 移除挂载
        elif path == "/api/mounts/remove":
            mountpoint = params.get("mountpoint", "").strip()
            if not mountpoint:
                self._send_json({"ok": False, "error": "缺少 mountpoint 参数"}, 400)
                return
            res = manager.remove_nfs_mount(mountpoint)
            self._send_json(res)
            return

        # 4. 重新挂载
        elif path == "/api/mounts/remount":
            mountpoint = params.get("mountpoint", "").strip()
            if not mountpoint:
                self._send_json({"ok": False, "error": "缺少 mountpoint 参数"}, 400)
                return
            res = manager.remount(mountpoint)
            self._send_json(res)
            return

        # 5. 单个挂载点深度健康体检
        elif path == "/api/mounts/health":
            mountpoint = params.get("mountpoint", "").strip()
            server = params.get("server", "").strip()
            if not mountpoint:
                self._send_json({"ok": False, "error": "缺少 mountpoint 参数"}, 400)
                return
            report = manager.health_check_mount(mountpoint, server if server else None)
            self._send_json({"ok": True, "report": report})
            return

        self._send_json({"ok": False, "error": "Not Found"}, 404)


def run_server(port=DEFAULT_PORT, host=DEFAULT_HOST):
    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, HyperMountHandler)
    print(f"==================================================")
    print(f"🚀 FNHyperMount 服务已启动: http://{host}:{port}")
    print(f"📂 静态页面目录: {FRONTEND_DIR}")
    print(f"==================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.server_close()


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    run_server(port=p)

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable


class _PACHandler(BaseHTTPRequestHandler):
    server_version = "GitHubFixPAC/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] not in ("/githubfix.pac", "/proxy.pac"):
            self.send_error(404)
            return
        body = self.server.pac_provider().encode("utf-8")  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ns-proxy-autoconfig; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        return


class PACServer:
    def __init__(self, pac_provider: Callable[[], str]):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _PACHandler)
        self._httpd.daemon_threads = True
        self._httpd.pac_provider = pac_provider  # type: ignore[attr-defined]
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="GitHubFixPAC", daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/githubfix.pac"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        except Exception:
            pass

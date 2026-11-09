from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time
from dataclasses import dataclass, field

from .probe import probe_candidates
from .resolver import resolve
from .sources import FeedData, build_candidates
from .hosts import read_hosts


GITHUB_SUFFIXES = (
    "github.com",
    "githubusercontent.com",
    "githubassets.com",
    "github.io",
)
DEFAULT_PORT = 17890


def is_github_host(host: str) -> bool:
    host = host.lower().strip(".")
    return any(host == suffix or host.endswith("." + suffix) for suffix in GITHUB_SUFFIXES)


def pac_script(proxy_port: int) -> str:
    domains = " || ".join(
        f'host == "{d}" || dnsDomainIs(host, ".{d}")' for d in GITHUB_SUFFIXES
    )
    return f"""// GitHubFix 自动生成的 PAC：仅将 GitHub 的 HTTPS 流量交给本地智能连接器。
function FindProxyForURL(url, host) {{
  host = host.toLowerCase();
  if (url.substring(0, 6).toLowerCase() == "https:" && ({domains})) {{
    return "PROXY 127.0.0.1:{proxy_port}; DIRECT";
  }}
  return "DIRECT";
}}
"""


@dataclass
class ProxyStats:
    started_at: float = field(default_factory=time.time)
    connections: int = 0
    successes: int = 0
    failures: int = 0
    active: int = 0
    bytes_up: int = 0
    bytes_down: int = 0
    last_error: str = ""
    last_host: str = ""

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "connections": self.connections,
            "successes": self.successes,
            "failures": self.failures,
            "active": self.active,
            "bytes_up": self.bytes_up,
            "bytes_down": self.bytes_down,
            "last_error": self.last_error,
            "last_host": self.last_host,
        }


class HostRouter:
    """Resolve and rank IPs for a host, with a short-lived cache."""

    def __init__(self, ttl: float = 240.0):
        self.ttl = ttl
        self._cache: dict[str, tuple[float, list[str]]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, host: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(host, threading.Lock())

    def _all_candidates(self, host: str) -> dict[str, dict[str, list[str]]]:
        resolution = resolve(host, timeout=4.0, include_system=False)
        try:
            pinned = read_hosts().entries
        except Exception:
            pinned = {}
        candidates = build_candidates({host: resolution}, FeedData(), pinned)
        return candidates.get(host, {})

    def ranked_ips(self, host: str, *, force: bool = False) -> list[str]:
        host = host.lower().strip(".")
        now = time.time()
        cached = self._cache.get(host)
        if cached and not force and now - cached[0] < self.ttl:
            return cached[1]
        lock = self._lock_for(host)
        if not lock.acquire(blocking=False):
            # Another thread is refreshing; use the previous list if available.
            if cached:
                return cached[1]
            lock.acquire()
        try:
            cached = self._cache.get(host)
            if cached and not force and time.time() - cached[0] < self.ttl:
                return cached[1]
            candidates = self._all_candidates(host)
            results = probe_candidates(host, candidates, max_candidates=6, timeout=2.8, samples=1)
            ranked = [item.ip for item in results if item.ok]
            if not ranked:
                # 没有通过 TLS 的候选也保留下来做 TCP 竞速，避免完全不可用。
                for source in sorted(candidates, key=lambda s: (not s.startswith("系统"), s)):
                    for ip in candidates[source]:
                        if ip not in ranked:
                            ranked.append(ip)
            self._cache[host] = (time.time(), ranked)
            return ranked
        finally:
            lock.release()

    def warm(self, hosts: list[str], force: bool = False) -> None:
        for host in hosts:
            try:
                self.ranked_ips(host, force=force)
            except Exception:
                pass

    def invalidate(self, host: str | None = None) -> None:
        if host:
            self._cache.pop(host.lower(), None)
        else:
            self._cache.clear()


class SmartProxy:
    def __init__(self, preferred_port: int = DEFAULT_PORT, router: HostRouter | None = None):
        self.preferred_port = preferred_port
        self.router = router or HostRouter()
        self.port = 0
        self.stats = ProxyStats()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: asyncio.AbstractServer | None = None
        self._ready = threading.Event()
        self._stop_event: asyncio.Event | None = None
        self._tasks: set[asyncio.Task] = set()
        self._error = ""

    @property
    def running(self) -> bool:
        return bool(self._server and self._server.is_serving())

    def start(self, timeout: float = 8.0) -> tuple[bool, str]:
        if self.running:
            return True, f"本地智能连接器已在 127.0.0.1:{self.port} 运行"
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="GitHubFixProxy", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            return False, "启动本地智能连接器超时"
        if self.port:
            return True, f"本地智能连接器已启动: 127.0.0.1:{self.port}"
        return False, self._error or "本地智能连接器启动失败"

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._stop_event = asyncio.Event()
        try:
            loop.run_until_complete(self._serve())
            loop.run_forever()
        except Exception as exc:
            self._error = str(exc)
            self._ready.set()
        finally:
            with contextlib.suppress(Exception):
                loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    async def _serve(self) -> None:
        last_error = ""
        for port in range(self.preferred_port, self.preferred_port + 12):
            try:
                self._server = await asyncio.start_server(self._handle_client, "127.0.0.1", port)
                self.port = port
                break
            except OSError as exc:
                last_error = str(exc)
        if not self._server:
            self._error = f"端口被占用: {last_error}"
            self._ready.set()
            return
        self._ready.set()
        # 后台预热常用域名，避免浏览器首个请求等待探测。
        for host in ("github.com", "api.github.com", "raw.githubusercontent.com", "github.githubassets.com", "codeload.github.com", "avatars.githubusercontent.com", "objects.githubusercontent.com"):
            task = asyncio.create_task(asyncio.to_thread(self.router.warm, [host]))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        monitor = asyncio.create_task(self._monitor())
        self._tasks.add(monitor)
        monitor.add_done_callback(self._tasks.discard)
        # 事件循环在 _run 中保持运行；这里只负责启动监听。

    async def _monitor(self) -> None:
        while True:
            await asyncio.sleep(180)
            if self._server is None:
                return
            try:
                await asyncio.to_thread(self.router.warm, ["github.com", "api.github.com"], True)
            except Exception:
                pass

    def stop(self, timeout: float = 5.0) -> None:
        if self._loop and self._stop_event:
            def _shutdown():
                for task in list(self._tasks):
                    task.cancel()
                if self._server:
                    self._server.close()
                if self._stop_event:
                    self._stop_event.set()
                self._loop.stop()

            self._loop.call_soon_threadsafe(_shutdown)
        if self._thread:
            self._thread.join(timeout=timeout)
        self._server = None
        self.port = 0

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.stats.connections += 1
        self.stats.active += 1
        remote_writer: asyncio.StreamWriter | None = None
        try:
            try:
                header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
                await self._respond(writer, 400, "Bad Request")
                return
            text = header.decode("iso-8859-1", "replace")
            first_line = text.splitlines()[0] if text else ""
            parts = first_line.split()
            if len(parts) < 2 or parts[0].upper() != "CONNECT":
                await self._respond(writer, 405, "Only CONNECT is supported")
                return
            authority = parts[1]
            host, _, port_text = authority.rpartition(":")
            if not host or not port_text.isdigit():
                await self._respond(writer, 400, "Invalid CONNECT target")
                return
            port = int(port_text)
            host = host.strip("[]").lower()
            if port != 443 or not is_github_host(host):
                await self._respond(writer, 403, "GitHubFix only proxies GitHub HTTPS")
                return
            self.stats.last_host = host
            ips = await asyncio.to_thread(self.router.ranked_ips, host)
            if not ips:
                ips = await asyncio.to_thread(self._generic_ips, host)
            if not ips:
                self.stats.failures += 1
                await self._respond(writer, 502, "No candidate IP")
                return
            try:
                remote_reader, remote_writer = await self._race_connect(ips, port)
            except OSError:
                # 缓存的 IP 可能刚刚失效，强制刷新后再试一次。
                ips = await asyncio.to_thread(self.router.ranked_ips, host, True)
                if not ips:
                    raise
                remote_reader, remote_writer = await self._race_connect(ips, port)
            writer.write(b"HTTP/1.1 200 Connection Established\r\nProxy-Agent: GitHubFix/1.0\r\n\r\n")
            await writer.drain()
            self.stats.successes += 1
            await self._relay(reader, writer, remote_reader, remote_writer)
        except Exception as exc:
            self.stats.failures += 1
            self.stats.last_error = f"{type(exc).__name__}: {exc}"
            with contextlib.suppress(Exception):
                await self._respond(writer, 502, "Upstream connection failed")
        finally:
            self.stats.active = max(0, self.stats.active - 1)
            if remote_writer is not None:
                with contextlib.suppress(Exception):
                    remote_writer.close()
            with contextlib.suppress(Exception):
                writer.close()

    @staticmethod
    def _generic_ips(host: str) -> list[str]:
        import ipaddress

        result: list[str] = []
        try:
            for info in socket.getaddrinfo(host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM):
                ip = info[4][0]
                try:
                    addr = ipaddress.ip_address(ip)
                except ValueError:
                    continue
                if not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast):
                    result.append(ip)
        except OSError:
            pass
        return list(dict.fromkeys(result))

    async def _race_connect(self, ips: list[str], port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        timeout = 2.8
        candidates = ips[:4]
        tasks: set[asyncio.Task] = set()
        for ip in candidates:
            tasks.add(asyncio.create_task(self._connect_one(ip, port, timeout)))
        pending = set(tasks)
        last_error: Exception | None = None
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED, timeout=timeout + 0.5)
                if not done:
                    break
                for task in done:
                    try:
                        result = task.result()
                        for other in pending:
                            other.cancel()
                        for other in pending:
                            with contextlib.suppress(asyncio.CancelledError, Exception):
                                await other
                        return result
                    except Exception as exc:
                        last_error = exc
            raise OSError(f"所有候选 IP 均连接失败: {last_error}")
        finally:
            for task in pending:
                task.cancel()

    @staticmethod
    async def _connect_one(ip: str, port: int, timeout: float):
        return await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)

    async def _relay(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        remote_reader: asyncio.StreamReader,
        remote_writer: asyncio.StreamWriter,
    ) -> None:
        async def pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, upload: bool):
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
                if upload:
                    self.stats.bytes_up += len(data)
                else:
                    self.stats.bytes_down += len(data)
            with contextlib.suppress(Exception):
                writer.write_eof()

        up = asyncio.create_task(pump(client_reader, remote_writer, True))
        down = asyncio.create_task(pump(remote_reader, client_writer, False))
        done, pending = await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    @staticmethod
    async def _respond(writer: asyncio.StreamWriter, code: int, message: str) -> None:
        reason = {400: "Bad Request", 403: "Forbidden", 405: "Method Not Allowed", 502: "Bad Gateway"}.get(code, "Error")
        body = (message + "\n").encode("utf-8")
        writer.write(
            f"HTTP/1.1 {code} {reason}\r\nContent-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii") + body
        )
        await writer.drain()

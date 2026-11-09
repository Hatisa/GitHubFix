from __future__ import annotations

import ipaddress
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable

from .models import DOMAIN_BY_HOST, ProbeResult


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GitHubFix/1.0"
# 限制全局并发，避免短时间内向 GitHub 发起过多连接反而触发限速。
_PROBE_SEMAPHORE = threading.Semaphore(8)


def _http_status(sock: socket.socket, host: str, path: str) -> tuple[int | None, str]:
    request = (
        f"HEAD {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"User-Agent: {USER_AGENT}\r\n"
        "Accept: */*\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii", "replace")
    sock.sendall(request)
    buffer = b""
    while b"\r\n\r\n" not in buffer and len(buffer) < 16384:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buffer += chunk
    header = buffer.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1", "replace")
    if not header:
        return None, "服务器未返回 HTTP 响应"
    first = header.splitlines()[0].strip()
    parts = first.split()
    if len(parts) < 2 or not parts[0].startswith("HTTP/"):
        return None, f"无效的 HTTP 响应: {first[:80]}"
    try:
        return int(parts[1]), ""
    except ValueError:
        return None, f"无效的 HTTP 状态: {parts[1][:20]}"


def _ssh_banner(sock: socket.socket) -> tuple[str, str]:
    sock.settimeout(4.0)
    data = sock.recv(255)
    banner = data.decode("ascii", "replace").strip()
    if not banner.startswith("SSH-"):
        return "", f"未收到 SSH banner: {banner[:60]}"
    return banner, ""


def probe_ip(
    host: str,
    ip: str,
    *,
    source: str = "",
    timeout: float = 4.0,
    path: str | None = None,
) -> ProbeResult:
    spec = DOMAIN_BY_HOST.get(host)
    probe_path = path or (spec.path if spec else "/")
    kind = spec.kind if spec else "https"
    result = ProbeResult(host=host, ip=ip, source=source)
    with _PROBE_SEMAPHORE:
        return _probe_locked(result, host, ip, timeout, probe_path, kind)


def _probe_locked(result: ProbeResult, host: str, ip: str, timeout: float, probe_path: str, kind: str) -> ProbeResult:
    sock: socket.socket | None = None
    try:
        started = time.perf_counter()
        sock = socket.create_connection((ip, 443), timeout=timeout)
        result.tcp_ms = round((time.perf_counter() - started) * 1000, 1)
        if kind == "ssh":
            banner, error = _ssh_banner(sock)
            if banner:
                result.ssh_banner = banner
                result.ok = True
                result.score = result.tcp_ms
            else:
                result.error = error
            return result
        context = ssl.create_default_context()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        tls_started = time.perf_counter()
        tls = context.wrap_socket(sock, server_hostname=host)
        sock = tls
        result.tls_ms = round((time.perf_counter() - tls_started) * 1000, 1)
        http_started = time.perf_counter()
        status, error = _http_status(tls, host, probe_path)
        result.http_ms = round((time.perf_counter() - http_started) * 1000, 1)
        result.status = status
        if status is None:
            result.error = error
        elif status < 500:
            result.ok = True
            result.score = (result.tcp_ms or 0) * 0.45 + (result.tls_ms or 0) * 0.4 + (result.http_ms or 0) * 0.15
            if status >= 400:
                result.score += 25
        else:
            result.error = f"HTTP {status}"
            result.score = 900000 + status
    except ssl.SSLCertVerificationError as exc:
        result.error = f"TLS 证书校验失败: {exc.verify_message or exc}"
        result.score = 800000
    except (socket.timeout, TimeoutError):
        result.error = "连接超时"
        result.score = 700000
    except ConnectionResetError:
        result.error = "连接被重置（可能被干扰）"
        result.score = 600000
    except OSError as exc:
        result.error = str(exc)
        result.score = 500000
    except Exception as exc:  # 防御性处理，单个候选失败不影响整体
        result.error = f"{type(exc).__name__}: {exc}"
        result.score = 400000
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    return result


def _looks_like_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(str(value))
        return True
    except ValueError:
        return False


def _normalize_candidates(candidates: dict[str, Iterable[str]]) -> dict[str, list[str]]:
    """Accept either source -> IPs, or IP -> sources, and return source -> IPs."""
    if not candidates:
        return {}
    keys = list(candidates)
    if all(_looks_like_ip(key) for key in keys):
        grouped: dict[str, list[str]] = {}
        for ip, sources in candidates.items():
            if isinstance(sources, str):
                sources = [sources]
            for source in sources:
                grouped.setdefault(str(source), []).append(ip)
        return grouped
    return {str(source): [str(ip) for ip in ips] for source, ips in candidates.items()}


def _run_batch(host: str, ordered: list[tuple[str, str]], timeout: float) -> list[ProbeResult]:
    results: list[ProbeResult] = []
    if not ordered:
        return results
    with ThreadPoolExecutor(max_workers=min(8, len(ordered))) as pool:
        futures = {
            pool.submit(probe_ip, host, ip, source=source, timeout=timeout): (ip, source)
            for ip, source in ordered
        }
        for future in as_completed(futures):
            ip, source = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = ProbeResult(host=host, ip=ip, source=source, error=str(exc), score=999999)
            results.append(result)
    return results


def probe_candidates(
    host: str,
    candidates: dict[str, Iterable[str]],
    *,
    max_candidates: int = 6,
    timeout: float = 3.6,
    samples: int = 2,
    progress: Callable[[str], None] | None = None,
) -> list[ProbeResult]:
    """Probe candidates, optionally re-testing the fastest ones for stability.

    The mapping may be source -> IPs (preferred) or IP -> sources.
    """
    candidates = _normalize_candidates(candidates)
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    # 系统解析结果通常来自当前 hosts，可能已失效；放在后面。
    for source in sorted(candidates, key=lambda s: (s.startswith("系统"), s)):
        for ip in candidates[source]:
            if ip in seen:
                continue
            seen.add(ip)
            ordered.append((ip, source))
            if len(ordered) >= max_candidates:
                break
        if len(ordered) >= max_candidates:
            break
    if not ordered:
        return []

    first = _run_batch(host, ordered, timeout)
    by_ip: dict[str, ProbeResult] = {result.ip: result for result in first}
    for result in first:
        result.attempts = 1
        result.successes = 1 if result.ok else 0
        result.stability = 1.0 if result.ok else 0.0
        if progress:
            progress(f"{host}  {result.ip}  {'可用' if result.ok else result.error or '失败'}")

    if samples > 1:
        # 成功的候选优先复测；同时给首轮失败的候选一次机会，避免一次抖动误杀可用 IP。
        retry = [result for result in first if result.ok][:3]
        retry += [result for result in first if not result.ok][:2]
        if retry:
            retry_ordered = [(result.ip, result.source) for result in retry]
            second = _run_batch(host, retry_ordered, timeout)
            for result in second:
                previous = by_ip[result.ip]
                previous.attempts = 2
                previous.successes += 1 if result.ok else 0
                previous.stability = previous.successes / 2
                if result.ok:
                    previous_total = (previous.tcp_ms or 0) + (previous.tls_ms or 0) + (previous.http_ms or 0)
                    result_total = (result.tcp_ms or 0) + (result.tls_ms or 0) + (result.http_ms or 0)
                    if not previous.ok or result_total < previous_total:
                        previous.ok = True
                        previous.tcp_ms = result.tcp_ms
                        previous.tls_ms = result.tls_ms
                        previous.http_ms = result.http_ms
                        previous.status = result.status
                        previous.error = ""
                    previous.score = min(previous.score if previous.ok else 999999, result.score)
                    if previous.stability < 1:
                        previous.score += 250
                else:
                    previous.score += 400
                if progress:
                    progress(f"{host}  {result.ip}  稳定性 {previous.successes}/{previous.attempts}")

    output = list(by_ip.values())
    output.sort(key=lambda r: (not r.ok, -r.stability, r.score, r.tcp_ms or 999999))
    return output


def probe_domains(
    candidates_by_host: dict[str, dict[str, Iterable[str]]],
    *,
    max_candidates: int = 6,
    timeout: float = 3.6,
    samples: int = 2,
    progress: Callable[[str], None] | None = None,
) -> dict[str, list[ProbeResult]]:
    output: dict[str, list[ProbeResult]] = {}
    with ThreadPoolExecutor(max_workers=min(5, len(candidates_by_host) or 1)) as pool:
        futures = {
            pool.submit(
                probe_candidates,
                host,
                candidates,
                max_candidates=max_candidates,
                timeout=timeout,
                samples=samples,
                progress=progress,
            ): host
            for host, candidates in candidates_by_host.items()
        }
        for future in as_completed(futures):
            host = futures[future]
            try:
                output[host] = future.result()
            except Exception as exc:
                output[host] = [ProbeResult(host=host, ip="", error=str(exc), score=999999)]
    return output


def verify_host(host: str, *, timeout: float = 8.0, path: str | None = None) -> tuple[bool, int | None, str]:
    """Verify a domain through the OS resolver (thus through the hosts file)."""
    import http.client

    spec = DOMAIN_BY_HOST.get(host)
    target = path or (spec.path if spec else "/")
    spec_kind = spec.kind if spec else "https"
    if spec_kind == "ssh":
        return False, None, "SSH 域名请使用 SSH 测试"
    try:
        conn = http.client.HTTPSConnection(host, 443, timeout=timeout)
        conn.request("HEAD", target, headers={"User-Agent": USER_AGENT, "Connection": "close"})
        resp = conn.getresponse()
        status = resp.status
        conn.close()
        return status < 500, status, "" if status < 500 else f"HTTP {status}"
    except Exception as exc:
        return False, None, str(exc)

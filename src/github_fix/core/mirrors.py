from __future__ import annotations

import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from .models import MirrorResult


TEST_RAW_URL = "https://raw.githubusercontent.com/521xueweihan/GitHub520/main/hosts"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GitHubFix/1.0"

# 第三方公共镜像只用于公开仓库/文件下载；不要通过它们传输令牌或私有代码。
MIRRORS: tuple[tuple[str, str, str], ...] = (
    ("ghfast.top", "https://ghfast.top", "常用 GitHub 代理，支持 clone/raw/release"),
    ("gh-proxy.com", "https://gh-proxy.com", "公共 GitHub 代理"),
    ("ghproxy.net", "https://ghproxy.net", "公共 GitHub 代理，稳定性随时间变化"),
    ("github.moeyy.xyz", "https://github.moeyy.xyz", "公共 GitHub 代理"),
    ("ghproxy.cc", "https://ghproxy.cc", "公共 GitHub 代理"),
)


def accelerate_url(url: str, base: str) -> str:
    if not url.startswith(("https://github.com/", "https://raw.githubusercontent.com/", "https://codeload.github.com/", "https://gist.githubusercontent.com/")):
        raise ValueError("只支持 GitHub 的公开 URL")
    return base.rstrip("/") + "/" + url


def clone_command(repo_url: str, base: str) -> str:
    return f"git clone {accelerate_url(repo_url, base)}"


def _probe(name: str, base: str, note: str, timeout: float) -> MirrorResult:
    result = MirrorResult(name=name, base=base, note=note)
    url = accelerate_url(TEST_RAW_URL, base)
    started = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(1024 * 1024)
            result.status = resp.status
            result.latency_ms = round((time.perf_counter() - started) * 1000, 1)
            text = body.decode("utf-8", "replace")
            if resp.status == 200 and "GitHub520 Host Start" in text and len(text) > 500:
                result.ok = True
            else:
                result.error = "返回内容不是预期的 GitHub 文件"
    except Exception as exc:
        result.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        result.error = str(exc)
    return result


def probe_mirrors(timeout: float = 10.0, progress=None) -> list[MirrorResult]:
    results: list[MirrorResult] = []
    with ThreadPoolExecutor(max_workers=len(MIRRORS)) as pool:
        futures = {pool.submit(_probe, name, base, note, timeout): name for name, base, note in MIRRORS}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                name = futures[future]
                base = next((b for n, b, _ in MIRRORS if n == name), "")
                result = MirrorResult(name=name, base=base, error=str(exc))
            results.append(result)
            if progress:
                progress(f"镜像 {result.name}: {'可用' if result.ok else '不可用'}")
    results.sort(key=lambda r: (not r.ok, r.latency_ms or 999999))
    return results

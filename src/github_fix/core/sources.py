from __future__ import annotations

import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .hosts import valid_public_ip
from .models import DOMAIN_BY_HOST


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GitHubFix/1.0"
HOSTS_FEEDS = {
    "GitHub520": "https://raw.githubusercontent.com/521xueweihan/GitHub520/main/hosts",
    "GitHub520-ghfast": "https://ghfast.top/https://raw.githubusercontent.com/521xueweihan/GitHub520/main/hosts",
    "ineo6": "https://raw.githubusercontent.com/ineo6/hosts/master/hosts",
    "ineo6-ghfast": "https://ghfast.top/https://raw.githubusercontent.com/ineo6/hosts/master/hosts",
    "jaywcjlove": "https://raw.githubusercontent.com/jaywcjlove/hosts/master/hosts",
    "ineo6-gitlab": "https://gitlab.com/ineo6/hosts/-/raw/master/next-hosts",
}

# 仅作为 DNS/测速候选，不会直接写入 hosts；最终必须通过 TLS + HTTP 验证。
BUILTIN_IPS: dict[str, tuple[str, ...]] = {
    "github.com": ("20.205.243.166", "20.27.177.113", "140.82.112.3", "140.82.113.3", "140.82.114.3"),
    "api.github.com": ("20.205.243.168", "20.27.177.116", "140.82.112.6", "140.82.113.6", "140.82.114.6"),
    "codeload.github.com": ("20.205.243.165", "140.82.112.9", "140.82.113.9", "140.82.114.9"),
    "raw.githubusercontent.com": ("185.199.108.133", "185.199.109.133", "185.199.110.133", "185.199.111.133"),
    "github.githubassets.com": ("185.199.108.154", "185.199.109.154", "185.199.110.154", "185.199.111.154"),
    "avatars.githubusercontent.com": ("185.199.108.133", "185.199.109.133", "185.199.110.133", "185.199.111.133"),
    "objects.githubusercontent.com": ("185.199.108.133", "185.199.109.133", "185.199.110.133", "185.199.111.133"),
    "release-assets.githubusercontent.com": ("185.199.108.133", "185.199.109.133", "185.199.110.133", "185.199.111.133"),
    "gist.githubusercontent.com": ("185.199.108.133", "185.199.109.133", "185.199.110.133", "185.199.111.133"),
    "ssh.github.com": ("20.205.243.160", "140.82.112.35", "140.82.113.35", "140.82.114.35"),
}


@dataclass
class FeedData:
    entries: dict[str, dict[str, list[str]]] = field(default_factory=dict)  # host -> ip -> sources
    errors: dict[str, str] = field(default_factory=dict)
    sources_ok: list[str] = field(default_factory=list)

    def add(self, host: str, ip: str, source: str) -> None:
        host = host.lower().strip()
        if host not in DOMAIN_BY_HOST or not valid_public_ip(ip):
            return
        self.entries.setdefault(host, {}).setdefault(ip, []).append(source)


def _fetch(url: str, timeout: float) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(2 * 1024 * 1024).decode("utf-8", "replace")


def parse_hosts_text(text: str, source: str) -> dict[str, dict[str, list[str]]]:
    entries: dict[str, dict[str, list[str]]] = {}
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2 or not valid_public_ip(parts[0]):
            continue
        for host in parts[1:]:
            host = host.lower().strip()
            if host in DOMAIN_BY_HOST:
                entries.setdefault(host, {}).setdefault(parts[0], []).append(source)
    return entries


def fetch_feeds(timeout: float = 7.0) -> FeedData:
    data = FeedData()
    tasks: dict[object, str] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for name, url in HOSTS_FEEDS.items():
            tasks[pool.submit(_fetch, url, timeout)] = name
        deadline = time.monotonic() + timeout + 1.0
        try:
            iterator = as_completed(tasks, timeout=max(0.1, deadline - time.monotonic()))
            for future in iterator:
                name = tasks[future]
                try:
                    text = future.result()
                    parsed = parse_hosts_text(text, name)
                    for host, ips in parsed.items():
                        for ip, sources in ips.items():
                            for source in sources:
                                data.add(host, ip, source)
                    if parsed:
                        data.sources_ok.append(name)
                    else:
                        data.errors[name] = "内容中没有目标 GitHub 域名"
                except Exception as exc:
                    data.errors[name] = str(exc)
        except TimeoutError:
            data.errors["订阅超时"] = "部分 hosts 订阅源响应超时"
    return data


def merge_candidate(
    target: dict[str, dict[str, list[str]]],
    host: str,
    ip: str,
    source: str,
) -> None:
    if host in DOMAIN_BY_HOST and valid_public_ip(ip):
        target.setdefault(host, {}).setdefault(ip, []).append(source)


def build_candidates(
    resolutions: dict[str, object],
    feeds: FeedData,
    pinned: dict[str, list[str]] | None = None,
) -> dict[str, dict[str, list[str]]]:
    """Merge all candidate sources into host -> ip -> source names."""
    candidates: dict[str, dict[str, list[str]]] = {}
    for host, resolution in resolutions.items():
        by_source = getattr(resolution, "by_source", {})
        for source, ips in by_source.items():
            for ip in ips:
                merge_candidate(candidates, host, ip, source)
    for host, ips in feeds.entries.items():
        for ip, sources in ips.items():
            for source in sources:
                merge_candidate(candidates, host, ip, source)
    for host, ips in BUILTIN_IPS.items():
        for ip in ips:
            merge_candidate(candidates, host, ip, "内置候选")
    for host, ips in (pinned or {}).items():
        for ip in ips:
            merge_candidate(candidates, host, ip, "当前 hosts")
    return candidates


def source_groups(candidates: dict[str, dict[str, list[str]]]) -> dict[str, dict[str, list[str]]]:
    return {host: {src: list(dict.fromkeys(ips)) for src, ips in sources.items()} for host, sources in candidates.items()}

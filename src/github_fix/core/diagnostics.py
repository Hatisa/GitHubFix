from __future__ import annotations

import datetime as _dt
import platform
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import git_tools
from .hosts import is_admin, read_hosts
from .mirrors import probe_mirrors
from .models import DOMAINS, REQUIRED_HOSTS, DiagnosisReport, ProbeResult
from .paths import hosts_path
from .probe import probe_domains
from .resolver import resolve_many
from .sources import build_candidates, fetch_feeds


Progress = Callable[[str], None]


def _log(progress: Progress | None, message: str) -> None:
    if progress:
        progress(message)


def _best_by_host(results: dict[str, list[ProbeResult]]) -> dict[str, ProbeResult]:
    best: dict[str, ProbeResult] = {}
    for host, items in results.items():
        usable = [item for item in items if item.ok]
        if usable:
            best[host] = usable[0]
    return best


def diagnose(
    progress: Progress | None = None,
    *,
    include_mirrors: bool = True,
    probe_timeout: float = 3.6,
    quick: bool = False,
) -> DiagnosisReport:
    _log(progress, "读取系统 hosts 与本地环境…")
    try:
        snapshot = read_hosts()
        hosts_read_error = ""
    except Exception as exc:
        snapshot = None
        hosts_read_error = str(exc)
    proxy_list = git_tools.detect_proxies(verify=False)
    git_info = git_tools.inspect_git(test_ssh=False) if git_tools.git_version() else {}
    _log(progress, "正在通过阿里/腾讯 DNS 和 DoH 解析 GitHub 域名…")

    hosts = [d.host for d in DOMAINS]
    feeds = None
    mirrors = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        resolution_future = pool.submit(resolve_many, hosts, timeout=5.0)
        feeds_future = pool.submit(fetch_feeds, 5.5)
        mirrors_future = pool.submit(probe_mirrors, 9.0) if include_mirrors else None
        resolutions = resolution_future.result()
        feeds = feeds_future.result()
        if mirrors_future is not None:
            mirrors = mirrors_future.result()

    _log(progress, "合并 hosts 订阅与候选 IP，开始 TLS/HTTP 测速…")
    candidates = build_candidates(resolutions, feeds, snapshot.entries if snapshot else {})
    results = probe_domains(
        candidates,
        max_candidates=5 if quick else 8,
        timeout=probe_timeout,
        samples=2,
        progress=None,
    )
    best = _best_by_host(results)

    domain_report: dict[str, dict] = {}
    for host, items in results.items():
        resolution = resolutions.get(host)
        domain_report[host] = {
            "candidates": [item.to_dict() for item in items],
            "best": best[host].to_dict() if host in best else None,
            "resolved_by": dict(resolution.by_source) if resolution else {},
        }

    healthy_required = [host for host in REQUIRED_HOSTS if host in best]
    summary: list[str] = []
    recommendations: list[str] = []
    summary.append(f"核心域名可用 {len(healthy_required)}/{len(REQUIRED_HOSTS)}")
    if "github.com" in best:
        web = best["github.com"]
        summary.append(f"github.com 最佳: {web.ip} ({web.latency_ms} ms)")
    else:
        summary.append("github.com 当前没有通过验证的 IP")
    if "api.github.com" in best:
        summary.append(f"API 最佳: {best['api.github.com'].ip} ({best['api.github.com'].latency_ms} ms)")
    if "raw.githubusercontent.com" in best:
        summary.append(f"Raw 最佳: {best['raw.githubusercontent.com'].ip} ({best['raw.githubusercontent.com'].latency_ms} ms)")

    if len(healthy_required) == 0:
        recommendations.append("当前网络可能完全无法直连 GitHub。请先尝试 Git SSH 443 或可信代理，不要只依赖 hosts。")
    elif len(healthy_required) < len(REQUIRED_HOSTS):
        missing = [h for h in REQUIRED_HOSTS if h not in best]
        recommendations.append("部分核心域名不可用: " + ", ".join(missing) + "。应用修复后如仍失败，请使用 Git SSH 443 或代理。")
    else:
        recommendations.append("直连测速正常，可执行“一键修复”写入最优 hosts。")

    if hosts_read_error:
        recommendations.append("读取 hosts 文件失败: " + hosts_read_error)
    elif snapshot and snapshot.legacy_count:
        recommendations.append("检测到旧版 GitHub Fix 配置，将在修复时自动备份并替换。")
    if snapshot and snapshot.managed_count:
        recommendations.append("已存在 GitHubFix 配置，修复会刷新为最新测速结果。")
    if snapshot and not snapshot.managed_count and not snapshot.legacy_count:
        recommendations.append("尚未安装 hosts 修复，建议先执行一键修复。")

    if include_mirrors:
        available_mirrors = [mirror for mirror in mirrors if mirror.ok]
        if available_mirrors:
            recommendations.append(f"可用公共镜像 {len(available_mirrors)} 个，适合公开仓库 clone/下载（不要传输令牌）。")
        else:
            recommendations.append("公共镜像当前均不可用，hosts 与 SSH 443 是主要方案。")

    if proxy_list:
        recommendations.append("检测到本机代理端口/系统代理，可在 Git 页测试并一键应用。")
    risky_rewrites = getattr(git_info, "risky_rewrites", []) if git_info else []
    if risky_rewrites:
        recommendations.append("检测到第三方 Git URL 改写，Git 可能绕过 hosts/智能代理；请在“Git 加速”页清理并备份。")

    report = DiagnosisReport(
        generated_at=_dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        admin=is_admin(),
        hosts_path=str(hosts_path()),
        hosts_managed=bool(snapshot and snapshot.managed_count > 0),
        hosts_legacy=bool(snapshot and snapshot.legacy_count > 0),
        pinned={host: ips[0] for host, ips in (snapshot.entries if snapshot else {}).items() if ips},
        domains=domain_report,
        best=best,
        mirrors=mirrors,
        git={
            "version": getattr(git_info, "version", "") if git_info else "",
            "user_name": getattr(git_info, "user_name", "") if git_info else "",
            "proxy": getattr(git_info, "proxy", "") if git_info else "",
            "ssh_ready": getattr(git_info, "ssh_ready", False) if git_info else False,
            "ssh_config": getattr(git_info, "ssh_config", "") if git_info else "",
            "url_rewrites": getattr(git_info, "url_rewrites", []) if git_info else [],
            "risky_rewrites": risky_rewrites,
        },
        proxies=proxy_list,
        network={
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "dns_errors": {host: resolutions[host].errors for host in resolutions if resolutions[host].errors},
            "feeds_ok": feeds.sources_ok,
            "feeds_errors": feeds.errors,
            "hosts_read_error": hosts_read_error,
        },
        summary=summary,
        recommendations=recommendations,
    )
    _log(progress, "诊断完成。")
    return report

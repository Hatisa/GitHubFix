from __future__ import annotations

import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from .hosts import apply_hosts, flush_dns, is_admin, restore_hosts, valid_public_ip
from .models import DOMAINS, REQUIRED_HOSTS, DiagnosisReport, ProbeResult
from .paths import hosts_path
from .probe import probe_ip, verify_host


# 需要端到端验证的域名（写入后逐个检查）。
VERIFY_HOSTS = (
    "github.com",
    "api.github.com",
    "codeload.github.com",
    "raw.githubusercontent.com",
    "github.githubassets.com",
)


@dataclass
class RepairOutcome:
    applied: bool
    rolled_back: bool = False
    message: str = ""
    detail: list[str] = field(default_factory=list)
    entries: dict[str, str] = field(default_factory=dict)
    carried: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    backup: str = ""
    verification: dict[str, dict] = field(default_factory=dict)
    report: DiagnosisReport | None = None

    def to_dict(self) -> dict:
        return {
            "applied": self.applied,
            "rolled_back": self.rolled_back,
            "message": self.message,
            "detail": self.detail,
            "entries": self.entries,
            "carried": self.carried,
            "skipped": self.skipped,
            "backup": self.backup,
            "verification": self.verification,
        }


def _log(progress: Callable[[str], None] | None, msg: str) -> None:
    if progress:
        progress(msg)


def _sleep(seconds: float) -> None:
    """Small pause between rounds; extracted so tests can neutralize it."""
    time.sleep(seconds)


# ---------------------------------------------------------------------------
# 选择要写入的记录
# ---------------------------------------------------------------------------

def _plan_entries(report: DiagnosisReport) -> tuple[dict[str, str], list[str], list[str]]:
    """Build the hosts plan.

    Priority per domain:
      1. an IP that passed TLS/HTTP probing in this diagnosis;
      2. the IP already present in our managed block (it worked at some point,
         and dropping it only falls back to polluted system DNS);
      3. omit the domain.

    Returns (entries, carried_hosts, skipped_hosts).
    """
    entries: dict[str, str] = {}
    carried: list[str] = []
    skipped: list[str] = []
    pinned = report.pinned or {}
    for spec in DOMAINS:
        best = report.best.get(spec.host)
        if best is not None and best.ok and valid_public_ip(best.ip):
            entries[spec.host] = best.ip
            continue
        previous = pinned.get(spec.host)
        if previous and valid_public_ip(previous):
            entries[spec.host] = previous
            carried.append(spec.host)
            continue
        skipped.append(spec.host)
    return entries, carried, skipped


def _candidate_ips(report: DiagnosisReport, host: str, exclude: set[str], limit: int = 4) -> list[str]:
    domain = report.domains.get(host) or {}
    ips: list[str] = []
    for item in domain.get("candidates", []):
        ip = str(item.get("ip", "")).strip()
        if not ip or ip in exclude or ip in ips:
            continue
        if not item.get("ok"):
            continue
        if valid_public_ip(ip):
            ips.append(ip)
        if len(ips) >= limit:
            break
    return ips


def _extended_probe(host: str, progress: Callable[[str], None] | None = None) -> ProbeResult | None:
    """A slower, wider probe used only when the first diagnosis found nothing."""
    from .resolver import resolve
    from .sources import build_candidates, fetch_feeds
    from .probe import probe_candidates

    _log(progress, f"{host} 没有可用记录，进行一轮更严格的补测…")
    try:
        resolution = resolve(host, timeout=6.0)
        feeds = fetch_feeds(6.0)
        candidates = build_candidates({host: resolution}, feeds, {})
        results = probe_candidates(
            host,
            candidates.get(host, {}),
            max_candidates=12,
            timeout=5.0,
            samples=3,
        )
        for item in results:
            if item.ok:
                _log(progress, f"{host} 补测成功: {item.ip}（{item.successes}/{item.attempts}，{item.latency_ms} ms）")
                return item
    except Exception as exc:
        _log(progress, f"{host} 补测失败: {exc}")
    return None


# ---------------------------------------------------------------------------
# 验证
# ---------------------------------------------------------------------------

def _verify_direct(
    entries: dict[str, str],
    *,
    timeout: float = 6.0,
    attempts: int = 2,
) -> dict[str, dict]:
    """Verify each written IP directly (TCP + TLS with SNI + HTTP), bypassing DNS.

    This is the authoritative check: it isolates "is this IP good for this host"
    from "has the OS DNS cache picked up the new hosts file yet".
    """
    output: dict[str, dict] = {}
    if not entries:
        return output

    def check(host: str, ip: str) -> tuple[str, dict]:
        best: ProbeResult | None = None
        tries = 0
        for _ in range(max(1, attempts)):
            tries += 1
            result = probe_ip(host, ip, timeout=timeout)
            if best is None or (result.ok and not best.ok):
                best = result
            if result.ok:
                break
            _sleep(0.35)
        return host, {
            "ip": ip,
            "direct": bool(best and best.ok),
            "direct_error": (best.error if best else "未探测"),
            "latency_ms": best.latency_ms if best else None,
            "status": best.status if best else None,
            "attempts": tries,
        }

    futures = {}
    with ThreadPoolExecutor(max_workers=min(5, len(entries))) as pool:
        for host, ip in entries.items():
            futures[pool.submit(check, host, ip)] = (host, ip)
        for future in as_completed(futures):
            host, ip = futures[future]
            try:
                _, data = future.result()
            except Exception as exc:  # pragma: no cover - defensive
                data = {
                    "ip": ip,
                    "direct": False,
                    "direct_error": f"{type(exc).__name__}: {exc}",
                    "latency_ms": None,
                    "status": None,
                    "attempts": 0,
                }
            output[host] = data
    return output


def _verify_dns(hosts: list[str], *, attempts: int = 2, timeout: float = 8.0) -> dict[str, dict]:
    """Verify through the OS resolver, with DNS flush + retry.

    A failure here is usually DNS cache lag, not a bad hosts entry, so callers
    must treat it as advisory rather than a reason to roll back.
    """
    output: dict[str, dict] = {}
    if not hosts:
        return output
    pending = list(hosts)
    for round_index in range(max(1, attempts)):
        if not pending:
            break
        if round_index:
            flush_dns()
            _sleep(0.8)
        with ThreadPoolExecutor(max_workers=min(5, len(pending))) as pool:
            futures = {pool.submit(verify_host, host, timeout=timeout): host for host in pending}
            for future in as_completed(futures):
                host = futures[future]
                try:
                    ok, status, error = future.result()
                except Exception as exc:
                    ok, status, error = False, None, str(exc)
                output[host] = {"ok": ok, "status": status, "error": error, "rounds": round_index + 1}
        pending = [host for host in pending if not output.get(host, {}).get("ok")]
    return output


def _apply(entries: dict[str, str]) -> tuple[str, int]:
    _, backup, legacy = apply_hosts(entries, flush=True)
    return backup, legacy


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def repair(
    report: DiagnosisReport,
    *,
    progress: Callable[[str], None] | None = None,
    auto_rollback: bool = True,
) -> RepairOutcome:
    if not is_admin():
        return RepairOutcome(False, message="修改 hosts 需要管理员权限，请以管理员身份重启本程序。", report=report)

    entries, carried, skipped = _plan_entries(report)

    if "github.com" not in entries:
        extra = _extended_probe("github.com", progress)
        if extra is not None and extra.ok:
            entries["github.com"] = extra.ip
            if "github.com" in skipped:
                skipped.remove("github.com")

    if "github.com" not in entries:
        return RepairOutcome(
            False,
            message=(
                "github.com 当前没有任何通过 TLS 证书校验的 IP，已停止修改以免破坏网络。\n"
                "请稍后重试，或在“Git 加速”页启用 SSH 443，或使用你所在组织允许的合法代理。"
            ),
            skipped=skipped,
            report=report,
        )

    required_written = [host for host in REQUIRED_HOSTS if host in entries]
    if len(required_written) < 2:
        missing = [host for host in REQUIRED_HOSTS if host not in entries]
        return RepairOutcome(
            False,
            message=(
                "可用核心域名过少（缺少: " + ", ".join(missing) + "），已停止修改。\n"
                "请稍后重试，或改用 SSH 443 / 合法代理。"
            ),
            entries=entries,
            skipped=skipped,
            report=report,
        )

    _log(progress, f"准备写入 {len(entries)} 条记录（核心 {len(required_written)}/5，沿用上次 {len(carried)} 条）…")
    original_backup = ""
    try:
        backup, legacy = _apply(entries)
        original_backup = original_backup or backup
    except PermissionError as exc:
        return RepairOutcome(False, message=str(exc), entries=entries, report=report)
    except Exception as exc:
        return RepairOutcome(False, message=f"写入 hosts 失败: {exc}", entries=entries, report=report)

    _log(progress, "hosts 已写入，开始直连验证（绕过 DNS，直接测试写入的 IP）…")
    _sleep(0.6)
    direct = _verify_direct(entries, timeout=6.0, attempts=2)

    # github.com 是硬性条件：写入的 IP 直连失败时，依次换用其他已验证候选。
    replaced = ""
    if not direct.get("github.com", {}).get("direct"):
        tried = {entries.get("github.com", "")}
        for alternate in _candidate_ips(report, "github.com", tried):
            tried.add(alternate)
            _log(progress, f"github.com 直连失败，尝试备选 IP {alternate}…")
            if not probe_ip("github.com", alternate, timeout=7.0).ok:
                continue
            entries["github.com"] = alternate
            try:
                backup, legacy = _apply(entries)
                original_backup = original_backup or backup
            except Exception as exc:
                _log(progress, f"写入备选 IP 失败: {exc}")
                break
            replaced = alternate
            _log(progress, "已用备选 IP 重写 hosts，重新验证…")
            _sleep(0.8)
            fresh = _verify_direct({"github.com": alternate}, timeout=7.0, attempts=3)
            if "github.com" in fresh:
                direct["github.com"] = fresh["github.com"]
            if direct.get("github.com", {}).get("direct"):
                break

    # 剔除此刻不可达的条目，而不是整块回滚：保留可用部分才真正改善网络。
    https_hosts = [host for host in entries if host != "ssh.github.com"]
    dead = [host for host in https_hosts if not direct.get(host, {}).get("direct")]
    if dead:
        _log(progress, "以下域名此刻不可达，将从 hosts 中剔除: " + ", ".join(dead))
        for host in dead:
            entries.pop(host, None)
            if host not in skipped:
                skipped.append(host)
        try:
            backup, legacy = _apply(entries)
            original_backup = original_backup or backup
            _sleep(0.5)
        except Exception as exc:
            _log(progress, f"剔除不可达条目后重写失败: {exc}")

    web_ok = bool(direct.get("github.com", {}).get("direct"))
    if (not web_ok or not entries) and auto_rollback and original_backup:
        _log(progress, "github.com 直连验证失败，自动回滚…")
        try:
            shutil.copy2(original_backup, hosts_path())
            flush_dns()
        except Exception as exc:
            return RepairOutcome(
                True,
                rolled_back=False,
                message=f"验证失败且自动回滚失败: {exc}。备份文件: {original_backup}",
                entries=entries,
                backup=original_backup,
                verification=direct,
                report=report,
            )
        return RepairOutcome(
            False,
            rolled_back=True,
            message=(
                "github.com 在所有候选 IP 上都无法完成 TLS 握手，已自动恢复原 hosts。\n"
                "说明: 当前网络对 GitHub 的封锁较强，写入任何 IP 都会让浏览器更慢。\n"
                "建议: 在“Git 加速”页启用 SSH 443，或使用你所在组织允许的合法代理，稍后再试。"
            ),
            entries=entries,
            skipped=skipped,
            backup=original_backup,
            verification=direct,
            report=report,
        )

    _log(progress, "直连验证完成，检查系统 DNS 是否已生效…")
    dns = _verify_dns([host for host in VERIFY_HOSTS if host in entries], attempts=2)
    for host, item in dns.items():
        if host in direct:
            direct[host]["dns"] = item["ok"]
            direct[host]["dns_error"] = item["error"]

    dns_ok = sum(1 for item in dns.values() if item["ok"])
    kept = [host for host in entries if host != "ssh.github.com"]
    message = f"已写入 {len(entries)} 条记录，直连验证 {len(kept)}/{len(kept)} 通过"
    if dns:
        message += f"，DNS 验证 {dns_ok}/{len(dns)} 通过"
    message += "。"
    if carried:
        kept_carried = [host for host in carried if host in entries]
        if kept_carried:
            message += f" 其中 {len(kept_carried)} 条沿用上次可用 IP。"
    if legacy:
        message += f" 已替换 {legacy} 个旧配置块。"
    if replaced:
        message += f" github.com 已切换到备选 IP {replaced}。"
    if skipped:
        message += f" 已跳过 {len(skipped)} 个当前没有可用 IP 的域名（改用系统 DNS）。"

    detail: list[str] = []
    for host in VERIFY_HOSTS:
        item = direct.get(host)
        if not item:
            continue
        if host not in entries:
            detail.append(f"{host} → 已剔除（{item.get('direct_error') or '不可达'}）")
            continue
        state = "直连通过" if item["direct"] else "直连失败"
        if item.get("dns") is True:
            state += " / DNS 通过"
        elif item.get("dns") is False:
            state += " / DNS 未生效"
        line = f"{host} → {item['ip']}  {state}"
        if not item["direct"] and item.get("direct_error"):
            line += f"（{item['direct_error']}）"
        detail.append(line)
    for host in skipped:
        if host not in VERIFY_HOSTS:
            detail.append(f"{host} → 未写入（无可用 IP）")

    if dns and dns_ok < max(1, len(dns) // 2):
        message += "\nDNS 缓存可能尚未刷新，浏览器若仍异常请等待几秒或重新打开标签页。"
    if original_backup:
        message += f"\n原文件备份: {original_backup}"

    return RepairOutcome(
        True,
        message=message,
        detail=detail,
        entries=entries,
        carried=carried,
        skipped=skipped,
        backup=original_backup,
        verification=direct,
        report=report,
    )


def restore(*, progress: Callable[[str], None] | None = None) -> tuple[bool, str]:
    if not is_admin():
        return False, "修改 hosts 需要管理员权限，请以管理员身份重启本程序。"
    try:
        _log(progress, "移除 GitHubFix 管理的 hosts 配置…")
        _, backup, removed = restore_hosts(flush=True)
        if removed == 0:
            return True, "没有发现 GitHubFix 管理的配置，系统 hosts 未改变。"
        message = f"已移除 {removed} 个加速配置块并刷新 DNS。"
        if backup:
            message += f" 修改前备份: {backup}"
        return True, message
    except Exception as exc:
        return False, f"恢复失败: {exc}"
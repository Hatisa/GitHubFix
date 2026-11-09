from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from typing import Callable

from .hosts import apply_hosts, flush_dns, is_admin, restore_hosts
from .models import DOMAINS, REQUIRED_HOSTS, DiagnosisReport
from .paths import hosts_path
from .probe import verify_host


@dataclass
class RepairOutcome:
    applied: bool
    rolled_back: bool = False
    message: str = ""
    entries: dict[str, str] = field(default_factory=dict)
    backup: str = ""
    verification: dict[str, dict] = field(default_factory=dict)
    report: DiagnosisReport | None = None

    def to_dict(self) -> dict:
        return {
            "applied": self.applied,
            "rolled_back": self.rolled_back,
            "message": self.message,
            "entries": self.entries,
            "backup": self.backup,
            "verification": self.verification,
        }


def _log(progress: Callable[[str], None] | None, msg: str) -> None:
    if progress:
        progress(msg)


def _choose_entries(report: DiagnosisReport) -> dict[str, str]:
    entries: dict[str, str] = {}
    for spec in DOMAINS:
        result = report.best.get(spec.host)
        if result and result.ok:
            entries[spec.host] = result.ip
    return entries


def _verify(hosts: list[str], timeout: float = 7.0) -> dict[str, dict]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    output: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(5, len(hosts) or 1)) as pool:
        futures = {pool.submit(verify_host, host, timeout=timeout): host for host in hosts}
        for future in as_completed(futures):
            host = futures[future]
            try:
                ok, status, error = future.result()
            except Exception as exc:
                ok, status, error = False, None, str(exc)
            output[host] = {"ok": ok, "status": status, "error": error}
    return output


def repair(
    report: DiagnosisReport,
    *,
    progress: Callable[[str], None] | None = None,
    auto_rollback: bool = True,
) -> RepairOutcome:
    if not is_admin():
        return RepairOutcome(False, message="修改 hosts 需要管理员权限，请以管理员身份重启本程序。", report=report)
    entries = _choose_entries(report)
    if "github.com" not in entries:
        return RepairOutcome(False, message="github.com 没有通过 TLS/HTTP 验证的 IP，已停止修改，避免破坏网络。", report=report)
    required_ok = sum(1 for host in REQUIRED_HOSTS if host in entries)
    if required_ok < 3:
        missing = [host for host in REQUIRED_HOSTS if host not in entries]
        return RepairOutcome(
            False,
            message="可用核心域名过少（" + ", ".join(missing) + "），已停止修改。建议改用 SSH 443 或代理。",
            report=report,
        )
    _log(progress, f"准备写入 {len(entries)} 条最优记录（核心 {required_ok}/5）…")
    try:
        _, backup, legacy = apply_hosts(entries, flush=True)
    except Exception as exc:
        return RepairOutcome(False, message=f"写入 hosts 失败: {exc}", entries=entries, report=report)
    _log(progress, "hosts 已写入并刷新 DNS，开始端到端验证…")
    time.sleep(0.8)
    verify_hosts = [
        host
        for host in ("github.com", "api.github.com", "codeload.github.com", "raw.githubusercontent.com", "github.githubassets.com")
        if host in entries
    ]
    verification = _verify(verify_hosts)
    passed = sum(1 for item in verification.values() if item["ok"])
    required_passes = max(2, (len(verify_hosts) * 3 + 4) // 5)  # 至少 60%
    web_ok = verification.get("github.com", {}).get("ok", False)
    if (not web_ok or passed < required_passes) and auto_rollback and backup:
        _log(progress, f"验证仅 {passed} 项通过，自动回滚…")
        try:
            shutil.copy2(backup, hosts_path())
            flush_dns()
        except Exception as exc:
            return RepairOutcome(
                True,
                rolled_back=False,
                message=f"验证失败且自动回滚失败: {exc}。备份文件: {backup}",
                entries=entries,
                backup=backup,
                verification=verification,
                report=report,
            )
        return RepairOutcome(
            False,
            rolled_back=True,
            message=f"写入后仅 {passed}/{len(verification)} 个域名验证通过，已自动恢复原 hosts。备份: {backup}",
            entries=entries,
            backup=backup,
            verification=verification,
            report=report,
        )
    message = f"已写入 {len(entries)} 条最优记录，端到端验证 {passed}/{len(verification)} 通过。"
    if legacy:
        message += f" 已替换 {legacy} 个旧配置块。"
    if backup:
        message += f" 原文件备份: {backup}"
    return RepairOutcome(True, message=message, entries=entries, backup=backup, verification=verification, report=report)


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

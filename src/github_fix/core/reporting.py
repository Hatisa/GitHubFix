from __future__ import annotations

import json
import time
from pathlib import Path

from .models import DOMAINS, DiagnosisReport
from .paths import reports_dir


def report_markdown(report: DiagnosisReport) -> str:
    lines: list[str] = []
    lines.append("# GitHubFix 网络诊断报告")
    lines.append("")
    lines.append(f"- 生成时间: {report.generated_at}")
    lines.append(f"- 管理员权限: {'是' if report.admin else '否'}")
    lines.append(f"- hosts 文件: {report.hosts_path}")
    lines.append(f"- GitHubFix 配置: {'已安装' if report.hosts_managed else '未安装'}")
    if report.hosts_legacy:
        lines.append("- 检测到旧版 GitHub Fix 配置")
    lines.append("")
    lines.append("## 结论")
    lines.extend(f"- {item}" for item in report.summary)
    lines.append("")
    lines.append("## 核心域名")
    lines.append("")
    lines.append("| 域名 | 最佳 IP | TCP+握手+HTTP | HTTP 状态 | 来源 |")
    lines.append("|---|---|---:|---:|---|")
    for spec in DOMAINS:
        best = report.best.get(spec.host)
        if best:
            lines.append(f"| {spec.host} | {best.ip} | {best.latency_ms} ms | {best.status or '-'} | {best.source} |")
        else:
            lines.append(f"| {spec.host} | 不可用 | - | - | - |")
    lines.append("")
    if report.mirrors:
        lines.append("## 公共镜像测速")
        lines.append("")
        lines.append("| 镜像 | 状态 | 延迟 | 说明 |")
        lines.append("|---|---|---:|---|")
        for mirror in report.mirrors:
            lines.append(f"| {mirror.name} | {'可用' if mirror.ok else '不可用'} | {mirror.latency_ms or '-'} ms | {mirror.note} |")
        lines.append("")
        lines.append("> 公共镜像仅用于公开仓库/文件，切勿通过第三方镜像传输 GitHub 令牌、私有代码或密码。")
        lines.append("")
    if report.proxies:
        lines.append("## 检测到的代理")
        lines.append("")
        for item in report.proxies:
            lines.append(f"- {item.get('proxy')}（{item.get('source')}）")
        lines.append("")
    lines.append("## 建议")
    lines.extend(f"- {item}" for item in report.recommendations)
    lines.append("")
    lines.append("## 环境")
    lines.append("")
    for key, value in report.network.items():
        lines.append(f"- {key}: {json.dumps(value, ensure_ascii=False)}")
    lines.append("")
    lines.append("---")
    lines.append("GitHubFix 只修改自己标记的 hosts 配置块；原始文件会备份到本地数据目录。")
    return "\n".join(lines)


def save_report(report: DiagnosisReport, path: str | Path | None = None) -> Path:
    target = Path(path) if path else reports_dir() / time.strftime("githubfix-report-%Y%m%d-%H%M%S.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report_markdown(report), encoding="utf-8")
    return target

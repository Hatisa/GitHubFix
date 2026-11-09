from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Sequence

from . import __version__
from .core import git_tools
from .core.diagnostics import diagnose
from .core.mirrors import accelerate_url, probe_mirrors
from .core.proxy_controller import ProxyController
from .core.reporting import report_markdown, save_report
from .core.service import repair, restore


def _print(message: str = "") -> None:
    print(message, flush=True)


def _progress(message: str) -> None:
    print(f"[*] {message}", file=sys.stderr, flush=True)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--no-mirrors", action="store_true", help="跳过公共镜像测速")


def cmd_diagnose(args: argparse.Namespace) -> int:
    report = diagnose(progress=_progress, include_mirrors=not args.no_mirrors)
    if args.json:
        _print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print(report_markdown(report))
    if args.output:
        target = save_report(report, args.output)
        _print(f"\n报告已保存: {target}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    report = diagnose(progress=_progress, include_mirrors=not args.no_mirrors)
    _print("\n".join(report.summary))
    if not args.yes:
        answer = input("将写入系统 hosts，是否继续？[y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            _print("已取消")
            return 2
    outcome = repair(report, progress=_progress)
    _print(outcome.message)
    if args.json:
        _print(json.dumps({"report": report.to_dict(), "outcome": outcome.to_dict()}, ensure_ascii=False, indent=2))
    if outcome.applied and not args.no_proxy:
        controller = ProxyController()
        ok, message = controller.start()
        _print(message)
        if ok:
            _print("智能代理已启动。关闭本进程会恢复系统代理。按 Ctrl+C 停止。")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                _print("\n正在停止智能代理…")
                controller.stop()
    return 0 if outcome.applied else 1


def cmd_restore(args: argparse.Namespace) -> int:
    controller = ProxyController()
    ok_proxy, message_proxy = controller.stop(restore_system=True)
    ok, message = restore(progress=_progress)
    ok_ssh, message_ssh = git_tools.remove_ssh_443()
    _print(message_proxy)
    _print(message)
    _print(message_ssh)
    return 0 if (ok and ok_proxy) else 1


def cmd_proxy(args: argparse.Namespace) -> int:
    controller = ProxyController()
    if args.action == "start":
        ok, message = controller.start()
        _print(message)
        if not ok:
            return 1
        _print("智能代理运行中。按 Ctrl+C 停止并恢复系统代理。")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            _print("\n正在停止…")
            _print(controller.stop()[1])
        return 0
    if args.action == "stop":
        ok, message = controller.stop()
        _print(message)
        return 0 if ok else 1
    status = controller.status()
    _print(json.dumps(status.__dict__, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_git_ssh(args: argparse.Namespace) -> int:
    if args.action == "test":
        ok, detail = git_tools.test_ssh_443()
        _print(("成功: " if ok else "失败: ") + detail)
        return 0 if ok else 1
    if args.action == "apply":
        ok_test, detail = git_tools.test_ssh_443()
        _print(("SSH 443 测试成功: " if ok_test else "SSH 443 测试失败: ") + detail)
        if not ok_test and not args.force:
            _print("未写入配置。可使用 --force 强制写入。")
            return 1
        ok, message = git_tools.apply_ssh_443()
        _print(message)
        return 0 if ok else 1
    ok, message = git_tools.remove_ssh_443()
    _print(message)
    return 0 if ok else 1


def cmd_rewrites(args: argparse.Namespace) -> int:
    if args.action == "list":
        rewrites = git_tools.inspect_url_rewrites()
        if not rewrites:
            _print("没有发现全局 GitHub URL 改写。")
            return 0
        for item in rewrites:
            flag = " [第三方]" if item["risky"] else " [GitHubFix]"
            _print(f"{item['base']}  <=  {item['instead_of']}{flag}")
        return 0
    ok, message = git_tools.clean_github_url_rewrites(dry_run=args.dry_run)
    _print(message)
    return 0 if ok else 1


def cmd_mirrors(args: argparse.Namespace) -> int:
    results = probe_mirrors(progress=_progress)
    if args.json:
        _print(json.dumps([item.to_dict() for item in results], ensure_ascii=False, indent=2))
    else:
        for item in results:
            _print(f"{item.name:22} {'可用' if item.ok else '不可用':4} {str(item.latency_ms or '-'):>8} ms  {item.error[:80]}")
    return 0


def cmd_convert(args: argparse.Namespace) -> int:
    candidates = [item for item in probe_mirrors(progress=_progress) if item.ok]
    if not candidates:
        _print("当前没有可用镜像。")
        return 1
    _print(accelerate_url(args.url, candidates[0].base))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="githubfix", description="GitHubFix - GitHub 智能连接助手")
    parser.add_argument("--version", action="version", version=f"GitHubFix {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("gui", help="打开图形界面")
    p.set_defaults(func=lambda args: _run_gui())

    p = sub.add_parser("diagnose", help="诊断 GitHub 直连与镜像状态")
    _add_common(p)
    p.add_argument("-o", "--output", help="保存 Markdown 报告")
    p.set_defaults(func=cmd_diagnose)

    p = sub.add_parser("apply", help="诊断并写入最优 hosts，然后启动智能代理")
    _add_common(p)
    p.add_argument("-y", "--yes", action="store_true", help="不询问")
    p.add_argument("--no-proxy", action="store_true", help="只写 hosts，不启动智能代理")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("restore", help="恢复 hosts、系统代理和 SSH 配置")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("proxy", help="管理本地智能连接器")
    p.add_argument("action", choices=("start", "stop", "status"))
    p.set_defaults(func=cmd_proxy)

    p = sub.add_parser("git-ssh", help="管理 GitHub SSH 443")
    p.add_argument("action", choices=("test", "apply", "remove"))
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_git_ssh)

    p = sub.add_parser("rewrites", help="检查/清理全局 Git URL 改写")
    p.add_argument("action", choices=("list", "clean"))
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_rewrites)

    p = sub.add_parser("mirrors", help="测速公共 GitHub 镜像")
    _add_common(p)
    p.set_defaults(func=cmd_mirrors)

    p = sub.add_parser("convert", help="把 GitHub URL 转成当前最快镜像 URL")
    p.add_argument("url")
    p.set_defaults(func=cmd_convert)
    return parser


def _run_gui() -> int:
    from .app import launch

    return launch()


def _configure_console() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv: Sequence[str] | None = None) -> int:
    _configure_console()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not getattr(args, "command", None):
        return _run_gui()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

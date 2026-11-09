from __future__ import annotations

import ctypes
import os
import subprocess
import sys


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(extra_args: list[str] | None = None) -> tuple[bool, str]:
    """Relaunch the current program elevated. Returns (launched, message)."""
    args = list(extra_args or [])
    if getattr(sys, "frozen", False):
        executable = sys.executable
        command = subprocess.list2cmdline(args)
    else:
        executable = sys.executable
        command = subprocess.list2cmdline(["-m", "github_fix", *args])
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            executable,
            command,
            os.getcwd(),
            1,
        )
        if int(result) > 32:
            return True, "已请求管理员权限，请在弹出的 UAC 窗口点击“是”。"
        return False, f"无法启动管理员进程（ShellExecute 返回 {result}）"
    except Exception as exc:
        return False, f"提升权限失败: {exc}"

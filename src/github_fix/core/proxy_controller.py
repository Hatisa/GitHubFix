from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from . import git_tools, system_proxy
from .pac_server import PACServer
from .paths import data_dir
from .proxy_server import DEFAULT_PORT, HostRouter, SmartProxy, pac_script


STATE_FILE = "proxy-runtime-state.json"


def _state_path() -> Path:
    return data_dir() / STATE_FILE


def _read_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(data: dict) -> None:
    _state_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass
class ProxyStatus:
    running: bool = False
    proxy_port: int = 0
    pac_url: str = ""
    system_pac: bool = False
    git_proxy: str = ""
    stats: dict | None = None
    message: str = ""


class ProxyController:
    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.router = HostRouter()
        self.proxy = SmartProxy(port, self.router)
        self.pac: PACServer | None = None
        self._previous_git_proxy = ""

    def start(self) -> tuple[bool, str]:
        if self.proxy.running:
            return True, f"智能代理已在运行（127.0.0.1:{self.proxy.port}）"
        ok, message = self.proxy.start()
        if not ok:
            return False, message
        self._previous_git_proxy = git_tools._get_config("http.https://github.com.proxy")
        if _is_local_proxy(self._previous_git_proxy):
            self._previous_git_proxy = ""
        self.pac = PACServer(lambda: pac_script(self.proxy.port))
        self.pac.start()
        pac_ok, pac_message = system_proxy.enable_pac(self.pac.url)
        if not pac_ok:
            self.pac.stop()
            self.pac = None
            self.proxy.stop()
            return False, pac_message
        git_ok, git_message = git_tools.set_git_proxy(f"http://127.0.0.1:{self.proxy.port}")
        _write_state(
            {
                "active": True,
                "proxy_port": self.proxy.port,
                "pac_url": self.pac.url,
                "previous_git_proxy": self._previous_git_proxy,
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        if not git_ok:
            return True, f"浏览器加速已启用；Git 代理设置失败: {git_message}"
        return True, f"智能代理已启用（127.0.0.1:{self.proxy.port}）。浏览器与 Git 的 GitHub 流量将自动选择可用 IP。"

    def stop(self, restore_system: bool = True) -> tuple[bool, str]:
        messages: list[str] = []
        state = _read_state()
        previous = state.get("previous_git_proxy", self._previous_git_proxy)
        current_git = git_tools._get_config("http.https://github.com.proxy")
        try:
            if _is_local_proxy(current_git):
                if previous:
                    git_tools.set_git_proxy(previous)
                    messages.append("已恢复原 Git 代理")
                else:
                    git_tools.clear_git_proxy()
                    messages.append("已清除 GitHubFix 的 Git 代理")
            elif current_git:
                messages.append("当前 Git 代理不是 GitHubFix 设置，保持不变")
            else:
                messages.append("Git 代理无需恢复")
        except Exception as exc:
            messages.append(f"Git 代理恢复失败: {exc}")
        if restore_system:
            ok, msg = system_proxy.disable_pac(restore=True)
            messages.append(msg)
        if self.pac:
            self.pac.stop()
            self.pac = None
        self.proxy.stop()
        try:
            _state_path().unlink(missing_ok=True)
        except OSError:
            pass
        return True, "；".join(messages)

    def status(self) -> ProxyStatus:
        system = system_proxy.read_settings()
        git_proxy = git_tools._get_config("http.https://github.com.proxy")
        return ProxyStatus(
            running=self.proxy.running,
            proxy_port=self.proxy.port,
            pac_url=self.pac.url if self.pac else "",
            system_pac="githubfix.pac" in (system.auto_config_url or ""),
            git_proxy=git_proxy,
            stats=self.proxy.stats.to_dict() if self.proxy.running else None,
            message="运行中" if self.proxy.running else "未运行",
        )

    @staticmethod
    def recover_stale() -> tuple[bool, str]:
        """Restore proxy settings left behind by a previous crashed session."""
        state = _read_state()
        system = system_proxy.read_settings()
        git_proxy = git_tools._get_config("http.https://github.com.proxy")
        if not state.get("active"):
            # 没有状态文件，但系统 PAC 或 Git 代理仍指向本工具时也要恢复。
            if system_proxy.PAC_MARKER in (system.auto_config_url or "") or _is_local_proxy(git_proxy):
                ok, msg = system_proxy.disable_pac(restore=True)
                if _is_local_proxy(git_proxy):
                    git_tools.clear_git_proxy()
                return True, "已清理上次异常退出留下的代理设置；" + msg
            return False, ""
        port = int(state.get("proxy_port", 0) or 0)
        if port and _port_open(port):
            return False, "检测到另一个 GitHubFix 实例正在使用智能代理"
        messages: list[str] = []
        previous = state.get("previous_git_proxy", "")
        try:
            if previous:
                git_tools.set_git_proxy(previous)
            else:
                git_tools.clear_git_proxy()
            messages.append("已恢复 Git 代理")
        except Exception as exc:
            messages.append(f"Git 代理恢复失败: {exc}")
        ok, msg = system_proxy.disable_pac(restore=True)
        messages.append(msg)
        try:
            _state_path().unlink(missing_ok=True)
        except OSError:
            pass
        return True, "；".join(messages)


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def _is_local_proxy(value: str) -> bool:
    value = (value or "").strip().lower()
    if not value:
        return False
    return any(value in (f"http://127.0.0.1:{port}", f"127.0.0.1:{port}") for port in range(DEFAULT_PORT, DEFAULT_PORT + 12))

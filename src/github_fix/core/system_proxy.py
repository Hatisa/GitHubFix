from __future__ import annotations

import ctypes
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import data_dir


REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
PAC_MARKER = "/githubfix.pac"


@dataclass
class ProxySettings:
    proxy_enable: int = 0
    proxy_server: str = ""
    proxy_override: str = ""
    auto_config_url: str = ""


def _open_key(write: bool = False):
    import winreg

    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ | (winreg.KEY_WRITE if write else 0))


def read_settings() -> ProxySettings:
    settings = ProxySettings()
    try:
        import winreg

        with _open_key(False) as key:
            for name, attr in (
                ("ProxyEnable", "proxy_enable"),
                ("ProxyServer", "proxy_server"),
                ("ProxyOverride", "proxy_override"),
                ("AutoConfigURL", "auto_config_url"),
            ):
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                    if attr == "proxy_enable":
                        value = int(value)
                    setattr(settings, attr, value)
                except FileNotFoundError:
                    pass
    except Exception:
        pass
    return settings


def _write_value(name: str, value, *, delete: bool = False) -> None:
    import winreg

    with _open_key(True) as key:
        if delete:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
        elif isinstance(value, int):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
        else:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def refresh_internet_settings() -> None:
    try:
        INTERNET_OPTION_SETTINGS_CHANGED = 39
        INTERNET_OPTION_REFRESH = 37
        wininet = ctypes.windll.Wininet
        wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
    except Exception:
        pass


def backup_path() -> Path:
    return data_dir() / "system-proxy-backup.json"


def has_backup() -> bool:
    return backup_path().exists()


def save_backup(settings: ProxySettings | None = None) -> Path:
    target = backup_path()
    if target.exists():
        return target
    settings = settings or read_settings()
    target.write_text(json.dumps({"saved_at": time.strftime("%Y-%m-%d %H:%M:%S"), "settings": asdict(settings)}, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def enable_pac(pac_url: str) -> tuple[bool, str]:
    """Enable a per-user PAC that only routes GitHub domains to our local proxy."""
    if os.name != "nt":
        return False, "仅支持 Windows"
    try:
        save_backup()
        _write_value("AutoConfigURL", pac_url)
        _write_value("ProxyEnable", 0)
        refresh_internet_settings()
        return True, f"已启用系统 PAC: {pac_url}"
    except Exception as exc:
        return False, f"设置系统 PAC 失败: {exc}"


def disable_pac(restore: bool = True) -> tuple[bool, str]:
    if os.name != "nt":
        return False, "仅支持 Windows"
    try:
        if restore and backup_path().exists():
            try:
                payload = json.loads(backup_path().read_text(encoding="utf-8"))
                settings = ProxySettings(**payload.get("settings", {}))
            except Exception:
                # 备份损坏时至少移除本工具的 PAC，避免网络被指向已停止的本地代理。
                _write_value("AutoConfigURL", "", delete=True)
                _write_value("ProxyEnable", 0)
                backup_path().unlink(missing_ok=True)
                refresh_internet_settings()
                return True, "代理备份损坏，已清除 GitHubFix PAC"
            _write_value("ProxyEnable", int(settings.proxy_enable))
            _write_value("ProxyServer", settings.proxy_server, delete=not bool(settings.proxy_server))
            _write_value("ProxyOverride", settings.proxy_override, delete=not bool(settings.proxy_override))
            if settings.auto_config_url:
                _write_value("AutoConfigURL", settings.auto_config_url)
            else:
                _write_value("AutoConfigURL", "", delete=True)
            backup_path().unlink(missing_ok=True)
            refresh_internet_settings()
            return True, "已恢复原有系统代理设置"
        current = read_settings()
        if PAC_MARKER in (current.auto_config_url or ""):
            _write_value("AutoConfigURL", "", delete=True)
            _write_value("ProxyEnable", 0)
            refresh_internet_settings()
            return True, "已关闭 GitHubFix 系统 PAC"
        return True, "系统代理未被 GitHubFix 修改"
    except Exception as exc:
        return False, f"恢复系统代理失败: {exc}"

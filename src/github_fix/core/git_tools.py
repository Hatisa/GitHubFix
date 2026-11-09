from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .paths import backup_dir


SSH_START = "# >>> GitHubFix SSH 443 >>>"
SSH_END = "# <<< GitHubFix SSH 443 <<<"
SSH_ALIAS = "github-443"
SSH_STATE_KEY = f"url.git@{SSH_ALIAS}:.insteadOf"
SSH_STATE_VALUE = "git@github.com:"
COMMON_PROXY_PORTS = (7890, 7897, 10808, 10809, 1080, 8080, 54642, 8888)
USER_AGENT = "GitHubFix/1.0"


@dataclass
class GitInfo:
    installed: bool = False
    version: str = ""
    user_name: str = ""
    user_email: str = ""
    proxy: str = ""
    http_version: str = ""
    instead_of: list[str] = field(default_factory=list)
    url_rewrites: list[dict] = field(default_factory=list)
    risky_rewrites: list[dict] = field(default_factory=list)
    ssh_config: str = ""
    ssh_ready: bool = False
    ssh_detail: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def run_git(args: list[str], timeout: float = 12.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_flags(),
    )


def git_version() -> str:
    try:
        proc = run_git(["--version"], timeout=6)
        return (proc.stdout or proc.stderr).strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


def _get_config(key: str) -> str:
    try:
        proc = run_git(["config", "--global", "--get", key], timeout=6)
        return (proc.stdout or "").strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


def _get_all(key: str) -> list[str]:
    try:
        proc = run_git(["config", "--global", "--get-all", key], timeout=6)
        return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()] if proc.returncode == 0 else []
    except Exception:
        return []


def inspect_url_rewrites() -> list[dict]:
    """Return global url.<base>.insteadOf rules that affect GitHub."""
    try:
        proc = run_git(["config", "--global", "--get-regexp", r"^url\..*\.insteadof$"], timeout=6)
    except Exception:
        return []
    if proc.returncode not in (0, 1):
        return []
    rewrites: list[dict] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        key, value = parts[0], parts[1].strip()
        lower = key.lower()
        if not lower.startswith("url.") or not lower.endswith(".insteadof"):
            continue
        base = key[4:-len(".insteadof")]
        if "github.com" not in (key + value).lower():
            continue
        ours = base.rstrip(":") == f"git@{SSH_ALIAS}"
        risky = (not ours) and base.lower() not in ("https://github.com/", "git@github.com:", "ssh://git@github.com/")
        rewrites.append({"key": key, "base": base, "instead_of": value, "ours": ours, "risky": risky})
    return rewrites


def clean_github_url_rewrites(dry_run: bool = False) -> tuple[bool, str]:
    """Remove third-party GitHub url.insteadOf rewrites, preserving user files."""
    rewrites = [item for item in inspect_url_rewrites() if item.get("risky")]
    if not rewrites:
        return True, "没有发现第三方 GitHub URL 改写。"
    if dry_run:
        return True, "；".join(f"{item['base']} -> {item['instead_of']}" for item in rewrites)
    config_path = Path.home() / ".gitconfig"
    try:
        if config_path.exists():
            backup = backup_dir() / f"gitconfig-{__import__('time').strftime('%Y%m%d-%H%M%S')}.bak"
            shutil.copy2(config_path, backup)
        for item in rewrites:
            run_git(["config", "--global", "--unset-all", str(item["key"]), str(item["instead_of"])], timeout=8)
        return True, f"已清理 {len(rewrites)} 条第三方 GitHub URL 改写，原 .gitconfig 已备份。"
    except Exception as exc:
        return False, f"清理失败: {exc}"


def ssh_config_path() -> Path:
    return Path.home() / ".ssh" / "config"


def inspect_ssh_config(path: Path | None = None) -> str:
    target = path or ssh_config_path()
    try:
        return target.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def has_managed_ssh_block(text: str) -> bool:
    return SSH_START in text and SSH_END in text


def strip_ssh_block(text: str) -> str:
    out: list[str] = []
    active = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped == SSH_START:
            active = True
            continue
        if active:
            if stripped == SSH_END:
                active = False
            continue
        out.append(line)
    return "".join(out).rstrip("\r\n") + ("\n" if "".join(out).strip() else "")


def build_ssh_block() -> str:
    return (
        f"{SSH_START}\n"
        f"# 通过 443 端口连接 GitHub SSH，适合 22 端口被阻断的网络。\n"
        f"Host {SSH_ALIAS}\n"
        "    HostName ssh.github.com\n"
        "    Port 443\n"
        "    User git\n"
        "    ServerAliveInterval 30\n"
        "    ServerAliveCountMax 3\n"
        f"{SSH_END}\n"
    )


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".githubfix-ssh-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def apply_ssh_443(path: Path | None = None) -> tuple[bool, str]:
    """Add a github-443 alias and make Git rewrite SSH GitHub URLs to it."""
    target = path or ssh_config_path()
    before = inspect_ssh_config(target)
    cleaned = strip_ssh_block(before)
    new_text = (cleaned.rstrip("\r\n") + "\n\n" if cleaned.strip() else "") + build_ssh_block()
    try:
        if target.exists():
            backup = backup_dir() / f"ssh-config-{__import__('time').strftime('%Y%m%d-%H%M%S')}.bak"
            shutil.copy2(target, backup)
        gitconfig = Path.home() / ".gitconfig"
        if gitconfig.exists():
            shutil.copy2(gitconfig, backup_dir() / f"gitconfig-{__import__('time').strftime('%Y%m%d-%H%M%S')}.bak")
        _atomic_write(target, new_text)
        run_git(["config", "--global", "--replace-all", SSH_STATE_KEY, SSH_STATE_VALUE], timeout=8)
        return True, "已写入 SSH 443 配置，并将 git@github.com: 重写到 github-443 别名。"
    except Exception as exc:
        return False, f"SSH 配置失败: {exc}"


def remove_ssh_443(path: Path | None = None) -> tuple[bool, str]:
    target = path or ssh_config_path()
    try:
        if target.exists():
            before = target.read_text(encoding="utf-8", errors="replace")
            if has_managed_ssh_block(before):
                backup = backup_dir() / f"ssh-config-{__import__('time').strftime('%Y%m%d-%H%M%S')}.bak"
                shutil.copy2(target, backup)
                _atomic_write(target, strip_ssh_block(before))
        try:
            run_git(["config", "--global", "--unset-all", SSH_STATE_KEY], timeout=8)
        except Exception:
            pass
        return True, "已移除 SSH 443 配置和 Git 重写规则。"
    except Exception as exc:
        return False, f"移除 SSH 配置失败: {exc}"


def test_ssh_443(timeout: float = 9.0) -> tuple[bool, str]:
    """Test SSH connectivity via ssh.github.com:443 without touching known_hosts."""
    ssh = shutil.which("ssh")
    if not ssh:
        return False, "未找到 ssh 客户端"
    known = tempfile.NamedTemporaryFile(prefix="githubfix-known-", delete=False)
    known.close()
    args = [
        ssh,
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", f"UserKnownHostsFile={known.name}",
        "-o", f"ConnectTimeout={int(timeout)}",
        "-p", "443",
        "-T", "git@ssh.github.com",
    ]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout + 4, creationflags=_flags())
        output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        lowered = output.lower()
        if "successfully authenticated" in lowered or "permission denied (publickey)" in lowered:
            return True, output or "SSH 443 端口连接成功"
        if "connection refused" in lowered or "timed out" in lowered or "could not resolve" in lowered:
            return False, output or "SSH 443 连接失败"
        return proc.returncode == 0, output or f"ssh 退出码 {proc.returncode}"
    except Exception as exc:
        return False, str(exc)
    finally:
        try:
            os.unlink(known.name)
        except OSError:
            pass


def _proxy_from_registry() -> tuple[str, bool]:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
            if int(enabled) and server:
                if "=" in server:
                    # http=127.0.0.1:7890;https=...
                    parts = dict(item.split("=", 1) for item in server.split(";") if "=" in item)
                    return parts.get("https") or parts.get("http") or "", True
                return str(server), True
    except Exception:
        pass
    return "", False


def _proxy_works(proxy: str, timeout: float = 6.0) -> bool:
    try:
        if "://" not in proxy:
            proxy = "http://" + proxy
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        opener = urllib.request.build_opener(handler)
        req = urllib.request.Request("https://api.github.com/meta", headers={"User-Agent": USER_AGENT})
        with opener.open(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def detect_proxies(verify: bool = False) -> list[dict]:
    found: dict[str, dict] = {}
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(key, "").strip()
        if value:
            found.setdefault(value, {"proxy": value, "source": f"环境变量 {key}", "reachable": None, "github_ok": None})
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(key, "").strip()
        if value:
            found[value]["reachable"] = True
    registry_proxy, enabled = _proxy_from_registry()
    if enabled and registry_proxy:
        found.setdefault(registry_proxy, {"proxy": registry_proxy, "source": "Windows 系统代理", "reachable": None, "github_ok": None})
    for port in COMMON_PROXY_PORTS:
        proxy = f"127.0.0.1:{port}"
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.35):
                found.setdefault(proxy, {"proxy": proxy, "source": "本机常见代理端口", "reachable": True, "github_ok": None})
        except OSError:
            pass
    results = list(found.values())
    if verify:
        for item in results:
            item["github_ok"] = _proxy_works(item["proxy"])
            item["reachable"] = item["github_ok"]
    return results


def set_git_proxy(proxy: str) -> tuple[bool, str]:
    if not proxy.strip():
        return False, "代理地址为空"
    try:
        run_git(["config", "--global", "--replace-all", "http.https://github.com.proxy", proxy.strip()], timeout=8)
        return True, f"已为 GitHub 设置 Git 代理: {proxy.strip()}"
    except Exception as exc:
        return False, str(exc)


def clear_git_proxy() -> tuple[bool, str]:
    try:
        run_git(["config", "--global", "--unset-all", "http.https://github.com.proxy"], timeout=8)
        return True, "已清除 GitHub 专用 Git 代理"
    except Exception as exc:
        return False, str(exc)


def inspect_git(*, test_ssh: bool = True) -> GitInfo:
    info = GitInfo()
    try:
        info.version = git_version()
        info.installed = bool(info.version)
        if not info.installed:
            info.error = "未安装 Git 或 Git 不在 PATH 中"
            return info
        info.user_name = _get_config("user.name")
        info.user_email = _get_config("user.email")
        info.proxy = _get_config("http.https://github.com.proxy")
        info.http_version = _get_config("http.https://github.com.version")
        info.instead_of = _get_all(SSH_STATE_KEY)
        info.url_rewrites = inspect_url_rewrites()
        info.risky_rewrites = [item for item in info.url_rewrites if item.get("risky")]
        info.ssh_config = inspect_ssh_config()
        managed = has_managed_ssh_block(info.ssh_config)
        info.ssh_ready = managed
        if test_ssh:
            ok, detail = test_ssh_443()
            info.ssh_detail = detail
            info.ssh_ready = managed and ok
        else:
            info.ssh_detail = "未测试（在 Git 页可单独测试）"
        return info
    except Exception as exc:
        info.error = str(exc)
        return info

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .paths import backup_dir, hosts_path


OUR_START = "# >>> GitHubFix managed block >>>"
OUR_END = "# <<< GitHubFix managed block <<<"
LEGACY_START = "# === GitHub Fix START ==="
LEGACY_END = "# === GitHub Fix END ==="
LEGACY_START_2 = "# GitHubFix START"
LEGACY_END_2 = "# GitHubFix END"

_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$")


@dataclass
class HostsSnapshot:
    text: str
    encoding: str
    managed_count: int = 0
    legacy_count: int = 0
    entries: dict[str, list[str]] = field(default_factory=dict)


def is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def valid_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


def valid_hostname(value: str) -> bool:
    return bool(_DOMAIN_RE.match(value.strip())) and len(value) <= 253


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def decode_hosts(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        # Windows 中文系统上的 hosts 可能是 ANSI/GBK，避免破坏原有注释。
        return data.decode("mbcs"), "mbcs"


def read_hosts(path: Path | None = None) -> HostsSnapshot:
    target = path or hosts_path()
    raw = _read_bytes(target)
    text, encoding = decode_hosts(raw)
    managed, legacy, entries = inspect_text(text)
    return HostsSnapshot(text=text, encoding=encoding, managed_count=managed, legacy_count=legacy, entries=entries)


def _managed_markers() -> tuple[tuple[str, str], ...]:
    return (
        (OUR_START, OUR_END),
        (LEGACY_START, LEGACY_END),
        (LEGACY_START_2, LEGACY_END_2),
    )


def strip_managed_blocks(text: str) -> tuple[str, int, int]:
    """Remove blocks written by GitHubFix (including the earlier script).

    Returns (new_text, our_blocks_removed, legacy_blocks_removed).
    Unknown user content is preserved exactly, except for a final newline fix.
    An unbalanced start marker is preserved rather than deleting user content.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    ours = 0
    legacy = 0
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        marker: tuple[str, str] | None = None
        for start, end in _managed_markers():
            if stripped == start or stripped.startswith(start + " "):
                marker = (start, end)
                break
        if marker is None:
            out.append(lines[i])
            i += 1
            continue
        start_marker, end_marker = marker
        j = i + 1
        while j < len(lines):
            end_stripped = lines[j].strip()
            if end_stripped == end_marker or end_stripped.startswith(end_marker + " "):
                break
            j += 1
        if j >= len(lines):
            # 没有结束标记：可能文件损坏或被手工截断，保留原文更安全。
            out.append(lines[i])
            i += 1
            continue
        if start_marker == OUR_START:
            ours += 1
        else:
            legacy += 1
        i = j + 1
    new_text = "".join(out)
    new_text = new_text.rstrip("\r\n") + ("\n" if new_text.strip() else "")
    return new_text, ours, legacy


def inspect_text(text: str) -> tuple[int, int, dict[str, list[str]]]:
    managed = 0
    legacy = 0
    entries: dict[str, list[str]] = {}
    active: tuple[str, str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if active is None:
            for start, end in _managed_markers():
                if stripped == start or stripped.startswith(start + " "):
                    active = (start, end)
                    if start == OUR_START:
                        managed += 1
                    else:
                        legacy += 1
                    break
        else:
            if stripped == active[1] or stripped.startswith(active[1] + " "):
                active = None
                continue
            if stripped and not stripped.startswith("#"):
                parts = stripped.split()
                if len(parts) >= 2 and valid_public_ip(parts[0]):
                    for host in parts[1:]:
                        host = host.split("#", 1)[0].strip().lower()
                        if valid_hostname(host):
                            entries.setdefault(host, []).append(parts[0])
    return managed, legacy, entries


def build_block(entries: dict[str, str], note: str = "") -> str:
    ordered_hosts = sorted(entries, key=lambda h: (h != "github.com", h))
    lines = [OUR_START]
    lines.append("# GitHubFix: 自动测速生成；删除本块或点击“恢复系统 DNS”即可撤销。")
    if note:
        lines.append(f"# {note}")
    for host in ordered_hosts:
        ip = entries[host].strip()
        if valid_hostname(host) and valid_public_ip(ip):
            lines.append(f"{ip}\t{host}")
    lines.append(OUR_END)
    return "\n".join(lines) + "\n"


def _timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + f"-{int((time.time() % 1) * 1_000_000):06d}"


def backup_hosts(path: Path | None = None) -> Path:
    target = path or hosts_path()
    dest = backup_dir() / f"hosts-{_timestamp()}.bak"
    shutil.copy2(target, dest)
    backups = sorted(backup_dir().glob("hosts-*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[30:]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=".githubfix-", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def apply_hosts(
    entries: dict[str, str],
    path: Path | None = None,
    *,
    dry_run: bool = False,
    create_backup: bool = True,
    flush: bool = True,
) -> tuple[str, str, int]:
    """Replace GitHubFix-managed blocks and append a new benchmarked block.

    Returns (new_text, backup_path, legacy_removed).
    """
    target = path or hosts_path()
    snapshot = read_hosts(target)
    before = snapshot.text
    cleaned, _ours, legacy = strip_managed_blocks(before)
    clean_entries = {
        host.lower().strip(): ip.strip()
        for host, ip in entries.items()
        if valid_hostname(host) and valid_public_ip(ip)
    }
    block = build_block(clean_entries)
    if not clean_entries:
        raise ValueError("没有可写入的 GitHub IP，已取消修改。")
    if cleaned and not cleaned.endswith("\n"):
        cleaned += "\n"
    if cleaned.strip():
        sep = "" if cleaned.endswith("\n") else "\n"
        new_text = cleaned + sep + block
    else:
        new_text = block
    if dry_run:
        return new_text, "", legacy
    if not is_admin():
        raise PermissionError("修改系统 hosts 需要管理员权限。请以管理员身份重启 GitHubFix。")
    backup_path = ""
    if create_backup:
        backup_path = str(backup_hosts(target))
    encoded = new_text.encode(snapshot.encoding, errors="replace")
    _atomic_write(target, encoded)
    if flush:
        flush_dns()
    return new_text, backup_path, legacy


def restore_hosts(
    path: Path | None = None,
    *,
    dry_run: bool = False,
    create_backup: bool = True,
    flush: bool = True,
) -> tuple[str, str, int]:
    target = path or hosts_path()
    snapshot = read_hosts(target)
    cleaned, ours, legacy = strip_managed_blocks(snapshot.text)
    removed = ours + legacy
    if dry_run:
        return cleaned, "", removed
    if not is_admin():
        raise PermissionError("修改系统 hosts 需要管理员权限。")
    backup_path = ""
    if create_backup:
        backup_path = str(backup_hosts(target))
    _atomic_write(target, cleaned.encode(snapshot.encoding, errors="replace"))
    if flush:
        flush_dns()
    return cleaned, backup_path, removed


def flush_dns() -> tuple[bool, str]:
    try:
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            ["ipconfig", "/flushdns"],
            capture_output=True,
            timeout=15,
            creationflags=flags,
        )
        encoding = "mbcs" if os.name == "nt" else "utf-8"
        stdout = (proc.stdout or b"").decode(encoding, errors="replace").strip()
        stderr = (proc.stderr or b"").decode(encoding, errors="replace").strip()
        message = stdout or stderr
        return proc.returncode == 0, message
    except Exception as exc:  # pragma: no cover - platform specific
        return False, str(exc)

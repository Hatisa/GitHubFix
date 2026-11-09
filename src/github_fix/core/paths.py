from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "GitHubFix"


def data_dir() -> Path:
    override = os.environ.get("GITHUBFIX_DATA_DIR")
    if override:
        path = Path(override).expanduser()
    else:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def backup_dir() -> Path:
    path = data_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def reports_dir() -> Path:
    path = data_dir() / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resource_path(relative: str) -> Path:
    """Return a bundled resource path, compatible with PyInstaller onefile."""
    if getattr(sys, "_MEIPASS", None):
        root = Path(sys._MEIPASS)
    else:
        # src/github_fix/core/paths.py -> project root
        root = Path(__file__).resolve().parents[3]
    return root / relative


def hosts_path() -> Path:
    override = os.environ.get("GITHUBFIX_HOSTS_PATH")
    if override:
        return Path(override).expanduser()
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return Path(system_root) / "System32" / "drivers" / "etc" / "hosts"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class DomainSpec:
    key: str
    host: str
    path: str = "/"
    required: bool = False
    kind: str = "https"  # https | ssh
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# 这些域名覆盖浏览、API、Git 克隆、Raw 文件、头像和 Release 下载。
DOMAINS: tuple[DomainSpec, ...] = (
    DomainSpec("web", "github.com", "/", True, "https", "GitHub 网页"),
    DomainSpec("api", "api.github.com", "/meta", True, "https", "GitHub API"),
    DomainSpec("codeload", "codeload.github.com", "/", True, "https", "代码归档下载"),
    DomainSpec("raw", "raw.githubusercontent.com", "/521xueweihan/GitHub520/main/hosts", True, "https", "Raw 文件"),
    DomainSpec("assets", "github.githubassets.com", "/", True, "https", "网页静态资源"),
    DomainSpec("avatars", "avatars.githubusercontent.com", "/u/1?s=40", False, "https", "用户头像"),
    DomainSpec("objects", "objects.githubusercontent.com", "/", False, "https", "Release 对象"),
    DomainSpec("release_assets", "release-assets.githubusercontent.com", "/", False, "https", "Release 下载"),
    DomainSpec("gist", "gist.githubusercontent.com", "/", False, "https", "Gist 内容"),
    DomainSpec("ssh", "ssh.github.com", "/", False, "ssh", "Git SSH (443)"),
)

DOMAIN_BY_HOST = {d.host: d for d in DOMAINS}
DOMAIN_BY_KEY = {d.key: d for d in DOMAINS}
REQUIRED_HOSTS = tuple(d.host for d in DOMAINS if d.required)


@dataclass
class ProbeResult:
    host: str
    ip: str
    source: str = ""
    tcp_ms: float | None = None
    tls_ms: float | None = None
    http_ms: float | None = None
    status: int | None = None
    error: str = ""
    ssh_banner: str = ""
    ok: bool = False
    score: float = 999999.0
    attempts: int = 1
    successes: int = 0
    stability: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def latency_ms(self) -> float | None:
        if self.tcp_ms is None:
            return None
        return round(self.tcp_ms + (self.tls_ms or 0) + (self.http_ms or 0), 1)


@dataclass
class MirrorResult:
    name: str
    base: str
    ok: bool = False
    latency_ms: float | None = None
    status: int | None = None
    error: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HostsApplyResult:
    path: str
    backup: str = ""
    entries: dict[str, str] = field(default_factory=dict)
    removed_blocks: int = 0
    written: bool = False
    dry_run: bool = False
    block: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DiagnosisReport:
    generated_at: str
    admin: bool
    hosts_path: str
    hosts_managed: bool
    hosts_legacy: bool
    pinned: dict[str, str] = field(default_factory=dict)
    domains: dict[str, Any] = field(default_factory=dict)
    best: dict[str, ProbeResult] = field(default_factory=dict)
    mirrors: list[MirrorResult] = field(default_factory=list)
    git: dict[str, Any] = field(default_factory=dict)
    proxies: list[dict[str, Any]] = field(default_factory=list)
    network: dict[str, Any] = field(default_factory=dict)
    summary: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["best"] = {k: v.to_dict() for k, v in self.best.items()}
        data["mirrors"] = [m.to_dict() for m in self.mirrors]
        return data

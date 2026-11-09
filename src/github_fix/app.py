from __future__ import annotations

import ctypes
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .core import git_tools
from .core.diagnostics import diagnose
from .core.hosts import is_admin, read_hosts
from .core.mirrors import clone_command, probe_mirrors
from .core.models import DOMAINS
from .core.paths import resource_path
from .core.proxy_controller import ProxyController
from .core.reporting import save_report
from .core.service import repair, restore
from .elevation import relaunch_as_admin


BG = "#0b1220"
BG2 = "#0f1a2e"
CARD = "#121d33"
CARD2 = "#17243e"
FG = "#e6edf7"
MUTED = "#93a4c3"
ACCENT = "#2f81f7"
ACCENT_HOVER = "#1f6feb"
GREEN = "#3fb950"
RED = "#f85149"
YELLOW = "#d29922"
BORDER = "#26385a"
FONT = ("Microsoft YaHei UI", 10)
FONT_SMALL = ("Microsoft YaHei UI", 9)
FONT_BOLD = ("Microsoft YaHei UI", 10, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 22, "bold")
FONT_SUBTITLE = ("Microsoft YaHei UI", 10)


def launch() -> int:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GitHubFix.Desktop.1")
    except Exception:
        pass
    root = tk.Tk()
    root._githubfix_app = GitHubFixApp(root)
    root.mainloop()
    return 0


class GitHubFixApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.controller = ProxyController()
        self.report = None
        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self._closing = False
        self._domain_results: dict[str, dict] = {}
        self._mirrors = []
        self._report_monotonic = 0.0
        self._scale = 1.0
        self._action_buttons: list[ttk.Button] = []
        self._build_style()
        self._build_ui()
        self._poll_queue()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(250, self._startup)

    # ---------- UI ----------
    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=FG, font=FONT)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Card.TLabel", background=CARD, foreground=FG)
        style.configure("Title.TLabel", background=BG, foreground=FG, font=FONT_TITLE)
        style.configure("Subtitle.TLabel", background=BG, foreground=MUTED, font=FONT_SUBTITLE)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=FONT_SMALL)
        style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED, font=FONT_SMALL)
        style.configure("Section.TLabel", background=CARD, foreground=FG, font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("TButton", background=CARD2, foreground=FG, borderwidth=0, padding=(12, 8), focusthickness=0, font=FONT)
        style.map("TButton", background=[("active", "#1e3152"), ("disabled", "#17243e")], foreground=[("disabled", "#61718e")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", font=("Microsoft YaHei UI", 11, "bold"), padding=(16, 12))
        style.map("Accent.TButton", background=[("active", ACCENT_HOVER), ("disabled", "#2a3a55")])
        style.configure("Danger.TButton", background="#5b1f24", foreground="#ffdcdc")
        style.map("Danger.TButton", background=[("active", "#7a292f")])
        style.configure("Small.TButton", padding=(9, 5), font=FONT_SMALL)
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 8, 0, 0))
        style.configure("TNotebook.Tab", background=BG2, foreground=MUTED, padding=(18, 9), borderwidth=0, font=FONT)
        style.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", FG)])
        style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG, rowheight=29, borderwidth=0, font=FONT_SMALL)
        style.configure("Treeview.Heading", background=CARD2, foreground=MUTED, relief="flat", font=("Microsoft YaHei UI", 9, "bold"), padding=(6, 6))
        style.map("Treeview", background=[("selected", "#1f3b66")], foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("active", CARD2)])
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=BG2, borderwidth=0)
        style.configure("TEntry", fieldbackground=BG2, foreground=FG, insertcolor=FG, borderwidth=0, padding=7)

    def _build_ui(self) -> None:
        self.root.title(f"GitHubFix {__version__} - GitHub 智能连接助手")
        self.root.geometry("1160x780")
        self.root.minsize(1000, 680)
        self.root.configure(bg=BG)
        icon = resource_path("assets/githubfix.ico")
        if icon.exists():
            try:
                self.root.iconbitmap(str(icon))
            except Exception:
                pass

        header = ttk.Frame(self.root, padding=(22, 18, 22, 8))
        header.pack(fill="x")
        left = ttk.Frame(header)
        left.pack(side="left")
        ttk.Label(left, text="GitHubFix", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            left,
            text="智能选路、hosts 优化、Git SSH 443 与本地连接器：让 GitHub 访问更稳定",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(3, 0))
        badges = ttk.Frame(header)
        badges.pack(side="right", pady=(6, 0))
        self.admin_badge = tk.Label(badges, text="检测中", bg=CARD2, fg=MUTED, padx=10, pady=4, font=FONT_SMALL)
        self.admin_badge.pack(side="left", padx=4)
        self.hosts_badge = tk.Label(badges, text="Hosts 未知", bg=CARD2, fg=MUTED, padx=10, pady=4, font=FONT_SMALL)
        self.hosts_badge.pack(side="left", padx=4)
        self.proxy_badge = tk.Label(badges, text="代理未运行", bg=CARD2, fg=MUTED, padx=10, pady=4, font=FONT_SMALL)
        self.proxy_badge.pack(side="left", padx=4)

        body = ttk.Frame(self.root, padding=(22, 6, 22, 0))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, minsize=272)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(body, style="Card.TFrame", padding=16)
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        ttk.Label(sidebar, text="操作", style="Section.TLabel").pack(anchor="w", pady=(0, 10))
        self.one_click_btn = ttk.Button(sidebar, text="一键智能加速", style="Accent.TButton", command=self._one_click)
        self.one_click_btn.pack(fill="x", pady=(0, 8))
        self._action_buttons.append(self.one_click_btn)
        self.hosts_btn = ttk.Button(sidebar, text="仅优化 Hosts", command=self._hosts_only)
        self.hosts_btn.pack(fill="x", pady=3)
        self._action_buttons.append(self.hosts_btn)
        self.proxy_btn = ttk.Button(sidebar, text="开启智能代理", command=self._toggle_proxy)
        self.proxy_btn.pack(fill="x", pady=3)
        self._action_buttons.append(self.proxy_btn)
        self.refresh_btn = ttk.Button(sidebar, text="重新检测网络", command=lambda: self._refresh_diagnose(include_mirrors=False))
        self.refresh_btn.pack(fill="x", pady=3)
        self._action_buttons.append(self.refresh_btn)
        self.export_btn = ttk.Button(sidebar, text="导出诊断报告", command=self._export_report)
        self.export_btn.pack(fill="x", pady=3)
        self._action_buttons.append(self.export_btn)
        self.restore_btn = ttk.Button(sidebar, text="恢复系统设置", style="Danger.TButton", command=self._restore_all)
        self.restore_btn.pack(fill="x", pady=(14, 3))
        self._action_buttons.append(self.restore_btn)

        ttk.Separator(sidebar, orient="horizontal").pack(fill="x", pady=16)
        ttk.Label(sidebar, text="当前状态", style="Card.TLabel", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        self.side_hosts = tk.Label(sidebar, text="Hosts: 尚未检测", bg=CARD, fg=MUTED, font=FONT_SMALL, anchor="w", justify="left", wraplength=230)
        self.side_hosts.pack(fill="x", pady=(6, 2))
        self.side_proxy = tk.Label(sidebar, text="代理: 未运行", bg=CARD, fg=MUTED, font=FONT_SMALL, anchor="w", justify="left", wraplength=230)
        self.side_proxy.pack(fill="x", pady=2)
        self.side_git = tk.Label(sidebar, text="Git: 检测中", bg=CARD, fg=MUTED, font=FONT_SMALL, anchor="w", justify="left", wraplength=230)
        self.side_git.pack(fill="x", pady=2)
        ttk.Label(
            sidebar,
            text="提示：公共镜像只适合公开仓库。不要把令牌、私有仓库或密码交给第三方镜像。",
            style="CardMuted.TLabel",
            wraplength=230,
            justify="left",
        ).pack(side="bottom", anchor="w", pady=(14, 0))

        self.notebook = ttk.Notebook(body)
        self.notebook.grid(row=0, column=1, sticky="nsew")
        self._build_overview_tab()
        self._build_proxy_tab()
        self._build_git_tab()
        self._build_mirror_tab()
        self._build_log_tab()

        footer = ttk.Frame(self.root, padding=(22, 8, 22, 12))
        footer.pack(fill="x")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", style="Horizontal.TProgressbar")
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 14))
        self.status_label = ttk.Label(footer, text="就绪", style="Muted.TLabel")
        self.status_label.pack(side="right")

    def _tab_frame(self, title: str) -> ttk.Frame:
        frame = ttk.Frame(self.notebook, style="Card.TFrame", padding=16)
        self.notebook.add(frame, text=title)
        return frame

    def _build_overview_tab(self) -> None:
        frame = self._tab_frame("总览")
        top = ttk.Frame(frame, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="诊断结论", style="Section.TLabel").pack(side="left")
        self.summary_time = ttk.Label(top, text="尚未检测", style="CardMuted.TLabel")
        self.summary_time.pack(side="right")
        self.summary_label = tk.Label(
            frame,
            text="正在准备检测…",
            bg=CARD,
            fg=FG,
            font=("Microsoft YaHei UI", 10),
            justify="left",
            anchor="w",
            wraplength=760,
        )
        self.summary_label.pack(fill="x", pady=(8, 10))
        actions = ttk.Frame(frame, style="Card.TFrame")
        actions.pack(fill="x", pady=(0, 6))
        ttk.Button(actions, text="查看选中域名的全部候选", style="Small.TButton", command=self._show_candidates).pack(side="left")
        ttk.Button(actions, text="测试公共镜像", style="Small.TButton", command=self._probe_mirrors).pack(side="left", padx=6)
        ttk.Button(actions, text="刷新 Git 信息", style="Small.TButton", command=self._refresh_git).pack(side="left")

        columns = ("host", "label", "ip", "stability", "latency", "http", "status")
        self.domain_tree = ttk.Treeview(frame, columns=columns, show="headings", height=11)
        headings = {
            "host": "域名",
            "label": "用途",
            "ip": "最佳 IP",
            "stability": "稳定性",
            "latency": "总耗时",
            "http": "HTTP",
            "status": "状态",
        }
        widths = {"host": 220, "label": 130, "ip": 130, "stability": 70, "latency": 80, "http": 60, "status": 80}
        for key, text in headings.items():
            self.domain_tree.heading(key, text=text)
            self.domain_tree.column(key, width=widths[key], anchor="w" if key in ("host", "label") else "center")
        self.domain_tree.tag_configure("ok", foreground=GREEN)
        self.domain_tree.tag_configure("bad", foreground=RED)
        self.domain_tree.tag_configure("warn", foreground=YELLOW)
        self.domain_tree.pack(fill="both", expand=True)
        self.domain_tree.bind("<Double-1>", lambda event: self._show_candidates())
        ttk.Label(
            frame,
            text="双击任意行可查看该域名的全部候选 IP、TLS 校验结果与失败原因。",
            style="CardMuted.TLabel",
        ).pack(anchor="w", pady=(6, 0))

    def _build_proxy_tab(self) -> None:
        frame = self._tab_frame("智能代理")
        ttk.Label(frame, text="本地智能连接器", style="Section.TLabel").pack(anchor="w")
        self.proxy_running_label = tk.Label(frame, text="未运行", bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 12, "bold"), anchor="w")
        self.proxy_running_label.pack(fill="x", pady=(8, 2))
        self.proxy_detail_label = tk.Label(frame, text="", bg=CARD, fg=MUTED, font=FONT_SMALL, anchor="w", justify="left", wraplength=760)
        self.proxy_detail_label.pack(fill="x", pady=(0, 8))
        self.proxy_stats_label = tk.Label(frame, text="", bg=CARD, fg=FG, font=FONT_SMALL, anchor="w", justify="left", wraplength=760)
        self.proxy_stats_label.pack(fill="x", pady=(0, 10))
        buttons = ttk.Frame(frame, style="Card.TFrame")
        buttons.pack(fill="x")
        ttk.Button(buttons, text="开启智能代理", command=lambda: self._proxy_action("start")).pack(side="left")
        ttk.Button(buttons, text="停止并恢复", command=lambda: self._proxy_action("stop")).pack(side="left", padx=8)
        ttk.Button(buttons, text="刷新状态", command=self._refresh_proxy_status).pack(side="left")
        ttk.Button(buttons, text="复制 PAC 地址", command=self._copy_pac).pack(side="left", padx=8)
        explanation = (
            "工作原理：PAC 只把 GitHub 相关域名交给 127.0.0.1 的本地连接器。连接器在多个已通过 "
            "TLS 证书校验的 IP 之间并发竞速，选出响应最快的线路；HTTPS 仍然是浏览器与 GitHub "
            "端到端加密，本工具不解密、不记录账号密码。\n\n"
            "关闭程序时会自动恢复原来的系统代理设置。即使程序异常退出，PAC 中也带有 DIRECT 兜底。"
        )
        ttk.Label(frame, text=explanation, style="CardMuted.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(18, 0))
        ttk.Label(
            frame,
            text="注意：智能代理不是 VPN；如果当前网络对 GitHub 全部 IP 做了封锁，它无法凭空突破封锁。",
            style="CardMuted.TLabel",
            foreground=YELLOW,
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(12, 0))

    def _build_git_tab(self) -> None:
        frame = self._tab_frame("Git 加速")
        ttk.Label(frame, text="Git / SSH 诊断", style="Section.TLabel").pack(anchor="w")
        self.git_info_label = tk.Label(frame, text="正在读取 Git 配置…", bg=CARD, fg=FG, font=FONT_SMALL, anchor="w", justify="left", wraplength=760)
        self.git_info_label.pack(fill="x", pady=(8, 4))
        self.git_rewrite_label = tk.Label(frame, text="", bg=CARD, fg=MUTED, font=FONT_SMALL, anchor="w", justify="left", wraplength=760)
        self.git_rewrite_label.pack(fill="x", pady=(0, 8))
        buttons = ttk.Frame(frame, style="Card.TFrame")
        buttons.pack(fill="x", pady=(0, 8))
        ttk.Button(buttons, text="测试 SSH 443", command=self._test_ssh).pack(side="left")
        ttk.Button(buttons, text="启用 SSH 443", command=self._apply_ssh).pack(side="left", padx=6)
        ttk.Button(buttons, text="移除 SSH 443", command=self._remove_ssh).pack(side="left")
        ttk.Button(buttons, text="清理第三方 URL 改写", style="Danger.TButton", command=self._clean_rewrites).pack(side="left", padx=6)
        ttk.Button(buttons, text="刷新", command=self._refresh_git).pack(side="left")
        self.git_output = tk.Text(
            frame,
            height=12,
            bg=BG2,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            font=("Consolas", 9),
            padx=10,
            pady=8,
            wrap="word",
        )
        self.git_output.pack(fill="both", expand=True, pady=(2, 0))
        ttk.Label(
            frame,
            text="SSH 443 会把 git@github.com: 的 SSH 地址重写到 github-443 别名，仅影响 GitHub SSH，不改变 HTTPS 配置。",
            style="CardMuted.TLabel",
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

    def _build_mirror_tab(self) -> None:
        frame = self._tab_frame("镜像下载")
        ttk.Label(frame, text="公共镜像测速", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text="仅用于公开仓库/Release/Raw 文件。第三方镜像可以看到请求内容，切勿用于私有仓库或携带令牌的 URL。",
            style="CardMuted.TLabel",
            foreground=YELLOW,
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(4, 8))
        columns = ("name", "status", "latency", "note")
        self.mirror_tree = ttk.Treeview(frame, columns=columns, show="headings", height=8)
        for key, text, width in (("name", "镜像", 160), ("status", "状态", 80), ("latency", "延迟", 90), ("note", "说明", 380)):
            self.mirror_tree.heading(key, text=text)
            self.mirror_tree.column(key, width=width, anchor="w" if key in ("name", "note") else "center")
        self.mirror_tree.tag_configure("ok", foreground=GREEN)
        self.mirror_tree.tag_configure("bad", foreground=RED)
        self.mirror_tree.pack(fill="both", expand=True)
        row = ttk.Frame(frame, style="Card.TFrame")
        row.pack(fill="x", pady=(10, 4))
        ttk.Label(row, text="GitHub 仓库 URL", style="Card.TLabel").pack(side="left")
        self.repo_entry = ttk.Entry(row, width=50)
        self.repo_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.repo_entry.insert(0, "https://github.com/521xueweihan/GitHub520")
        ttk.Button(row, text="测速", style="Small.TButton", command=self._probe_mirrors).pack(side="left")
        ttk.Button(row, text="复制加速克隆命令", style="Small.TButton", command=self._copy_clone).pack(side="left", padx=6)
        self.mirror_hint = ttk.Label(frame, text="尚未测速", style="CardMuted.TLabel", wraplength=760, justify="left")
        self.mirror_hint.pack(anchor="w", pady=(4, 0))

    def _build_log_tab(self) -> None:
        frame = self._tab_frame("运行日志")
        self.log_text = tk.Text(
            frame,
            bg=BG2,
            fg=MUTED,
            insertbackground=FG,
            relief="flat",
            font=("Consolas", 9),
            padx=10,
            pady=8,
            wrap="word",
            state="disabled",
        )
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.log_text.tag_configure("info", foreground=MUTED)
        self.log_text.tag_configure("ok", foreground=GREEN)
        self.log_text.tag_configure("err", foreground=RED)
        self.log_text.tag_configure("warn", foreground=YELLOW)

    # ---------- task plumbing ----------
    def _log(self, message: str, level: str = "info") -> None:
        if not hasattr(self, "log_text"):
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n", level)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll_queue(self) -> None:
        if self._closing:
            return
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self._log(str(payload))
                elif kind == "done":
                    result, callback = payload
                    self._set_busy(False)
                    if callback:
                        try:
                            callback(result)
                        except Exception as exc:
                            self._log(f"回调失败: {exc}", "err")
                elif kind == "error":
                    exc, callback = payload
                    self._set_busy(False)
                    self._log(f"操作失败: {exc}", "err")
                    if callback:
                        try:
                            callback(None)
                        except Exception:
                            pass
                    messagebox.showerror("操作失败", str(exc), parent=self.root)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        for button in self._action_buttons:
            try:
                button.configure(state="disabled" if busy else "normal")
            except tk.TclError:
                pass
        if busy:
            self.progress.start(12)
            self.status_label.configure(text=text or "处理中…")
        else:
            self.progress.stop()
            self.status_label.configure(text="就绪")

    def _run_task(self, name: str, func, callback=None) -> None:
        if self._busy:
            return
        self._set_busy(True, name)
        self._log(f"▶ {name}", "info")
        q = self._queue

        def worker():
            try:
                result = func(lambda message: q.put(("log", message)))
                q.put(("done", (result, callback)))
            except Exception as exc:
                q.put(("error", (exc, callback)))

        threading.Thread(target=worker, name="GitHubFixTask", daemon=True).start()

    # ---------- startup / diagnosis ----------
    def _startup(self) -> None:
        self._log(f"GitHubFix {__version__} 启动", "ok")
        if os.environ.get("GITHUBFIX_NO_AUTOSTART") == "1":
            self._update_badges()
            return
        try:
            recovered, message = ProxyController.recover_stale()
            if recovered:
                self._log(message, "warn")
        except Exception as exc:
            self._log(f"检查残留代理设置失败: {exc}", "warn")
        self._refresh_proxy_status()
        self._refresh_diagnose(include_mirrors=False)

    def _refresh_diagnose(self, include_mirrors: bool = False) -> None:
        def work(log):
            return diagnose(progress=log, include_mirrors=include_mirrors, quick=True)

        self._run_task("网络诊断", work, self._apply_report)

    def _apply_report(self, report) -> None:
        if report is None:
            return
        self.report = report
        self._report_monotonic = time.monotonic()
        self._domain_results = {host: item for host, item in report.domains.items()}
        for item in self.domain_tree.get_children():
            self.domain_tree.delete(item)
        for spec in DOMAINS:
            best = report.best.get(spec.host)
            if best:
                stability = f"{best.stability * 100:.0f}%"
                values = (
                    spec.host,
                    spec.label,
                    best.ip,
                    stability,
                    f"{best.latency_ms} ms" if best.latency_ms is not None else "-",
                    str(best.status or "-"),
                    "可用",
                )
                tag = "ok" if best.stability >= 0.99 else "warn"
            else:
                values = (spec.host, spec.label, "-", "-", "-", "-", "不可用")
                tag = "bad"
            self.domain_tree.insert("", "end", values=values, tags=(tag,))
        summary = "\n".join(f"• {line}" for line in report.summary)
        if report.recommendations:
            summary += "\n" + "\n".join(f"→ {line}" for line in report.recommendations[:3])
        self.summary_label.configure(text=summary)
        self.summary_time.configure(text=report.generated_at.replace("T", " "))
        self._update_badges()
        if report.mirrors:
            self._apply_mirrors(report.mirrors)
        for line in report.recommendations:
            self._log("建议: " + line, "warn")
        self._refresh_proxy_status()
        self._update_git_labels_from_report(report)

    def _update_badges(self) -> None:
        admin = is_admin()
        self.admin_badge.configure(
            text="管理员" if admin else "普通用户（修复需管理员）",
            fg=GREEN if admin else RED,
            bg="#132a1b" if admin else "#331b1e",
        )
        if self.report:
            pinned = self.report.hosts_managed
            self.hosts_badge.configure(
                text="Hosts 已优化" if pinned else "Hosts 未优化",
                fg=GREEN if pinned else YELLOW,
                bg="#132a1b" if pinned else "#332a12",
            )
            self.side_hosts.configure(
                text=f"Hosts: {'已安装 GitHubFix 配置' if pinned else '未安装'}\n文件: {self.report.hosts_path}",
                fg=GREEN if pinned else MUTED,
            )
        else:
            try:
                snapshot = read_hosts()
                installed = snapshot.managed_count > 0
                self.hosts_badge.configure(text="Hosts 已优化" if installed else "Hosts 未优化", fg=GREEN if installed else YELLOW)
                self.side_hosts.configure(text="Hosts: " + ("已安装 GitHubFix 配置" if installed else "未安装"), fg=GREEN if installed else MUTED)
            except Exception:
                pass

    def _update_git_labels_from_report(self, report) -> None:
        git = report.git or {}
        text = f"Git: {git.get('version') or '未安装'}\n"
        if git.get("proxy"):
            text += f"GitHub 专用代理: {git['proxy']}\n"
        self.side_git.configure(text=text.strip(), fg=FG if git.get("version") else RED)
        self.git_info_label.configure(
            text=f"Git: {git.get('version') or '未安装'}   用户: {git.get('user_name') or '-'}   "
            f"GitHub 专用代理: {git.get('proxy') or '未配置'}\nSSH 443 配置: {'已启用' if git.get('ssh_ready') else '未启用'}",
        )
        risky = git.get("risky_rewrites") or []
        if risky:
            details = "\n".join(f"  {item.get('base')} → {item.get('instead_of')}" for item in risky)
            self.git_rewrite_label.configure(text=f"检测到第三方 Git URL 改写（可能绕过智能代理，影响 push/私有仓库）：\n{details}", fg=RED)
        else:
            self.git_rewrite_label.configure(text="未检测到第三方 Git URL 改写。", fg=MUTED)

    # ---------- actions ----------
    def _one_click(self) -> None:
        if not self._require_admin():
            return
        risky = ((self.report.git or {}).get("risky_rewrites") if self.report else []) or []
        clean_rewrites = False
        if risky:
            detail = "\n".join(f"{item.get('base')}  <=  {item.get('instead_of')}" for item in risky)
            choice = messagebox.askyesnocancel(
                "发现 Git URL 改写",
                "检测到第三方 Git URL 改写，Git 会绕过 hosts 和智能代理，且可能影响私有仓库/push：\n\n"
                + detail
                + "\n\n是否在本次加速前清理？（会先备份 .gitconfig）\n"
                "选择“否”则保留并继续。",
                parent=self.root,
            )
            if choice is None:
                return
            clean_rewrites = bool(choice)

        def work(log):
            clean_success = False
            if clean_rewrites:
                log("清理第三方 Git URL 改写…")
                clean_success, clean_message = git_tools.clean_github_url_rewrites()
                log(clean_message, "ok" if clean_success else "err")
            report = self._recent_report()
            if report is None:
                log("开始智能加速：诊断 → 写入 hosts → 启动本地连接器")
                report = diagnose(progress=log, include_mirrors=False, quick=True)
            else:
                log("复用 3 分钟内的诊断结果，避免重复探测。")
            if clean_success and report.git:
                report.git["risky_rewrites"] = []
                report.git["url_rewrites"] = [item for item in (report.git.get("url_rewrites") or []) if not item.get("risky")]
            outcome = repair(report, progress=log)
            log("启动本地智能连接器…")
            proxy_ok, proxy_message = self.controller.start()
            return report, outcome, proxy_ok, proxy_message

        self._run_task("一键智能加速", work, self._one_click_done)

    def _one_click_done(self, result) -> None:
        if not result:
            return
        report, outcome, proxy_ok, proxy_message = result
        self._apply_report(report)
        self._log(outcome.message, "ok" if outcome.applied else "err")
        self._log(proxy_message, "ok" if proxy_ok else "warn")
        self._refresh_proxy_status()
        lines = [outcome.message]
        lines.append(proxy_message)
        risky = (report.git or {}).get("risky_rewrites") or []
        if outcome.applied and proxy_ok:
            lines.append("可以打开浏览器测试 https://github.com。")
            if risky:
                lines.append("注意：检测到第三方 Git URL 改写，Git 仍会走镜像；请在“Git 加速”页清理后重新开启智能代理。")
            else:
                lines.append("Git 克隆会自动使用本地连接器。")
        elif outcome.applied:
            lines.append("hosts 已生效，但智能代理未启动。仍可尝试直接访问或 SSH 443。")
        elif proxy_ok:
            lines.append("hosts 未写入（可用 IP 不足或验证失败），但本地智能连接器已启动，可先用于 Git HTTPS。")
        else:
            lines.append("hosts 与智能代理均未成功。请改用 SSH 443 或你所在组织允许的合法代理。")
        messagebox.showinfo("智能加速结果", "\n\n".join(lines), parent=self.root)

    def _hosts_only(self) -> None:
        if not self._require_admin():
            return

        def work(log):
            report = self._recent_report()
            if report is None:
                report = diagnose(progress=log, include_mirrors=False, quick=True)
            else:
                log("复用 3 分钟内的诊断结果。")
            outcome = repair(report, progress=log)
            return report, outcome

        self._run_task("优化 Hosts", work, self._hosts_done)

    def _hosts_done(self, result) -> None:
        if not result:
            return
        report, outcome = result
        self._apply_report(report)
        self._log(outcome.message, "ok" if outcome.applied else "err")
        messagebox.showinfo("Hosts 优化结果", outcome.message, parent=self.root)

    def _toggle_proxy(self) -> None:
        status = self.controller.status()
        if status.running:
            self._proxy_action("stop")
        else:
            self._proxy_action("start")

    def _proxy_action(self, action: str) -> None:
        if action == "start" and not self._require_admin():
            return
        if action == "start":
            def work(log):
                log("启动本地连接器并设置按域名分流的 PAC…")
                return self.controller.start()
        else:
            def work(log):
                log("停止本地连接器并恢复系统代理/Git 代理…")
                return self.controller.stop()
        self._run_task("智能代理", work, self._proxy_done)

    def _proxy_done(self, result) -> None:
        if not result:
            self._refresh_proxy_status()
            return
        ok, message = result
        self._log(message, "ok" if ok else "err")
        self._refresh_proxy_status()
        if not ok:
            messagebox.showwarning("智能代理", message, parent=self.root)

    def _refresh_proxy_status(self) -> None:
        try:
            status = self.controller.status()
        except Exception:
            status = None
        running = bool(status and status.running)
        self.proxy_badge.configure(
            text="智能代理运行中" if running else "代理未运行",
            fg=GREEN if running else MUTED,
            bg="#132a1b" if running else CARD2,
        )
        self.side_proxy.configure(
            text="代理: " + (f"运行中 127.0.0.1:{status.proxy_port}" if running else "未运行"),
            fg=GREEN if running else MUTED,
        )
        self.proxy_btn.configure(text="停止智能代理" if running else "开启智能代理")
        if running:
            self.proxy_running_label.configure(text="● 运行中", fg=GREEN)
            stats = status.stats or {}
            self.proxy_detail_label.configure(
                text=f"监听 127.0.0.1:{status.proxy_port}    PAC: {status.pac_url or '-'}    Git 代理: {status.git_proxy or '-'}",
                fg=MUTED,
            )
            self.proxy_stats_label.configure(
                text=f"连接 {stats.get('connections', 0)} 次，成功 {stats.get('successes', 0)} 次，失败 {stats.get('failures', 0)} 次，"
                f"当前活跃 {stats.get('active', 0)}，上行 {stats.get('bytes_up', 0) / 1024:.1f} KB，下行 {stats.get('bytes_down', 0) / 1024:.1f} KB"
                + (f"\n最近错误: {stats.get('last_error')}" if stats.get("last_error") else "")
            )
        else:
            self.proxy_running_label.configure(text="○ 未运行", fg=MUTED)
            self.proxy_detail_label.configure(text="智能代理未启动。开启后，仅 GitHub 流量会经过本地连接器。", fg=MUTED)
            self.proxy_stats_label.configure(text="")

    def _copy_pac(self) -> None:
        status = self.controller.status()
        if status.pac_url:
            self.root.clipboard_clear()
            self.root.clipboard_append(status.pac_url)
            self._log("已复制 PAC 地址到剪贴板", "ok")
        else:
            messagebox.showinfo("PAC 地址", "智能代理尚未启动，没有 PAC 地址。", parent=self.root)

    def _refresh_git(self, quick: bool = False) -> None:
        def work(log):
            info = git_tools.inspect_git(test_ssh=not quick)
            return info

        self._run_task("读取 Git 信息", work, self._apply_git_info)

    def _apply_git_info(self, info) -> None:
        if info is None:
            return
        self.git_info_label.configure(
            text=f"Git: {info.version or '未安装'}   用户: {info.user_name or '-'}   "
            f"GitHub 专用代理: {info.proxy or '未配置'}\nSSH 443 配置: {'已启用' if info.ssh_ready else '未启用'}   "
            f"SSH 测试: {info.ssh_detail or '未测试'}"
        )
        if info.risky_rewrites:
            details = "\n".join(f"  {item.get('base')} → {item.get('instead_of')}" for item in info.risky_rewrites)
            self.git_rewrite_label.configure(text=f"检测到第三方 Git URL 改写：\n{details}", fg=RED)
        else:
            self.git_rewrite_label.configure(text="未检测到第三方 Git URL 改写。", fg=MUTED)
        self.side_git.configure(
            text=f"Git: {info.version or '未安装'}\nSSH 443: {'已启用' if info.ssh_ready else '未启用'}",
            fg=FG if info.version else RED,
        )
        self.git_output.configure(state="normal")
        self.git_output.delete("1.0", "end")
        self.git_output.insert("end", f"版本: {info.version or '未安装'}\n")
        self.git_output.insert("end", f"用户: {info.user_name or '-'} <{info.user_email or '-'}>\n")
        self.git_output.insert("end", f"GitHub 专用代理: {info.proxy or '未配置'}\n")
        self.git_output.insert("end", f"SSH 443 配置块: {'已存在' if info.ssh_ready else '不存在'}\n")
        self.git_output.insert("end", f"SSH 测试: {info.ssh_detail or '未测试'}\n")
        if info.risky_rewrites:
            self.git_output.insert("end", "\n第三方 URL 改写:\n")
            for item in info.risky_rewrites:
                self.git_output.insert("end", f"  {item['base']}  <=  {item['instead_of']}\n")
        self.git_output.configure(state="disabled")

    def _test_ssh(self) -> None:
        def work(log):
            log("测试 ssh.github.com:443（只验证连通性，不会修改配置）…")
            return git_tools.test_ssh_443()

        self._run_task("测试 SSH 443", work, lambda result: self._ssh_result(result, "SSH 443 测试"))

    def _ssh_result(self, result, title: str) -> None:
        if not result:
            return
        ok, detail = result
        self._log(detail, "ok" if ok else "err")
        self.git_output.configure(state="normal")
        self.git_output.insert("end", f"\n[{title}] {'成功' if ok else '失败'}\n{detail}\n")
        self.git_output.see("end")
        self.git_output.configure(state="disabled")
        messagebox.showinfo(title, detail[:1500], parent=self.root)

    def _apply_ssh(self) -> None:
        if not messagebox.askyesno(
            "启用 SSH 443",
            "将写入 ~/.ssh/config 的 GitHubFix 配置块，并把 git@github.com: 重写到 github-443 别名。\n"
            "这不会修改 HTTPS 配置。继续？",
            parent=self.root,
        ):
            return

        def work(log):
            ok, detail = git_tools.test_ssh_443()
            log(("SSH 443 测试成功: " if ok else "SSH 443 测试失败，未写入配置: ") + detail)
            if not ok:
                return False, "SSH 443 连接测试失败，未修改 ~/.ssh/config。请检查网络后重试。\n\n" + detail
            result = git_tools.apply_ssh_443()
            return result

        self._run_task("启用 SSH 443", work, lambda result: self._ssh_result(result, "启用 SSH 443"))

    def _remove_ssh(self) -> None:
        def work(log):
            return git_tools.remove_ssh_443()

        self._run_task("移除 SSH 443", work, lambda result: self._ssh_result(result, "移除 SSH 443"))

    def _clean_rewrites(self) -> None:
        if not self.report:
            info = git_tools.inspect_git(test_ssh=False)
            risky = info.risky_rewrites
        else:
            risky = (self.report.git or {}).get("risky_rewrites") or []
        if not risky:
            messagebox.showinfo("URL 改写", "没有发现第三方 GitHub URL 改写。", parent=self.root)
            return
        detail = "\n".join(f"{item['base']}  <=  {item['instead_of']}" for item in risky)
        if not messagebox.askyesno(
            "清理第三方 Git 改写",
            "将删除以下全局 Git URL 改写，并先备份 .gitconfig：\n\n" + detail + "\n\n继续？",
            parent=self.root,
        ):
            return

        def work(log):
            return git_tools.clean_github_url_rewrites()

        self._run_task("清理 Git URL 改写", work, self._clean_done)

    def _clean_done(self, result) -> None:
        if not result:
            return
        ok, message = result
        self._log(message, "ok" if ok else "err")
        messagebox.showinfo("清理结果", message, parent=self.root)
        self._refresh_git(quick=True)

    def _restore_all(self) -> None:
        if not messagebox.askyesno(
            "恢复系统设置",
            "将停止智能代理、恢复 hosts、移除 GitHubFix 的 SSH 443 配置和 Git 代理。\n"
            "不会删除你原有的 .gitconfig 其他内容或第三方改写（可单独清理）。\n\n继续？",
            parent=self.root,
        ):
            return
        if not self._require_admin():
            return

        def work(log):
            messages = []
            ok_proxy, msg_proxy = self.controller.stop()
            messages.append(msg_proxy)
            ok_hosts, msg_hosts = restore(progress=log)
            messages.append(msg_hosts)
            ok_ssh, msg_ssh = git_tools.remove_ssh_443()
            messages.append(msg_ssh)
            return "\n".join(messages), ok_proxy and ok_hosts and ok_ssh

        self._run_task("恢复系统设置", work, self._restore_done)

    def _restore_done(self, result) -> None:
        if not result:
            return
        message, ok = result
        self._log(message, "ok" if ok else "err")
        messagebox.showinfo("恢复结果", message, parent=self.root)
        self.report = None
        self._refresh_proxy_status()
        self._refresh_diagnose(include_mirrors=False)

    def _probe_mirrors(self) -> None:
        def work(log):
            return probe_mirrors(progress=log)

        self._run_task("公共镜像测速", work, self._apply_mirrors)

    def _apply_mirrors(self, results) -> None:
        self._mirrors = list(results or [])
        for item in self.mirror_tree.get_children():
            self.mirror_tree.delete(item)
        for mirror in self._mirrors:
            self.mirror_tree.insert(
                "",
                "end",
                values=(mirror.name, "可用" if mirror.ok else "不可用", f"{mirror.latency_ms} ms" if mirror.latency_ms else "-", mirror.note),
                tags=("ok" if mirror.ok else "bad",),
            )
        ok = [item for item in self._mirrors if item.ok]
        self.mirror_hint.configure(
            text=(f"最快可用镜像: {ok[0].name} ({ok[0].latency_ms} ms)" if ok else "当前没有可用公共镜像。")
            + "  第三方镜像仅用于公开内容，请勿携带凭据。"
        )

    def _copy_clone(self) -> None:
        url = self.repo_entry.get().strip()
        if not url.startswith("https://github.com/"):
            messagebox.showwarning("URL 无效", "请输入 https://github.com/ 开头的公开仓库 URL。", parent=self.root)
            return
        ok_mirrors = [item for item in self._mirrors if item.ok]
        if not ok_mirrors:
            messagebox.showinfo("请先测速", "请先点击“测速”，选择一个可用镜像。", parent=self.root)
            return
        command = clone_command(url, ok_mirrors[0].base)
        self.root.clipboard_clear()
        self.root.clipboard_append(command)
        self._log("已复制: " + command, "ok")
        messagebox.showinfo("已复制", "加速克隆命令已复制到剪贴板：\n\n" + command, parent=self.root)

    def _export_report(self) -> None:
        if not self.report:
            messagebox.showinfo("导出报告", "请先完成一次网络诊断。", parent=self.root)
            return
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="保存诊断报告",
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("所有文件", "*.*")],
            initialfile="githubfix-report.md",
        )
        if not path:
            return
        try:
            target = save_report(self.report, path)
            self._log(f"报告已保存: {target}", "ok")
            messagebox.showinfo("导出成功", str(target), parent=self.root)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)

    def _show_candidates(self) -> None:
        selection = self.domain_tree.selection()
        if not selection:
            messagebox.showinfo("候选 IP", "请先在表格中选择一个域名。", parent=self.root)
            return
        values = self.domain_tree.item(selection[0], "values")
        host = values[0] if values else ""
        data = self._domain_results.get(host, {})
        candidates = data.get("candidates", [])
        window = tk.Toplevel(self.root)
        window.title(f"{host} 候选 IP")
        window.geometry("900x480")
        window.configure(bg=BG)
        ttk.Label(
            window,
            text=f"{host} 的全部候选（按稳定性和延迟排序）",
            style="Title.TLabel",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w", padx=16, pady=(14, 6))
        columns = ("ip", "source", "status", "stability", "latency", "http", "error")
        tree = ttk.Treeview(window, columns=columns, show="headings")
        for key, text, width in (
            ("ip", "IP", 130),
            ("source", "来源", 130),
            ("status", "结果", 70),
            ("stability", "稳定性", 70),
            ("latency", "延迟", 80),
            ("http", "HTTP", 60),
            ("error", "错误", 280),
        ):
            tree.heading(key, text=text)
            tree.column(key, width=width, anchor="w" if key in ("source", "error") else "center")
        tree.tag_configure("ok", foreground=GREEN)
        tree.tag_configure("bad", foreground=RED)
        tree.tag_configure("warn", foreground=YELLOW)
        for item in candidates:
            stability = item.get("stability", 0)
            tag = "ok" if item.get("ok") and stability >= 0.99 else ("warn" if item.get("ok") else "bad")
            if item.get("tcp_ms") is not None:
                total_ms = sum(float(item.get(key) or 0) for key in ("tcp_ms", "tls_ms", "http_ms"))
                latency = f"{total_ms:.0f} ms"
            else:
                latency = "-"
            tree.insert(
                "",
                "end",
                values=(
                    item.get("ip", ""),
                    item.get("source", ""),
                    "可用" if item.get("ok") else "失败",
                    f"{stability * 100:.0f}%" if item.get("attempts") else "-",
                    latency,
                    item.get("status", "-"),
                    item.get("error", ""),
                ),
                tags=(tag,),
            )
        tree.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    # ---------- helpers ----------
    def _recent_report(self):
        if self.report and (time.monotonic() - self._report_monotonic) < 180:
            return self.report
        return None

    def _require_admin(self) -> bool:
        if is_admin():
            return True
        if messagebox.askyesno(
            "需要管理员权限",
            "写入 hosts 需要管理员权限。\n\n是否以管理员身份重新启动 GitHubFix？",
            parent=self.root,
        ):
            ok, message = relaunch_as_admin(["--gui"])
            if ok:
                self._closing = True
                self.root.destroy()
            else:
                messagebox.showerror("提升权限失败", message, parent=self.root)
        return False

    def _on_close(self) -> None:
        if self._busy and not messagebox.askyesno("任务进行中", "仍有操作在进行，确定退出吗？", parent=self.root):
            return
        self._closing = True
        try:
            status = self.controller.status()
            if status.running:
                self._log("正在停止智能代理并恢复系统设置…", "warn")
                self.controller.stop()
        except Exception:
            pass
        self.root.destroy()

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from github_fix.core import hosts, service
from github_fix.core.models import DOMAINS, DiagnosisReport, ProbeResult


def make_report(ips=None):
    ips = ips or {}
    best = {}
    for spec in DOMAINS:
        ip = ips.get(spec.host, "20.205.243.166")
        best[spec.host] = ProbeResult(
            host=spec.host,
            ip=ip,
            ok=True,
            status=200,
            tcp_ms=20,
            tls_ms=30,
            http_ms=40,
            stability=1.0,
        )
    return DiagnosisReport(
        generated_at="2026-01-01T00:00:00+08:00",
        admin=True,
        hosts_path="",
        hosts_managed=False,
        hosts_legacy=True,
        best=best,
    )


def direct_result(entries, ok_hosts=None):
    """Build a _verify_direct() shaped result where ok_hosts pass."""
    ok_hosts = ok_hosts if ok_hosts is not None else set(entries)
    return {
        host: {
            "ip": ip,
            "direct": host in ok_hosts,
            "direct_error": "" if host in ok_hosts else "连接超时",
            "latency_ms": 100 if host in ok_hosts else None,
            "status": 200 if host in ok_hosts else None,
            "attempts": 1,
        }
        for host, ip in entries.items()
    }


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.hosts = self.root / "hosts"
        self.hosts.write_text(
            "# user hosts\n127.0.0.1 localhost\n# === GitHub Fix START ===\n1.2.3.4 github.com\n# === GitHub Fix END ===\n",
            encoding="utf-8",
        )
        self.env = mock.patch.dict(
            os.environ,
            {
                "GITHUBFIX_DATA_DIR": str(self.root / "data"),
                "GITHUBFIX_HOSTS_PATH": str(self.hosts),
            },
        )
        self.env.start()
        self.patches = [
            mock.patch.object(hosts, "is_admin", return_value=True),
            mock.patch.object(service, "is_admin", return_value=True),
            mock.patch.object(hosts, "flush_dns", return_value=(True, "")),
            mock.patch.object(service, "flush_dns", return_value=(True, "")),
            mock.patch.object(service, "_verify_dns", return_value={}),
            mock.patch.object(service, "_sleep", return_value=None),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def _patch_direct(self, ok_hosts=None, resolver=None):
        return mock.patch.object(
            service,
            "_verify_direct",
            side_effect=resolver or (lambda entries, **kwargs: direct_result(entries, ok_hosts)),
        )

    def test_repair_writes_best_and_keeps_backup(self):
        with self._patch_direct():
            outcome = service.repair(make_report({"github.com": "20.205.243.166"}), auto_rollback=True)
        self.assertTrue(outcome.applied)
        self.assertFalse(outcome.rolled_back)
        self.assertTrue(Path(outcome.backup).exists())
        snapshot = hosts.read_hosts(self.hosts)
        self.assertEqual(snapshot.entries["github.com"], ["20.205.243.166"])
        self.assertIn("127.0.0.1 localhost", snapshot.text)

    def test_unreachable_extra_hosts_are_dropped_not_rolled_back(self):
        """A dead non-essential domain must not undo a working github.com entry."""
        dead = {"raw.githubusercontent.com", "objects.githubusercontent.com"}
        with self._patch_direct(ok_hosts=set(h.host for h in DOMAINS) - dead):
            outcome = service.repair(make_report(), auto_rollback=True)
        self.assertTrue(outcome.applied, outcome.message)
        self.assertFalse(outcome.rolled_back)
        snapshot = hosts.read_hosts(self.hosts)
        self.assertIn("github.com", snapshot.entries)
        self.assertNotIn("raw.githubusercontent.com", snapshot.entries)
        self.assertTrue(set(dead).issubset(set(outcome.skipped)))

    def test_auto_rollback_when_github_unreachable(self):
        with self._patch_direct(ok_hosts=set()):
            outcome = service.repair(make_report(), auto_rollback=True)
        self.assertFalse(outcome.applied)
        self.assertTrue(outcome.rolled_back)
        text = self.hosts.read_text(encoding="utf-8")
        self.assertIn("# === GitHub Fix START ===", text)
        self.assertNotIn(hosts.OUR_START, text)

    def test_github_falls_back_to_alternate_candidate(self):
        report = make_report()
        report.domains["github.com"] = {
            "candidates": [
                {"ip": "140.82.113.3", "ok": True},
                {"ip": "20.205.243.166", "ok": True},
            ],
            "best": None,
            "resolved_by": {},
        }
        calls = {"n": 0}

        def resolver(entries, **kwargs):
            calls["n"] += 1
            primary_ok = entries.get("github.com") == "140.82.113.3"
            return direct_result(entries, set(entries) if primary_ok else set(entries) - {"github.com"})

        with mock.patch.object(service, "probe_ip", return_value=ProbeResult(host="github.com", ip="140.82.113.3", ok=True)):
            with mock.patch.object(service, "_verify_direct", side_effect=resolver):
                outcome = service.repair(report, auto_rollback=True)

        self.assertTrue(outcome.applied, outcome.message)
        self.assertEqual(outcome.entries["github.com"], "140.82.113.3")

    def test_restore_removes_managed_blocks(self):
        with self._patch_direct():
            service.repair(make_report(), auto_rollback=False)
        ok, message = service.restore()
        self.assertTrue(ok)
        text = self.hosts.read_text(encoding="utf-8")
        self.assertNotIn(hosts.OUR_START, text)
        self.assertNotIn(hosts.LEGACY_START, text)
        self.assertIn("127.0.0.1 localhost", text)


if __name__ == "__main__":
    unittest.main()

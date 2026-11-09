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
        best[spec.host] = ProbeResult(host=spec.host, ip=ip, ok=True, status=200, tcp_ms=20, tls_ms=30, http_ms=40, stability=1.0)
    return DiagnosisReport(
        generated_at="2026-01-01T00:00:00+08:00",
        admin=True,
        hosts_path="",
        hosts_managed=False,
        hosts_legacy=True,
        best=best,
    )


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.hosts = self.root / "hosts"
        self.hosts.write_text("# user hosts\n127.0.0.1 localhost\n# === GitHub Fix START ===\n1.2.3.4 github.com\n# === GitHub Fix END ===\n", encoding="utf-8")
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
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    @mock.patch.object(service.time, "sleep", return_value=None)
    def test_repair_writes_best_and_keeps_backup(self, _sleep):
        fake_verify = {
            "github.com": {"ok": True, "status": 200, "error": ""},
            "api.github.com": {"ok": True, "status": 200, "error": ""},
            "raw.githubusercontent.com": {"ok": True, "status": 200, "error": ""},
            "github.githubassets.com": {"ok": True, "status": 404, "error": ""},
        }
        with mock.patch.object(service, "_verify", return_value=fake_verify):
            outcome = service.repair(make_report({"github.com": "20.205.243.166"}), auto_rollback=True)
        self.assertTrue(outcome.applied)
        self.assertFalse(outcome.rolled_back)
        self.assertTrue(Path(outcome.backup).exists())
        snapshot = hosts.read_hosts(self.hosts)
        self.assertEqual(snapshot.entries["github.com"], ["20.205.243.166"])
        self.assertIn("127.0.0.1 localhost", snapshot.text)

    @mock.patch.object(service.time, "sleep", return_value=None)
    def test_auto_rollback_restores_original(self, _sleep):
        bad_verify = {
            "github.com": {"ok": False, "status": None, "error": "timeout"},
            "api.github.com": {"ok": False, "status": None, "error": "timeout"},
            "raw.githubusercontent.com": {"ok": False, "status": None, "error": "timeout"},
            "github.githubassets.com": {"ok": False, "status": None, "error": "timeout"},
        }
        with mock.patch.object(service, "_verify", return_value=bad_verify):
            outcome = service.repair(make_report(), auto_rollback=True)
        self.assertFalse(outcome.applied)
        self.assertTrue(outcome.rolled_back)
        text = self.hosts.read_text(encoding="utf-8")
        self.assertIn("# === GitHub Fix START ===", text)

    def test_restore_removes_managed_blocks(self):
        with mock.patch.object(service, "_verify", return_value={"github.com": {"ok": True}}):
            service.repair(make_report(), auto_rollback=False)
        ok, message = service.restore()
        self.assertTrue(ok)
        text = self.hosts.read_text(encoding="utf-8")
        self.assertNotIn(hosts.OUR_START, text)
        self.assertNotIn(hosts.LEGACY_START, text)
        self.assertIn("127.0.0.1 localhost", text)


if __name__ == "__main__":
    unittest.main()

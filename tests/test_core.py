from __future__ import annotations

import unittest

from github_fix.core import git_tools, mirrors, probe, proxy_server
from github_fix.core.sources import build_candidates, parse_hosts_text


class CoreTests(unittest.TestCase):
    def test_accelerate_url(self):
        self.assertEqual(
            mirrors.accelerate_url("https://github.com/a/b", "https://ghfast.top"),
            "https://ghfast.top/https://github.com/a/b",
        )
        with self.assertRaises(ValueError):
            mirrors.accelerate_url("https://example.com/x", "https://ghfast.top")

    def test_parse_hosts_text(self):
        parsed = parse_hosts_text("1.1.1.1 github.com api.github.com\nnot-an-ip github.com", "test")
        self.assertEqual(parsed["github.com"]["1.1.1.1"], ["test"])
        self.assertIn("api.github.com", parsed)

    def test_build_candidates_and_normalization(self):
        class FakeResolution:
            by_source = {"Ali": ["1.1.1.1"], "系统解析": ["2.2.2.2"]}

        from github_fix.core.sources import FeedData

        cands = build_candidates({"github.com": FakeResolution()}, FeedData(), {"github.com": ["3.3.3.3"]})
        self.assertIn("1.1.1.1", cands["github.com"])
        self.assertIn("3.3.3.3", cands["github.com"])
        normalized = probe._normalize_candidates(cands["github.com"])
        self.assertIn("3.3.3.3", normalized["当前 hosts"])
        self.assertIn("1.1.1.1", normalized["Ali"])

    def test_pac_rules(self):
        pac = proxy_server.pac_script(17890)
        self.assertIn("127.0.0.1:17890", pac)
        self.assertIn('host == "github.com"', pac)
        self.assertTrue(proxy_server.is_github_host("api.github.com"))
        self.assertTrue(proxy_server.is_github_host("raw.githubusercontent.com"))
        self.assertFalse(proxy_server.is_github_host("github.com.evil.example"))
        self.assertFalse(proxy_server.is_github_host("example.com"))

    def test_ssh_config_block_roundtrip(self):
        original = "Host example\n    HostName example.com\n"
        block = git_tools.build_ssh_block()
        combined = original + block
        self.assertTrue(git_tools.has_managed_ssh_block(combined))
        stripped = git_tools.strip_ssh_block(combined)
        self.assertIn("Host example", stripped)
        self.assertNotIn(git_tools.SSH_START, stripped)
        self.assertEqual(git_tools.strip_ssh_block(block).strip(), "")


if __name__ == "__main__":
    unittest.main()

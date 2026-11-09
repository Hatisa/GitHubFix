from __future__ import annotations

import os
import unittest

from github_fix.core.resolver import resolve
from github_fix.core.sources import fetch_feeds


@unittest.skipUnless(os.environ.get("GITHUBFIX_LIVE_TESTS") == "1", "set GITHUBFIX_LIVE_TESTS=1 for network tests")
class LiveNetworkTests(unittest.TestCase):
    def test_resolve_github(self):
        result = resolve("github.com", timeout=6)
        self.assertTrue(result.ips)

    def test_fetch_feeds(self):
        feeds = fetch_feeds(timeout=8)
        self.assertTrue(feeds.entries or feeds.errors)

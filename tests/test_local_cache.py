from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from binance_analyzer.runtime.local_cache import LocalCacheManager


class LocalCacheManagerTests(unittest.TestCase):
    def test_strips_content_encoding_on_save_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = LocalCacheManager(Path(tmpdir))
            url = "https://bin.bnbstatic.com/static/main.abc.js"
            manager.save_to_cache(
                url,
                "script",
                b"console.log(1)",
                {"Content-Type": "text/javascript", "content-encoding": "br", "content-length": "14"},
            )
            cached = manager.get_cached(url, "script")
            self.assertIsNotNone(cached)
            self.assertEqual(cached["body"], b"console.log(1)")
            self.assertEqual(cached["headers"].get("content-type"), "text/javascript")
            self.assertNotIn("content-encoding", {key.lower() for key in cached["headers"]})
            self.assertNotIn("content-length", {key.lower() for key in cached["headers"]})

    def test_skips_empty_bodies_and_old_zero_size_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = LocalCacheManager(Path(tmpdir))
            url = "https://bin.bnbstatic.com/static/empty.js"
            manager.save_to_cache(url, "script", b"", {"content-type": "text/javascript"})
            self.assertIsNone(manager.get_cached(url, "script"))

    def test_does_not_cache_api_or_captcha(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = LocalCacheManager(Path(tmpdir))
            self.assertFalse(manager._is_cacheable("https://www.binance.com/bapi/accounts/x", "fetch"))
            self.assertFalse(manager._is_cacheable("https://bin.bnbstatic.com/static/captcha.js", "script"))
            self.assertTrue(manager._is_cacheable("https://bin.bnbstatic.com/static/app.js", "script"))


if __name__ == "__main__":
    unittest.main()

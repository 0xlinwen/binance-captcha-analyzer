from __future__ import annotations

import unittest

from binance_analyzer.fingerprint import (
    CHROME_VERSIONS,
    FINGERPRINT_MODE_NATIVE,
    FINGERPRINT_MODE_SPOOFED,
    describe_fingerprint,
    generate_fingerprint,
    is_native_fingerprint,
)


class FingerprintTests(unittest.TestCase):
    def test_default_mode_is_native_and_does_not_spoof(self) -> None:
        fingerprint = generate_fingerprint()

        self.assertTrue(is_native_fingerprint(fingerprint))
        self.assertEqual(fingerprint["mode"], FINGERPRINT_MODE_NATIVE)
        self.assertEqual(fingerprint["user_agent"], "")
        self.assertEqual(fingerprint["languages"], [])
        self.assertEqual(fingerprint["screen_width"], 0)
        self.assertIn("native", describe_fingerprint(fingerprint))

    def test_spoofed_mode_uses_version_pool_and_mac_profile(self) -> None:
        fingerprint = generate_fingerprint(use_real_profile=True, mode=FINGERPRINT_MODE_SPOOFED)

        self.assertFalse(is_native_fingerprint(fingerprint))
        self.assertEqual(fingerprint["mode"], FINGERPRINT_MODE_SPOOFED)
        self.assertIn(fingerprint["chrome_version"], CHROME_VERSIONS)
        self.assertIn(fingerprint["chrome_version"], fingerprint["user_agent"])
        self.assertEqual(fingerprint["platform"], "MacIntel")
        self.assertGreater(fingerprint["screen_width"], 0)
        self.assertIn("UA=", describe_fingerprint(fingerprint))

    def test_invalid_mode_fails_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "native/spoofed"):
            generate_fingerprint(mode="random")


if __name__ == "__main__":
    unittest.main()

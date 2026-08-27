from __future__ import annotations

import unittest

from binance_analyzer.credential_export import export_credentials


class _Context:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self):
        return self._cookies


class _Page:
    def __init__(self, cookies):
        self.context = _Context(cookies)


class CredentialExportTests(unittest.TestCase):
    def test_export_records_current_time_without_reading_cookie_expiry(self) -> None:
        credentials = export_credentials(
            _Page([{"domain": ".binance.com", "name": "session", "value": "value", "expires": -1}])
        )

        self.assertRegex(credentials.credential_exported_at, r"^20\d{2}-")

    def test_export_time_is_present_for_persistent_cookies(self) -> None:
        credentials = export_credentials(
            _Page([
                {"domain": ".binance.com", "name": "session", "value": "value", "expires": -1},
                {"domain": ".binance.com", "name": "persistent", "value": "value", "expires": 1_800_000_000},
                {"domain": ".binance.com", "name": "later", "value": "value", "expires": 1_900_000_000},
            ])
        )

        self.assertRegex(credentials.credential_exported_at, r"^20\d{2}-")


if __name__ == "__main__":
    unittest.main()

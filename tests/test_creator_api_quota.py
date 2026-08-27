from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from binance_analyzer.integrations.creator_api_quota import (
    acquire_creator_api_slot,
    initialize_creator_api_quota,
    release_creator_api_slot,
)


class CreatorApiQuotaTests(unittest.TestCase):
    def test_failed_extraction_releases_slot_for_next_success(self) -> None:
        config = {
            "output_file": "data/results/registered_accounts.json",
            "creator_api": {"enabled": True, "max_accounts": 1, "slot_wait_timeout_sec": 1},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            initialize_creator_api_quota(base_dir, config)
            first = acquire_creator_api_slot(base_dir, config)
            release_creator_api_slot(base_dir, config, first, completed=False)
            second = acquire_creator_api_slot(base_dir, config)
            release_creator_api_slot(base_dir, config, second, completed=True)
            self.assertIsNone(acquire_creator_api_slot(base_dir, config))

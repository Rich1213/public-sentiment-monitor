import os
import unittest
from unittest.mock import patch

from src.collectors.dcard_collector import DcardCollector


class DcardCollectorConfigTest(unittest.TestCase):
    def test_prefers_residential_proxy_over_scraperapi_for_dcard(self):
        with patch.dict(
            os.environ,
            {
                "SCRAPERAPI_KEY": "scraper-key",
                "DCARD_PROXY_URL": "http://user:pass@proxy.example:1000",
            },
            clear=False,
        ):
            with patch.object(DcardCollector, "_init_session", return_value=object()):
                collector = DcardCollector("7-ELEVEN")

        self.assertEqual(collector._bypass_mode, "proxy")

    def test_uses_scraperapi_when_proxy_is_absent(self):
        with patch.dict(
            os.environ,
            {
                "SCRAPERAPI_KEY": "scraper-key",
                "DCARD_PROXY_URL": "",
            },
            clear=False,
        ):
            with patch.object(DcardCollector, "_init_session", return_value=object()):
                collector = DcardCollector("7-ELEVEN")

        self.assertEqual(collector._bypass_mode, "scraperapi")

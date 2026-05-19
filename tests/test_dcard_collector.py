import os
import unittest
from unittest.mock import patch
from types import SimpleNamespace

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

    def test_proxy_mode_uses_residential_proxy_before_scraperapi(self):
        with patch.dict(
            os.environ,
            {
                "SCRAPERAPI_KEY": "scraper-key",
                "DCARD_PROXY_URL": "http://user:pass@proxy.example:1000",
            },
            clear=False,
        ):
            collector = DcardCollector("7-ELEVEN")

        collector.session = SimpleNamespace(
            get=lambda *args, **kwargs: SimpleNamespace(status_code=200)
        )
        with patch.object(collector, "_scraperapi_get_with_fallback") as scraper_call:
            resp = collector._get("https://www.dcard.tw/search/posts", params={"query": "7-ELEVEN"})

        self.assertEqual(resp.status_code, 200)
        scraper_call.assert_not_called()

    def test_proxy_mode_falls_back_to_scraperapi_after_proxy_403(self):
        with patch.dict(
            os.environ,
            {
                "SCRAPERAPI_KEY": "scraper-key",
                "DCARD_PROXY_URL": "http://user:pass@proxy.example:1000",
            },
            clear=False,
        ):
            collector = DcardCollector("7-ELEVEN")

        collector.session = SimpleNamespace(
            get=lambda *args, **kwargs: SimpleNamespace(status_code=403)
        )
        with patch.object(
            collector,
            "_scraperapi_get_with_fallback",
            return_value=SimpleNamespace(status_code=200),
        ) as scraper_call:
            resp = collector._get("https://www.dcard.tw/search/posts", params={"query": "7-ELEVEN"})

        self.assertEqual(resp.status_code, 200)
        scraper_call.assert_called_once()

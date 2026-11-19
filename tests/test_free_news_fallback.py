# -*- coding: utf-8 -*-
"""Tests for the keyless news fallback (Google News RSS + yfinance)."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

from src.config import free_news_fallback_enabled
from src.data.stock_mapping import STOCK_ENGLISH_NAME_MAP, STOCK_NAME_MAP
from src.search_service import (
    FreeNewsSearchProvider,
    SearchResponse,
    SearchResult,
    SearchService,
)

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>SpaceX - Google News</title>
  <item>
    <title>SpaceX&#8217;s December Lock-Up Frees Another 17% of Its Shares - Motley Fool</title>
    <link>https://news.google.com/rss/articles/abc</link>
    <pubDate>Wed, 18 Nov 2026 21:00:00 GMT</pubDate>
    <description>&lt;a href="https://example.com/a"&gt;SpaceX&amp;#8217;s December Lock-Up&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;Motley Fool&lt;/font&gt;</description>
    <source url="https://www.fool.com">Motley Fool</source>
  </item>
  <item>
    <title>No date headline - Reuters</title>
    <link>https://news.google.com/rss/articles/def</link>
    <description>No date headline</description>
    <source url="https://www.reuters.com">Reuters</source>
  </item>
</channel></rss>
"""


def _today_iso(days_ago: int = 0) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=days_ago)).isoformat()


class FreeNewsSearchProviderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = FreeNewsSearchProvider()

    def test_provider_is_always_available_without_keys(self) -> None:
        self.assertTrue(self.provider.is_available)

    def test_google_news_rss_is_parsed_into_search_results(self) -> None:
        response = MagicMock()
        response.content = RSS_XML.encode("utf-8")
        response.raise_for_status = MagicMock()
        with patch("src.search_service.requests.get", return_value=response), patch.object(
            self.provider, "_fetch_yfinance_news", return_value=[]
        ):
            result = self.provider.search("SpaceX SPCX stock latest news", max_results=5, days=7)

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "GoogleNewsRSS")
        self.assertEqual(len(result.results), 2)
        first = result.results[0]
        self.assertEqual(first.title, "SpaceX’s December Lock-Up Frees Another 17% of Its Shares")
        self.assertEqual(first.source, "Motley Fool")
        self.assertEqual(first.published_date, "2026-11-18")
        self.assertEqual(first.url, "https://news.google.com/rss/articles/abc")
        self.assertTrue(first.retrieved_at)

    def test_yfinance_news_supports_current_and_legacy_shapes(self) -> None:
        entries = [
            {
                "content": {
                    "title": "SpaceX stock rises on lock-up news",
                    "summary": "Fabrizio <b>summary</b>",
                    "pubDate": f"{_today_iso()}T10:00:00Z",
                    "canonicalUrl": {"url": "https://finance.yahoo.com/a"},
                    "provider": {"displayName": "Yahoo Finance"},
                }
            },
            {
                "title": "Legacy shaped headline",
                "link": "https://finance.yahoo.com/b",
                "publisher": "Yahoo Finance Video",
                "providerPublishTime": int(datetime.now(timezone.utc).timestamp()) - 3600,
            },
        ]
        fake_ticker = MagicMock()
        fake_ticker.news = entries
        fake_yf = MagicMock()
        fake_yf.Ticker.return_value = fake_ticker
        with patch.dict(sys.modules, {"yfinance": fake_yf}):
            items = self.provider._fetch_yfinance_news(["SPCX"], 7)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].source, "Yahoo Finance")
        self.assertEqual(items[0].snippet, "Fabrizio summary")
        self.assertEqual(items[1].source, "Yahoo Finance Video")
        self.assertEqual(items[1].url, "https://finance.yahoo.com/b")

    def test_google_news_failure_falls_back_to_yfinance(self) -> None:
        yf_item = SearchResult(
            title="SpaceX headline from Yahoo",
            snippet="snippet",
            url="https://finance.yahoo.com/x",
            source="Yahoo Finance",
            published_date=_today_iso(),
        )
        with patch("src.search_service.requests.get", side_effect=OSError("network down")), patch.object(
            self.provider, "_fetch_yfinance_news", return_value=[yf_item]
        ):
            result = self.provider.search("SpaceX SPCX stock", max_results=3, days=3)

        self.assertTrue(result.success)
        self.assertEqual([item.title for item in result.results], ["SpaceX headline from Yahoo"])

    def test_all_sources_failing_is_fail_open(self) -> None:
        with patch("src.search_service.requests.get", side_effect=OSError("network down")), patch.object(
            self.provider, "_fetch_yfinance_news", side_effect=RuntimeError("boom")
        ):
            result = self.provider.search("SpaceX SPCX stock", max_results=3, days=3)

        self.assertFalse(result.success)
        self.assertEqual(result.results, [])
        self.assertTrue(result.error_message)

    def test_ticker_resolution_skips_market_stopwords(self) -> None:
        self.assertEqual(self.provider._resolve_tickers("US stock market", None), [])
        self.assertEqual(self.provider._resolve_tickers("SpaceX SPCX stock latest news", None), ["SPCX"])
        self.assertEqual(
            self.provider._resolve_tickers("600519 股票 最新消息", []),
            [],
        )
        self.assertEqual(
            self.provider._resolve_tickers("SpaceX SPCX stock", ["SPCX.US"]),
            ["SPCX"],
        )


class SearchServiceFreeFallbackTestCase(unittest.TestCase):
    def test_fallback_registered_only_when_enabled(self) -> None:
        with patch.dict("os.environ", {"FREE_NEWS_FALLBACK_ENABLED": "true"}):
            service = SearchService()
            self.assertIsNotNone(service.free_news_provider)
            self.assertIs(service._providers[-1], service.free_news_provider)
            self.assertTrue(service.is_available)

        with patch.dict("os.environ", {"FREE_NEWS_FALLBACK_ENABLED": "false"}):
            service = SearchService()
            self.assertIsNone(service.free_news_provider)
            self.assertFalse(service.is_available)

    def test_search_stock_news_uses_free_source_without_paid_keys(self) -> None:
        with patch.dict("os.environ", {"FREE_NEWS_FALLBACK_ENABLED": "true"}):
            service = SearchService()

        today = _today_iso()
        fake_items = [
            SearchResult(
                title="SpaceX shares jump after Starship test",
                snippet="SpaceX shares jumped.",
                url="https://example.com/spacex-1",
                source="Reuters",
                published_date=today,
            ),
            SearchResult(
                title="SpaceX wins new launch contract",
                snippet="A new contract was announced.",
                url="https://example.com/spacex-2",
                source="Reuters",
                published_date=today,
            ),
        ]
        with patch.object(service.free_news_provider, "search", return_value=SearchResponse(
            query="q", results=fake_items, provider="GoogleNewsRSS", success=True
        )) as mocked:
            response = service.search_stock_news("SPCX", "SpaceX", max_results=2)

        self.assertTrue(response.success)
        self.assertEqual(response.provider, "GoogleNewsRSS")
        self.assertEqual([item.title for item in response.results], [item.title for item in fake_items])
        self.assertEqual(mocked.call_args.kwargs["tickers"], ["SPCX"])

    def test_config_reports_capability_from_free_fallback(self) -> None:
        with patch.dict("os.environ", {"FREE_NEWS_FALLBACK_ENABLED": "true"}):
            self.assertTrue(free_news_fallback_enabled())
        with patch.dict("os.environ", {"FREE_NEWS_FALLBACK_ENABLED": "off"}):
            self.assertFalse(free_news_fallback_enabled())


class SpcxMappingTestCase(unittest.TestCase):
    def test_spcx_has_local_name_and_english_alias(self) -> None:
        self.assertEqual(STOCK_NAME_MAP.get("SPCX"), "SpaceX")
        self.assertIn("SpaceX", STOCK_ENGLISH_NAME_MAP.get("SPCX", ()))


if __name__ == "__main__":
    unittest.main()

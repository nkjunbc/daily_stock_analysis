# -*- coding: utf-8 -*-
"""Tests for the SpaceX (private company) digest block in the daily report."""

import re
import sys
import types
import unittest
from unittest import mock

CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF]")

HEADLINES = [
    {"title": "SpaceX sets new launch cadence record", "link": "https://example.com/a", "source": "Reuters", "published": "2026-11-18"},
    {"title": "Starlink adds airline partners", "link": "https://example.com/b", "source": "CNBC", "published": "2026-11-18"},
]


def _import_module():
    try:
        from src import spacex_watch  # noqa: PLC0415
    except Exception:  # pragma: no cover - only when third-party deps are absent
        stub = types.ModuleType("src.report_language")
        stub.normalize_report_language = lambda value, default="zh": (
            "ko" if str(value or default).lower().startswith("ko") else ("en" if str(value or default).lower().startswith("en") else "zh")
        )
        sys.modules["src.report_language"] = stub
        from src import spacex_watch  # noqa: PLC0415
    return spacex_watch


sw = _import_module()


class SpaceXSectionTest(unittest.TestCase):
    def setUp(self) -> None:
        sw.reset_cache()

    def tearDown(self) -> None:
        sw.reset_cache()

    def test_render_uses_llm_bullets_and_headlines(self) -> None:
        with mock.patch.object(sw, "fetch_spacex_headlines", return_value=list(HEADLINES)), \
             mock.patch.object(sw, "summarize_headlines", return_value=["- 발사 빈도가 최고치를 기록했습니다.", "- 스타링크 항공사 제휴가 늘었습니다."]):
            section = sw.render_spacex_section("ko")

        self.assertTrue(section.startswith("## 🚀 SpaceX (비상장 동향)"))
        self.assertIn("발사 빈도가 최고치를 기록했습니다.", section)
        self.assertIn("[SpaceX sets new launch cadence record](https://example.com/a)", section)
        self.assertIn("Reuters · 2026-11-18", section)
        self.assertIsNone(CJK_RE.search(section))
        self.assertNotIn("：", section)

    def test_render_falls_back_to_headlines_when_summary_missing(self) -> None:
        with mock.patch.object(sw, "fetch_spacex_headlines", return_value=list(HEADLINES)), \
             mock.patch.object(sw, "summarize_headlines", return_value=[]):
            section = sw.render_spacex_section("ko")

        self.assertIn("## 🚀 SpaceX (비상장 동향)", section)
        self.assertIn("헤드라인만 정리했습니다", section)
        self.assertIn("Starlink adds airline partners", section)

    def test_render_returns_empty_when_feed_fails(self) -> None:
        with mock.patch.object(sw, "fetch_spacex_headlines", side_effect=OSError("network down")):
            self.assertEqual(sw.render_spacex_section("ko"), "")

    def test_render_result_is_cached(self) -> None:
        with mock.patch.object(sw, "fetch_spacex_headlines", return_value=list(HEADLINES)) as fetch, \
             mock.patch.object(sw, "summarize_headlines", return_value=["- 요약 한 줄입니다.", "- 두 번째 줄입니다."]) as summarize:
            sw.render_spacex_section("ko")
            sw.render_spacex_section("ko")

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(summarize.call_count, 1)

    def test_clean_bullets_normalizes_llm_output(self) -> None:
        raw = "```\n- 첫 번째 요약\n2) 두 번째 요약\n\n### 제목\n```"
        bullets = sw._clean_bullets(raw)
        self.assertEqual(bullets, ["- 첫 번째 요약", "- 두 번째 요약"])

    def test_clean_bullets_rejects_single_line(self) -> None:
        self.assertEqual(sw._clean_bullets("- only one line"), [])

    def test_headline_title_does_not_repeat_source(self) -> None:
        xml = (
            '<rss><channel><item><title>SpaceX wins contract - Reuters</title>'
            "<link>https://example.com/c</link><source>Reuters</source>"
            "<pubDate>Wed, 18 Nov 2026 14:03:41 GMT</pubDate></item></channel></rss>"
        )

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return xml.encode("utf-8")

        with mock.patch.object(sw.urllib.request, "urlopen", return_value=_Response()):
            items = sw.fetch_spacex_headlines()

        self.assertEqual(items[0]["title"], "SpaceX wins contract")
        self.assertEqual(items[0]["source"], "Reuters")
        self.assertEqual(items[0]["published"], "2026-11-18")


if __name__ == "__main__":
    unittest.main()

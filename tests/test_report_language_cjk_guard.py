# -*- coding: utf-8 -*-
"""Korean report bodies must not carry Chinese-character residue."""

import unittest

from src.report_language import sanitize_report_text


class KoreanReportCjkGuardTestCase(unittest.TestCase):
    def test_hanja_gloss_is_removed_from_korean_text(self) -> None:
        cleaned = sanitize_report_text(
            "AI 투자 열기 속 비(非)AI 기술주 및 반도체 종목별 순환매",
            "ko",
        )
        self.assertEqual(cleaned, "AI 투자 열기 속 비AI 기술주 및 반도체 종목별 순환매")

    def test_standalone_cjk_run_is_removed_from_korean_text(self) -> None:
        cleaned = sanitize_report_text("시장은 (上涨) 흐름을 보였습니다.", "ko")
        self.assertNotIn("上涨", cleaned)
        self.assertIn("흐름을 보였습니다", cleaned)

    def test_non_korean_reports_are_untouched(self) -> None:
        for language in ("zh", "en", None):
            text = "palantir 上涨 3% (非)AI"
            self.assertEqual(sanitize_report_text(text, language), text)

    def test_korean_text_without_cjk_is_unchanged(self) -> None:
        text = "**📢 최신 뉴스**: 반도체 주식의 2개월 연속 하락 가능성"
        self.assertEqual(sanitize_report_text(text, "ko"), text)


if __name__ == "__main__":
    unittest.main()

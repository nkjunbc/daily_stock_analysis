# -*- coding: utf-8 -*-
"""SpaceX (private company) news digest for the daily decision dashboard.

The dashboard covers listed tickers only, so this module adds a small
"SpaceX 동향" block built from public Google News headlines.

Design constraints:
- No paid search API: only the public Google News RSS feed plus the LLM
  backend the project already uses.
- Strictly fail-open: every failure (network, parsing, LLM) downgrades to the
  headline list, and a total failure returns an empty string so the existing
  five-ticker report is emitted exactly as before.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

from src.report_language import normalize_report_language

logger = logging.getLogger(__name__)

RSS_URL = "https://news.google.com/rss/search?q=SpaceX&hl=en-US&gl=US&ceid=US:en"
MAX_HEADLINES = 5
_HTTP_TIMEOUT_SECONDS = 10.0
_HEADLINES_TTL_SECONDS = 30 * 60
_RENDER_TTL_SECONDS = 30 * 60
_RENDER_FAILURE_TTL_SECONDS = 5 * 60
_USER_AGENT = "Mozilla/5.0 (compatible; daily_stock_analysis/1.0; +https://github.com/nkjunbc/daily_stock_analysis)"

_HEADLINES_CACHE: Dict[str, Any] = {"ts": 0.0, "items": []}
_RENDER_CACHE: Dict[str, Dict[str, Any]] = {}

_HEADING = {
    "ko": "## 🚀 SpaceX (비상장 동향)",
    "en": "## 🚀 SpaceX (Private-Market Watch)",
    "zh": "## 🚀 SpaceX（未上市动态）",
}
_NOTE = {
    "ko": "SpaceX는 비상장사라 시세·재무 지표가 없어, 최근 뉴스 흐름만 정리했습니다.",
    "en": "SpaceX is privately held, so this covers recent news flow only (no quote or financial metrics).",
    "zh": "SpaceX 未上市，无行情与财务指标，仅汇总近期新闻。",
}
_HEADLINES_HEADING = {
    "ko": "**📰 최근 헤드라인**",
    "en": "**📰 Latest headlines**",
    "zh": "**📰 近期头条**",
}
_NO_SUMMARY_NOTE = {
    "ko": "_요약 생성이 어려워 헤드라인만 정리했습니다._",
    "en": "_Summary generation was unavailable; headlines only._",
    "zh": "_摘要生成不可用，仅列头条。_",
}

_LLM_INSTRUCTION = {
    "ko": (
        "당신은 한국어 금융 뉴스 에디터입니다. 아래는 최근 SpaceX 관련 뉴스 헤드라인입니다.\n"
        "헤드라인에 적힌 사실만 사용해 SpaceX(비상장사)의 최근 동향을 한국어 3~4줄로 요약하세요.\n"
        "- 각 줄은 '- '로 시작하는 짧은 한 문장으로 씁니다.\n"
        "- 발사·스타링크, 자금 조달·기업가치, 정부 계약·규제, 경쟁 구도 중 눈에 띄는 흐름 위주로 정리합니다.\n"
        "- 헤드라인에 없는 내용은 쓰지 말고, 숫자·날짜는 헤드라인에 나온 것만 씁니다.\n"
        "- 제목, 인사말, 코드블록, 머리말 없이 요약 줄만 출력합니다."
    ),
    "en": (
        "You are a financial news editor. Below are recent SpaceX headlines.\n"
        "Using only facts stated in the headlines, summarize SpaceX's latest developments in 3-4 short lines.\n"
        "- Each line starts with '- ' and is one short sentence.\n"
        "- Focus on launches/Starlink, funding and valuation, government contracts/regulation, and competitive moves.\n"
        "- Do not add facts, numbers, or dates that are not in the headlines.\n"
        "- Output only the bullet lines: no title, greeting, or code block."
    ),
    "zh": (
        "你是一名财经新闻编辑。以下是近期 SpaceX 相关新闻标题。\n"
        "仅使用标题中的事实，用 3-4 行中文简要概括 SpaceX 的最新动态。\n"
        "- 每行以 '- ' 开头，一句话。\n"
        "- 聚焦发射/星链、融资与估值、政府合同与监管、竞争格局。\n"
        "- 不要补充标题中没有的事实、数字或日期。\n"
        "- 只输出这些行，不要标题、问候语或代码块。"
    ),
}


def _lang(value: Optional[str]) -> str:
    return normalize_report_language(value)


def _short_date(raw: Optional[str]) -> str:
    """Turn an RFC-822 pubDate into a compact YYYY-MM-DD date."""
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001 - malformed feeds must not break rendering
        return ""


def fetch_spacex_headlines(limit: int = MAX_HEADLINES, timeout: float = _HTTP_TIMEOUT_SECONDS) -> List[Dict[str, str]]:
    """Fetch recent SpaceX headlines from Google News RSS (newest first)."""
    request = urllib.request.Request(RSS_URL, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URL
        payload = response.read()

    root = ET.fromstring(payload)
    items: List[Dict[str, str]] = []
    seen = set()
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        source = (node.findtext("source") or "").strip()
        published = _short_date(node.findtext("pubDate"))
        if not title:
            continue
        # Google News titles usually end with " - Publisher"; strip it so the
        # source is not printed twice next to the headline.
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)].strip()
        elif not source and " - " in title:
            title, source = (part.strip() for part in title.rsplit(" - ", 1))
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "title": title,
                "link": link,
                "source": source,
                "published": published,
            }
        )
        if len(items) >= max(1, limit):
            break
    return items


def _cached_headlines() -> List[Dict[str, str]]:
    now = time.time()
    if _HEADLINES_CACHE["items"] and now - float(_HEADLINES_CACHE["ts"]) < _HEADLINES_TTL_SECONDS:
        return list(_HEADLINES_CACHE["items"])
    items = fetch_spacex_headlines()
    _HEADLINES_CACHE["ts"] = now
    _HEADLINES_CACHE["items"] = items
    return list(items)


def _headline_prompt(headlines: List[Dict[str, str]], language: str) -> str:
    numbered = "\n".join(
        f"{index}. {item['title']}"
        + (f" ({item['source']})" if item.get("source") else "")
        + (f" [{item['published']}]" if item.get("published") else "")
        for index, item in enumerate(headlines, start=1)
    )
    header = {"ko": "헤드라인:", "en": "Headlines:", "zh": "标题:"}[language]
    return f"{_LLM_INSTRUCTION[language]}\n\n{header}\n{numbered}"


def _clean_bullets(text: Optional[str]) -> List[str]:
    """Reduce raw LLM output to at most four clean bullet lines."""
    if not text:
        return []
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    bullets: List[str] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^([-*•·]|\d+[.)])\s*", "", line).strip()
        line = line.lstrip("*").strip()
        if not line:
            continue
        if len(line) > 240:
            line = line[:240].rstrip() + "…"
        bullets.append(f"- {line}")
        if len(bullets) >= 4:
            break
    if len(bullets) < 2:
        return []
    return bullets


def summarize_headlines(headlines: List[Dict[str, str]], language: str = "ko") -> List[str]:
    """Summarize headlines with the project's LLM backend; [] when unavailable."""
    if not headlines:
        return []
    lang = _lang(language)
    prompt = _headline_prompt(headlines, lang)
    try:
        from src.analyzer import GeminiAnalyzer

        analyzer = GeminiAnalyzer()
        text = analyzer.generate_text(prompt, max_tokens=512, temperature=0.3)
    except Exception as exc:  # noqa: BLE001 - fail-open by design
        logger.warning("SpaceX 요약 생성 실패(헤드라인으로 대체): %s", exc)
        return []
    bullets = _clean_bullets(text)
    if not bullets:
        logger.info("SpaceX 요약이 비어 있어 헤드라인으로 대체합니다.")
    return bullets


def _headline_lines(headlines: List[Dict[str, str]], language: str) -> List[str]:
    lines: List[str] = []
    for item in headlines:
        title = item.get("title") or ""
        meta = " · ".join(part for part in (item.get("source"), item.get("published")) if part)
        link = item.get("link") or ""
        label = f"[{title}]({link})" if link else title
        lines.append(f"- {label}" + (f" — {meta}" if meta else ""))
    return lines


def _store_render(language: str, text: str, *, ttl: float) -> str:
    _RENDER_CACHE[language] = {"ts": time.time(), "value": text, "ttl": ttl}
    return text


def render_spacex_section(language: str = "ko") -> str:
    """Render the SpaceX block as Markdown; "" when nothing can be produced."""
    lang = _lang(language)
    cached = _RENDER_CACHE.get(lang)
    if cached and time.time() - float(cached.get("ts", 0.0)) < float(cached.get("ttl", _RENDER_TTL_SECONDS)):
        return str(cached.get("value") or "")

    try:
        headlines = _cached_headlines()
    except Exception as exc:  # noqa: BLE001 - network/feed failure must not touch the report
        logger.warning("SpaceX 헤드라인 수집 실패(섹션 생략): %s", exc)
        return _store_render(lang, "", ttl=_RENDER_FAILURE_TTL_SECONDS)

    if not headlines:
        return _store_render(lang, "", ttl=_RENDER_FAILURE_TTL_SECONDS)

    try:
        bullets = summarize_headlines(headlines, lang)
    except Exception as exc:  # noqa: BLE001
        logger.warning("SpaceX 요약 단계 예외(헤드라인으로 대체): %s", exc)
        bullets = []

    lines: List[str] = [_HEADING[lang], "", f"> {_NOTE[lang]}", ""]
    if bullets:
        lines.extend(bullets)
        lines.append("")
    else:
        lines.extend([_NO_SUMMARY_NOTE[lang], ""])
    lines.append(_HEADLINES_HEADING[lang])
    lines.extend(_headline_lines(headlines, lang))

    return _store_render(lang, "\n".join(lines).strip(), ttl=_RENDER_TTL_SECONDS)


def reset_cache() -> None:
    """Clear module caches (used by tests)."""
    _HEADLINES_CACHE["ts"] = 0.0
    _HEADLINES_CACHE["items"] = []
    _RENDER_CACHE.clear()

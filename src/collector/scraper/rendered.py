"""Playwright で出馬表を描画し、JS反映後の単勝オッズ/人気を読む(フォールバック)。

通常の HTML/API でオッズが取得できない場合の重い手段。必要時だけ使う。
"""

import logging
import time

from src.collector.scraper._core import (
    BASE_URL,
    USER_AGENT,
    _metrics,
    _parse_float,
)
from src.common.config import settings

logger = logging.getLogger(__name__)


def _parse_rendered_odds_values(values) -> dict[int, dict[str, float | int]]:
    rendered: dict[int, dict[str, float | int]] = {}
    if not isinstance(values, dict):
        return {}
    for horse_number_text, row in values.items():
        if not isinstance(row, dict):
            continue
        try:
            horse_number = int(horse_number_text)
        except (TypeError, ValueError):
            continue
        item: dict[str, float | int] = {}
        odds = _parse_float(str(row.get("odds", "")))
        if odds is not None:
            item["odds"] = odds
        popularity_text = str(row.get("popularity", "")).strip()
        if popularity_text.isdigit():
            item["popularity"] = int(popularity_text)
        if item:
            rendered[horse_number] = item
    return rendered


def _read_rendered_win_odds(page, race_id: str) -> dict[int, dict[str, float | int]]:
    """Read pre-race odds/popularity from an already-open Playwright page."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    url = f"{BASE_URL}/race/shutuba.html?race_id={race_id}"
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    try:
        page.wait_for_function(
            """() => Array.from(document.querySelectorAll('span[id^="odds-"], span[id^="ninki-"]'))
                .some((el) => /^\\d+(?:\\.\\d+)?$/.test((el.textContent || '').trim()))""",
            timeout=5000,
        )
    except PlaywrightTimeoutError:
        logger.info("rendered odds did not appear before timeout: race_id=%s", race_id)

    values = page.evaluate(
        """() => {
            const byHorse = {};
            const put = (id, key, raw) => {
                const match = /_(\\d+)$/.exec(id || '');
                if (!match) return;
                const horseNumber = Number(match[1]);
                const text = (raw || '').trim();
                if (!byHorse[horseNumber]) byHorse[horseNumber] = {};
                byHorse[horseNumber][key] = text;
            };
            document.querySelectorAll('span[id^="odds-"]').forEach((el) => {
                put(el.id, 'odds', el.textContent);
            });
            document.querySelectorAll('span[id^="ninki-"]').forEach((el) => {
                put(el.id, 'popularity', el.textContent);
            });
            return byHorse;
        }"""
    )
    return _parse_rendered_odds_values(values)


def _new_rendered_page(browser):
    page = browser.new_page(user_agent=USER_AGENT, locale="ja-JP", timezone_id="Asia/Tokyo")
    page.route(
        "**/*",
        lambda route: route.abort()
        if route.request.resource_type in {"image", "stylesheet", "font"}
        else route.continue_(),
    )
    return page


class RenderedOddsClient:
    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._page = None

    def open(self) -> None:
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        _metrics.playwright_browser_starts += 1
        self._page = _new_rendered_page(self._browser)

    def fetch_win_odds(self, race_id: str) -> dict[int, dict[str, float | int]]:
        self.open()
        _metrics.playwright_pages += 1
        try:
            return _read_rendered_win_odds(self._page, race_id)
        finally:
            time.sleep(settings.SCRAPER_REQUEST_INTERVAL_SECONDS)

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self._page = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _fetch_rendered_win_odds(race_id: str) -> dict[int, dict[str, float | int]]:
    """Use a real browser to read pre-race odds/popularity after netkeiba JS updates the page.

    The normal requests path is cheaper and remains the default. This function is a fallback for
    races where the static HTML/API does not expose values that are visible in a browser.
    """
    try:
        with RenderedOddsClient() as client:
            return client.fetch_win_odds(race_id)
    except ImportError:
        logger.warning("playwright is not installed; skip rendered odds for race_id=%s", race_id)
        return {}
    except Exception as exc:
        logger.warning("failed to fetch rendered odds for race_id=%s: %s", race_id, exc)
        return {}

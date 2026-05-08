from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin

from playwright.sync_api import BrowserContext, Page

LOCALE = "en-us"
MIN_REQUEST_INTERVAL_SECONDS = 1.0
KNOWN_PRODUCT_SLUGS = {
    "42143": "ferrari-daytona-sp3-42143",
}


@dataclass
class RetailResult:
    set_number: str
    product_url: str
    price: str


@dataclass
class RateLimiter:
    min_interval_seconds: float
    _last_request_time: float = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_time
        remaining = self.min_interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_time = time.monotonic()


def _lego_goto(page: Page, url: str, limiter: RateLimiter, timeout_ms: int = 45000) -> None:
    limiter.wait()
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)


def _handle_possible_bot_challenge(page: Page, headless: bool, seconds_to_wait: int = 90) -> None:
    title = (page.title() or "").strip().lower()
    if "just a moment" not in title:
        return
    if headless:
        raise RuntimeError(
            "LEGO showed bot protection while running headless. "
            "Set HEADLESS = False to manually complete the challenge once."
        )
    print("LEGO showed bot protection. Please complete the challenge in the browser window.")
    print(f"Waiting up to {seconds_to_wait} seconds for the page to continue...")
    page.wait_for_timeout(seconds_to_wait * 1000)


def _find_product_url(page: Page, set_number: str) -> str:
    set_suffix_re = re.compile(rf"-{re.escape(set_number)}(?:[/?#]|$)", re.IGNORECASE)
    links = page.locator("a[href*='/product/']")
    for i in range(links.count()):
        href = links.nth(i).get_attribute("href")
        if not href:
            continue
        absolute = urljoin("https://www.lego.com", href)
        if "/product/" in absolute.lower() and set_suffix_re.search(absolute):
            return absolute
    raise RuntimeError(f"Could not find a LEGO product link for set {set_number} on search results page.")


def _candidate_product_urls(set_number: str) -> list[str]:
    urls: list[str] = []
    known_slug = KNOWN_PRODUCT_SLUGS.get(set_number)
    if known_slug:
        urls.append(f"https://www.lego.com/{LOCALE}/product/{known_slug}")
    urls.append(f"https://www.lego.com/{LOCALE}/product/{set_number}")
    return urls


def _page_matches_set_number(page: Page, set_number: str) -> bool:
    if f"-{set_number}" in page.url:
        return True
    return set_number in page.locator("body").inner_text()


def _resolve_product_url(page: Page, limiter: RateLimiter, set_number: str, headless: bool) -> str:
    search_url = f"https://www.lego.com/{LOCALE}/search?q={set_number}"
    _lego_goto(page, search_url, limiter)
    _handle_possible_bot_challenge(page, headless=headless)
    page.wait_for_timeout(2500)
    try:
        return _find_product_url(page, set_number)
    except RuntimeError:
        pass

    for candidate_url in _candidate_product_urls(set_number):
        _lego_goto(page, candidate_url, limiter)
        _handle_possible_bot_challenge(page, headless=headless)
        page.wait_for_timeout(1500)
        if _page_matches_set_number(page, set_number):
            return page.url

    raise RuntimeError(
        f"Could not find a LEGO product link for set {set_number} from search results or product URL candidates."
    )


def _extract_price(page: Page) -> str:
    selectors = [
        "meta[property='product:price:amount']",
        "[data-test='product-price-sale']",
        "[data-test='product-price']",
        "[data-test='product-overview-price']",
        "[class*='ProductPrice']",
        "[class*='price']",
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        if locator.count() == 0:
            continue

        if selector.startswith("meta"):
            content = locator.get_attribute("content")
            if content and re.search(r"\d", content):
                return f"${content.strip()}"
            continue

        text = locator.inner_text().strip()
        match = re.search(r"\$\s*\d[\d,]*(?:\.\d{2})?", text)
        if match:
            return match.group(0).replace(" ", "")

    body_text = page.locator("body").inner_text()
    match = re.search(r"\$\s*\d[\d,]*(?:\.\d{2})?", body_text)
    if match:
        return match.group(0).replace(" ", "")
    raise RuntimeError("Could not locate a US dollar price on the LEGO product page.")


def fetch_retail_price(context: BrowserContext, set_number: str, *, headless: bool) -> RetailResult:
    page = context.new_page()
    limiter = RateLimiter(min_interval_seconds=MIN_REQUEST_INTERVAL_SECONDS)
    product_url = _resolve_product_url(page, limiter, set_number, headless=headless)
    _lego_goto(page, product_url, limiter)
    _handle_possible_bot_challenge(page, headless=headless)
    page.wait_for_timeout(2500)
    price = _extract_price(page)
    page.close()
    return RetailResult(set_number=set_number, product_url=product_url, price=price)

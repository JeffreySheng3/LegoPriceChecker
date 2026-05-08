from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.sync_api import BrowserContext, Page

from config import KNOWN_BRICKECONOMY_URLS, MAX_RESALE_HISTORY


@dataclass
class ResaleEntry:
    title: str
    price: str
    delta_vs_value: str
    marketplace: str


@dataclass
class ResaleResult:
    set_number: str
    brickeconomy_url: str
    entries: list[ResaleEntry]


def _handle_possible_bot_challenge(page: Page, headless: bool, seconds_to_wait: int = 90) -> None:
    title = (page.title() or "").strip().lower()
    body_text = page.locator("body").inner_text().lower()
    if "just a moment" not in title and "security verification" not in body_text:
        return
    if headless:
        raise RuntimeError(
            "BrickEconomy showed bot protection while running headless. "
            "Set HEADLESS = False to manually complete the challenge once."
        )
    print("BrickEconomy showed bot protection. Please complete the challenge in the browser window.")
    print(f"Waiting up to {seconds_to_wait} seconds for the page to continue...")
    page.wait_for_timeout(seconds_to_wait * 1000)


def _detect_marketplace(row: Page) -> str:
    classes = row.locator("div[class^='set-sale-']").first.get_attribute("class") or ""
    for marker, name in (
        ("set-sale-ebay", "eBay"),
        ("set-sale-stockx", "StockX"),
        ("set-sale-bricklink", "BrickLink"),
        ("set-sale-amazon", "Amazon"),
    ):
        if marker in classes:
            return name
    return "Unknown"


def _extract_resale_history(page: Page, limit: int) -> list[ResaleEntry]:
    rows = page.locator("#sales_region_table tr")
    if rows.count() == 0:
        raise RuntimeError("Could not find BrickEconomy resale table (#sales_region_table).")

    resale_entries: list[ResaleEntry] = []
    for i in range(rows.count()):
        row = rows.nth(i)
        title = row.locator("td:nth-child(2)").inner_text().strip()
        if not title:
            continue

        price_cell_text = row.locator("td:nth-child(3)").inner_text().strip()
        match_price = re.search(r"\$\d[\d,]*(?:\.\d{2})?", price_cell_text)
        if not match_price:
            continue

        match_delta = re.search(r"[+-]\d+(?:\.\d+)?%", price_cell_text)
        delta = match_delta.group(0) if match_delta else "N/A"

        resale_entries.append(
            ResaleEntry(
                title=title,
                price=match_price.group(0),
                delta_vs_value=delta,
                marketplace=_detect_marketplace(row),
            )
        )
        if len(resale_entries) >= limit:
            break

    if not resale_entries:
        raise RuntimeError("No resale entries with prices were found on BrickEconomy.")
    return resale_entries


def fetch_resale_prices(
    context: BrowserContext,
    set_number: str,
    *,
    headless: bool,
    limit: int = MAX_RESALE_HISTORY,
) -> ResaleResult:
    brickeconomy_url = KNOWN_BRICKECONOMY_URLS.get(set_number)
    if not brickeconomy_url:
        raise RuntimeError(f"No BrickEconomy URL configured for set {set_number}.")

    page = context.new_page()
    page.goto(brickeconomy_url, wait_until="domcontentloaded", timeout=45000)
    _handle_possible_bot_challenge(page, headless=headless)
    page.wait_for_timeout(4000)
    entries = _extract_resale_history(page, limit)
    final_url = page.url
    page.close()
    return ResaleResult(set_number=set_number, brickeconomy_url=final_url, entries=entries)

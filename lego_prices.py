#!/usr/bin/env python3
"""Scrape BrickEconomy resale listings for a hardcoded LEGO set."""

from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

SET_NUMBER = "42143"
BRICKECONOMY_URL = "https://www.brickeconomy.com/set/42143-1/lego-technic-ferrari-daytona-sp3"
# BrickEconomy often uses anti-bot checks, so keep browser visible by default.
HEADLESS = False
MAX_RESALE_HISTORY = 5


@dataclass
class ResaleEntry:
    title: str
    price: str
    delta_vs_value: str
    marketplace: str


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
    count = rows.count()
    if count == 0:
        raise RuntimeError("Could not find BrickEconomy resale table (#sales_region_table).")

    resale_entries: list[ResaleEntry] = []
    for i in range(count):
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
        marketplace = _detect_marketplace(row)

        resale_entries.append(
            ResaleEntry(
                title=title,
                price=match_price.group(0),
                delta_vs_value=delta,
                marketplace=marketplace,
            )
        )

        if len(resale_entries) >= limit:
            break

    if not resale_entries:
        raise RuntimeError("No resale entries with prices were found on BrickEconomy.")
    return resale_entries


def main() -> int:
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=HEADLESS, channel="chrome")
            except Exception:
                browser = playwright.chromium.launch(headless=HEADLESS)
            context = browser.new_context()
            page = context.new_page()

            page.goto(BRICKECONOMY_URL, wait_until="domcontentloaded", timeout=45000)
            _handle_possible_bot_challenge(page, headless=HEADLESS)
            page.wait_for_timeout(4000)
            entries = _extract_resale_history(page, MAX_RESALE_HISTORY)

            print(f"Set number: {SET_NUMBER}")
            print(f"BrickEconomy URL: {page.url}")
            print(f"Last {MAX_RESALE_HISTORY} resale listings:")
            for idx, entry in enumerate(entries, start=1):
                print(f"{idx}. [{entry.marketplace}] {entry.price} ({entry.delta_vs_value})")
                print(f"   {entry.title}")

            browser.close()
            return 0

    except ModuleNotFoundError:
        print("Playwright is not installed.")
        print("Install with: pip install playwright")
        print("Then install browser binaries with: playwright install")
    except PlaywrightTimeoutError as exc:
        print(f"Timed out while loading BrickEconomy: {exc}")
    except Exception as exc:
        print(f"Resale scraping failed: {exc}")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

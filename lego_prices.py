#!/usr/bin/env python3
"""Entry point for retail and resale LEGO price scraping."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

from config import HEADLESS, MAX_RESALE_HISTORY, SET_NUMBER
from resale_price import fetch_resale_prices
from retail_price import fetch_retail_price

OUTPUT_FILE = Path("price_report.txt")


def _build_report(retail, resale) -> str:
    lines = [
        "LEGO Price Report",
        "=================",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Set number: {SET_NUMBER}",
        "",
        "Retail (LEGO.com)",
        "-----------------",
        f"Product URL: {retail.product_url}",
        f"Current US price: {retail.price}",
        "",
        "Resale (BrickEconomy)",
        "----------------------",
        f"Page URL: {resale.brickeconomy_url}",
        f"Last {MAX_RESALE_HISTORY} resale listings:",
    ]
    for idx, entry in enumerate(resale.entries, start=1):
        lines.append(f"{idx}. [{entry.marketplace}] {entry.price} ({entry.delta_vs_value})")
        lines.append(f"   {entry.title}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=HEADLESS, channel="chrome")
            except Exception:
                browser = playwright.chromium.launch(headless=HEADLESS)
            context = browser.new_context(locale="en-US")

            retail = fetch_retail_price(context, SET_NUMBER, headless=HEADLESS)
            resale = fetch_resale_prices(context, SET_NUMBER, headless=HEADLESS, limit=MAX_RESALE_HISTORY)
            report = _build_report(retail, resale)
            OUTPUT_FILE.write_text(report, encoding="utf-8")

            print(f"Wrote report to {OUTPUT_FILE}")

            browser.close()
            return 0

    except ModuleNotFoundError:
        print("Playwright is not installed.")
        print("Install with: pip install playwright")
        print("Then install browser binaries with: playwright install")
    except PlaywrightTimeoutError as exc:
        print(f"Timed out while loading pages: {exc}")
    except Exception as exc:
        print(f"Scraping failed: {exc}")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

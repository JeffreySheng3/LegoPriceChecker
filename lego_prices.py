#!/usr/bin/env python3
"""Entry point for retail and resale LEGO price scraping."""

from __future__ import annotations

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

from resale_price import MAX_RESALE_HISTORY, fetch_resale_prices
from retail_price import fetch_retail_price

SET_NUMBER = "42143"
# Both LEGO and BrickEconomy may require human verification.
HEADLESS = False


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

            print(f"Set number: {SET_NUMBER}")
            print()
            print("Retail (LEGO.com):")
            print(f"- Product URL: {retail.product_url}")
            print(f"- Current US price: {retail.price}")
            print()
            print("Resale (BrickEconomy):")
            print(f"- Page URL: {resale.brickeconomy_url}")
            print(f"- Last {MAX_RESALE_HISTORY} resale listings:")
            for idx, entry in enumerate(resale.entries, start=1):
                print(f"  {idx}. [{entry.marketplace}] {entry.price} ({entry.delta_vs_value})")
                print(f"     {entry.title}")

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

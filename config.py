from __future__ import annotations

SET_NUMBER = "42143"

# Keep browser visible because target sites may present bot checks.
HEADLESS = False

# Retail settings
LEGO_LOCALE = "en-us"
LEGO_MIN_REQUEST_INTERVAL_SECONDS = 1.0
KNOWN_PRODUCT_SLUGS = {
    "42143": "ferrari-daytona-sp3-42143",
}

# Resale settings
MAX_RESALE_HISTORY = 5
KNOWN_BRICKECONOMY_URLS = {
    "42143": "https://www.brickeconomy.com/set/42143-1/lego-technic-ferrari-daytona-sp3",
}

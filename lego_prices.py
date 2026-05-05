#!/usr/bin/env python3
"""
Load a LEGO inventory from CSV, then optionally look up LEGO Shop list prices online
from the consumer product detail page (schema.org Product JSON-LD embedded in HTML).

Brickset API is intentionally not used.

CSV columns (header row required); names matched case-insensitively:

  set number, name, year, theme id, number of parts, image url

Aliases accepted, e.g. set_number / theme_id / parts / image_url.

Usage:
  python lego_prices.py sets.csv                    # interactive menu (TTY)
  python lego_prices.py sets.csv --lookup 75192-1   # one PDP fetch, then exit
  python lego_prices.py sets.csv --lookup-all       # every catalog row (delay between PDP fetches)

  python lego_prices.py sets.csv --locale en-gb    # LEGO store region slug (default en-us)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class CatalogRow:
    set_number: str
    name: str
    year: str
    theme_id: str
    parts: str
    image_url: str


def _normalize_header(h: str) -> str:
    return h.strip().lower().replace(" ", "_")


def _row_get(row: dict[str, str], *keys: str) -> str:
    keyed = {_normalize_header(k): (v or "").strip() for k, v in row.items()}
    for key in keys:
        nk = _normalize_header(key)
        if nk in keyed and keyed[nk]:
            return keyed[nk]
    return ""


def load_catalog(path: Path) -> list[CatalogRow]:
    if not path.is_file():
        raise FileNotFoundError(f"No such file: {path}")

    out: list[CatalogRow] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header row: {path}")

        missing_rows: list[int] = []
        for idx, raw in enumerate(reader, start=2):
            if not raw or all((v or "").strip() == "" for v in raw.values()):
                continue
            sn = _row_get(raw, "set_number", "set number", "number")
            name = _row_get(raw, "name")
            year = _row_get(raw, "year")
            theme_id = _row_get(raw, "theme_id", "theme id")
            parts = _row_get(raw, "parts", "number of parts", "pieces")
            image_url = _row_get(raw, "image_url", "image url", "image")
            if not sn:
                missing_rows.append(idx)
                continue
            out.append(
                CatalogRow(
                    set_number=sn,
                    name=name,
                    year=year,
                    theme_id=theme_id,
                    parts=parts,
                    image_url=image_url,
                )
            )

    if missing_rows:
        preview = ", ".join(map(str, missing_rows[:20]))
        suffix = "" if len(missing_rows) <= 20 else " ..."
        raise ValueError(f"CSV rows missing set number at line(s): {preview}{suffix}")

    return out


def primary_set_digits(set_number: str) -> str:
    head = set_number.strip().split("-", 1)[0]
    digits = re.sub(r"\D+", "", head)
    return digits or re.sub(r"\D+", "", set_number.strip())


def _type_values(t: Any) -> set[str]:
    if isinstance(t, str):
        return {t}
    if isinstance(t, list):
        return {str(x) for x in t if isinstance(x, str)}
    return set()


def _flatten_json_ld_objects(node: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            t = x.get("@type")
            if "Product" in _type_values(t):
                out.append(x)
            g = x.get("@graph")
            if isinstance(g, list):
                for it in g:
                    walk(it)
            for k, v in x.items():
                if k != "@graph":
                    walk(v)
        elif isinstance(x, list):
            for it in x:
                walk(it)

    walk(node)
    return out


_LD_JSON_BLOCKS = re.compile(
    r'<script type="application/ld\+json"[^>]*>(.*?)</script>',
    flags=re.IGNORECASE | re.DOTALL,
)


def _products_from_product_page_html(html: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for block in _LD_JSON_BLOCKS.findall(html):
        raw = block.strip()
        try:
            j = json.loads(raw)
        except json.JSONDecodeError:
            continue
        found.extend(_flatten_json_ld_objects(j))
    return found


_CURRENCY_SYM = {"USD": "$", "GBP": "£", "EUR": "€", "CAD": "CA$", "AUD": "A$"}

_AVAIL_TAIL_OK = frozenset({"InStock", "OutOfStock", "SoldOut", "LimitedAvailability", "OnlineOnly"})


def _availability_suffix(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    tail = value.rsplit("/", 1)[-1]
    if tail in _AVAIL_TAIL_OK:
        return f" [{tail}]"
    return ""


def _format_offer(offers: Any) -> tuple[str | None, str | None]:
    """Return (display_price, primary_offer_product_url_maybe)."""

    if isinstance(offers, list):
        for item in offers:
            display, url = _format_offer(item)
            if display:
                return display, url
        return None, None

    if not isinstance(offers, dict):
        return None, None

    price = offers.get("price")
    if price is None:
        return None, offers.get("url")

    curr = str(offers.get("priceCurrency") or "").upper().strip()

    try:
        amt = f"{float(price):.2f}"
    except (TypeError, ValueError):
        amt = str(price).strip()

    sym = _CURRENCY_SYM.get(curr, "")
    if sym:
        display = f"{sym}{amt}"
    elif curr:
        display = f"{curr} {amt}"
    else:
        display = amt

    display += _availability_suffix(offers.get("availability"))
    return display, offers.get("url")


def _slugify_catalog_name(name: str) -> str:
    if not name:
        return ""

    norm = unicodedata.normalize("NFKD", name)
    ascii_only = norm.encode("ascii", "ignore").decode("ascii")
    s = ascii_only.lower()

    replacements = {"&": " and ", "+": " plus ", "/": " ", "'": "", "™": "", "®": ""}
    for a, b in replacements.items():
        s = s.replace(a, b)

    s = re.sub(r"[^\w\s-]", " ", s)
    s = re.sub(r"\s+", "-", s.strip())
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def product_slug_candidates(row: CatalogRow) -> list[str]:
    digits = primary_set_digits(row.set_number)
    if not digits:
        return []

    base = _slugify_catalog_name(row.name)
    prefixes = ("ucs-", "ideas-", "technic-", "lego-")

    base_variants: list[str] = []
    if base:
        base_variants.append(base)
        for prefix in prefixes:
            if base.startswith(prefix):
                tail = base[len(prefix) :].strip("-")
                if tail:
                    base_variants.append(tail)

    slugs: list[str] = []
    seen: set[str] = set()
    for variant in dict.fromkeys(base_variants):
        slug = f"{variant}-{digits}"
        if slug not in seen:
            seen.add(slug)
            slugs.append(slug)

    if not slugs:
        slugs.append(digits)

    return slugs


def _fetch_url(url: str, *, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as e:
        # Some LEGO hosts return usable bodies on errors; callers usually discard.
        return int(e.code), e.read()
    except urllib.error.URLError:
        return 598, b""


def digits_match(csv_set_number: str, product_id: Any) -> bool:
    want = primary_set_digits(csv_set_number)
    if not want:
        return False
    got = str(product_id or "").strip()
    got_digits = re.sub(r"\D+", "", got)
    return bool(got_digits) and got_digits == want


def lookup_price_on_lego_pdp(
    *,
    locale: str,
    set_number: str,
    name: str,
    timeout: float = 35.0,
) -> tuple[str | None, str | None, str | None]:
    """
    Returns (price_display, product_page_url_that_worked_or_None, fallback_search_url).

    This builds likely product URL slugs from the catalog name plus the numeric portion of set_number,
    downloading https://www.lego.com/{locale}/product/{slug} until JSON-LD productId matches.
    """

    loc = locale.strip("/").strip().lower()

    fallback_search = "https://www.lego.com/" + loc + "/search?" + urllib.parse.urlencode(
        {"q": primary_set_digits(set_number) or set_number.strip()}
    )

    dummy = CatalogRow(set_number=set_number, name=name, year="", theme_id="", parts="", image_url="")
    for slug in product_slug_candidates(dummy):
        url = f"https://www.lego.com/{loc}/product/{slug}"

        status, body = _fetch_url(url, timeout=timeout)

        html = body.decode("utf-8", errors="replace")

        products = _products_from_product_page_html(html)
        if status in {404, 410, 598}:
            continue

        if status >= 400 and not products:

            continue

        if not products:
            continue

        matched: dict[str, Any] | None = None
        for p in products:
            if digits_match(set_number, p.get("productId")):
                matched = p
                break

        if matched is None:
            continue

        formatted, maybe_url = _format_offer(matched.get("offers"))
        canon_url = matched.get("@id") or maybe_url or url

        return formatted, str(canon_url).strip(), fallback_search

    return None, None, fallback_search


def print_catalog(rows: Iterable[CatalogRow], *, enumerated: bool = True) -> None:
    for i, r in enumerate(rows, start=1):
        prefix = f"{i:>4}. " if enumerated else ""
        print(f"{prefix}{r.set_number:<12} {r.year:<6} {r.name}")
        tail = []
        if r.theme_id:
            tail.append(f"theme_id={r.theme_id}")
        if r.parts:
            tail.append(f"parts={r.parts}")
        if tail:
            print(f"{' ' * (len(prefix) + 13 + 8)} ({', '.join(tail)})")


def interactive_menu(rows: list[CatalogRow], locale: str, delay_s: float) -> None:
    while True:
        print(
            """

Commands:
  l          List catalog
  p <term>   Look up LEGO.com PDP price using catalog row substring match on set_number
  p #<n>     Look up PDP price for list row n
  a          Look up PDP prices for all rows (respects delay between requests)
  q          Quit"""
        )

        try:
            line = input("\n> ").strip()
        except EOFError:
            print()
            return

        if not line:
            continue

        cmd, _, rest = line.partition(" ")
        cmd_l = cmd.lower()
        rest = rest.strip()

        if cmd_l in ("q", "quit", "exit"):
            return
        if cmd_l == "l":
            print_catalog(rows)
            continue
        if cmd_l == "a":
            lookup_all(rows, locale=locale, delay_s=delay_s)
            continue

        if cmd_l == "p":
            if rest.startswith("#"):
                try:
                    n = int(rest[1:].strip())
                except ValueError:
                    print("Usage: p #<row_number>")
                    continue
                if n < 1 or n > len(rows):
                    print(f"Row {n} is out of range (1-{len(rows)}).")
                    continue
                _print_price_for_row(rows[n - 1], locale)
                continue

            if not rest:
                print("Usage: p <set_number substring> | p #<row_number>")
                continue

            matches = [r for r in rows if rest.lower() in r.set_number.lower()]
            if len(matches) == 0:
                print(f"No catalog row with set_number containing {rest!r}.")
                continue
            if len(matches) > 1:
                print(f"Ambiguous ({len(matches)} matches). Narrow the term or use row number:")
                print_catalog(matches)
                continue

            _print_price_for_row(matches[0], locale)
            continue

        print("Unknown command.")


def _print_price_for_row(r: CatalogRow, locale: str) -> None:
    print(f"Looking up LEGO.com price for set {r.set_number} ({r.name}) ...")

    price, page_url, search_url = lookup_price_on_lego_pdp(
        locale=locale,
        set_number=r.set_number,
        name=r.name,
    )

    if price:
        print(f"  {price}")

    else:
        print(
            "  Could not open a matching LEGO.com product detail page using this CSV slug guess,"
            " or the PDP JSON-LD offer did not contain a usable price.",
        )

    if page_url:
        print(f"  Product URL: {page_url}")

    print(f"  Search fallback: {search_url}")


def lookup_all(rows: list[CatalogRow], *, locale: str, delay_s: float) -> None:
    n = len(rows)
    if n == 0:
        print("Catalog is empty.")
        return

    for i, r in enumerate(rows, start=1):
        print(f"[{i}/{n}] {r.set_number} - {r.name}")
        price, page_url, search_url = lookup_price_on_lego_pdp(
            locale=locale,
            set_number=r.set_number,
            name=r.name,
        )

        if price:
            print(f"  {price}")

        else:
            print("  (no PDP price extracted)")

        if page_url:
            print(f"  {page_url}")

        print(f"  {search_url}")

        if i < n and delay_s > 0:
            time.sleep(delay_s)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="LEGO catalog CSV + LEGO.com PDP price lookup.")
    p.add_argument("csv_file", type=Path, help="Path to catalog CSV")
    p.add_argument(
        "--locale",
        default="en-us",
        help="LEGO.com locale slug in URLs (default: en-us)",
    )
    p.add_argument(
        "--lookup",
        metavar="SET",
        help="Look up PDP price for this set number once, using catalog name match if SET exists in CSV",
    )
    p.add_argument(
        "--lookup-all",
        action="store_true",
        help="Walk every CSV row and attempt PDP lookups",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=1.25,
        metavar="SEC",
        help="Pause between PDP requests during --lookup-all (default: 1.25)",
    )
    p.add_argument(
        "--no-menu",
        action="store_true",
        help="Skip interactive commands (requires --lookup or --lookup-all)",
    )

    args = p.parse_args(argv)

    if args.lookup and args.lookup_all:
        print("Specify only one of --lookup and --lookup-all.", file=sys.stderr)
        return 2

    try:
        rows = load_catalog(args.csv_file)

    except (OSError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2

    if args.lookup:
        needle = args.lookup.strip()

        needle_digits = primary_set_digits(needle)
        name_guess = ""
        for r in rows:
            if needle_digits and primary_set_digits(r.set_number) == needle_digits:
                name_guess = r.name
                break
            if r.set_number.strip() == needle:
                name_guess = r.name
                break

        price, page_url, search_url = lookup_price_on_lego_pdp(
            locale=args.locale,
            set_number=needle,
            name=name_guess,
        )

        print(f"Lookup: {needle}" + (f" - {name_guess}" if name_guess else ""))

        if price:
            print(price)
        else:
            print("No PDP price extracted from LEGO.com HTML.", file=sys.stderr)

        if page_url:
            print(page_url)

        print(search_url or "")

        return 0 if price else 1

    if args.lookup_all:
        lookup_all(rows, locale=args.locale, delay_s=max(0.0, args.delay))
        return 0

    print(f"Loaded {len(rows)} set(s) from {args.csv_file}")

    use_menu = (not args.no_menu) and sys.stdin.isatty()

    if not use_menu:
        print(
            "Non-interactive shells need --lookup SET or --lookup-all (or run on a TTY for the menu).",
            file=sys.stderr,
        )
        return 2

    interactive_menu(rows, locale=args.locale, delay_s=max(0.0, args.delay))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

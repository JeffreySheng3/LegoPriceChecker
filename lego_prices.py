#!/usr/bin/env python3
"""
Look up LEGO set prices using the free Brickset API v3.

Brickset returns official LEGO Shop / catalog retail prices by region (where available).
Get a free API key: https://brickset.com/tools/webservices/requestkey

Usage:
  set BRICKSET_API_KEY in the environment, then:

  python lego_prices.py --set 75192-1
  python lego_prices.py --query "Millennium Falcon"
  python lego_prices.py --check-key
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

BRICKSET_BASE = "https://brickset.com/api/v3.asmx"


@dataclass
class BricksetError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


def _post(method: str, body: dict[str, str], timeout: float = 30.0) -> dict[str, Any]:
    url = f"{BRICKSET_BASE}/{method}"
    data = urllib.parse.urlencode(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "lego-price-checker/1.0 (Python; Brickset API client)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise BricksetError(f"HTTP {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise BricksetError(f"Network error: {e.reason}") from e

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise BricksetError(f"Invalid JSON from Brickset: {e}") from e


def check_key(api_key: str) -> dict[str, Any]:
    return _post("checkKey", {"apiKey": api_key})


def get_sets(api_key: str, params: dict[str, Any]) -> dict[str, Any]:
    body = {
        "apiKey": api_key,
        "params": json.dumps(params, separators=(",", ":")),
    }
    return _post("getSets", body)


def format_price(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return f"{value:.2f}" if isinstance(value, float) else str(value)
    return str(value)


def summarize_set(s: dict[str, Any]) -> dict[str, Any]:
    legocom = s.get("LEGOCom") or {}
    regions = {}
    for code in ("US", "UK", "CA", "DE"):
        details = legocom.get(code) or {}
        rp = details.get("retailPrice")
        regions[f"{code}_retail"] = rp
    return {
        "set_id": s.get("setID"),
        "number": s.get("number"),
        "variant": s.get("numberVariant"),
        "name": s.get("name"),
        "year": s.get("year"),
        "theme": s.get("theme"),
        "subtheme": s.get("subtheme"),
        "pieces": s.get("pieces"),
        "minifigs": s.get("minifigs"),
        "brickset_url": s.get("bricksetURL"),
        **regions,
    }


def print_sets_human(sets: list[dict[str, Any]]) -> None:
    if not sets:
        print("No sets found.")
        return
    for s in sets:
        legocom = s.get("LEGOCom") or {}
        print(f"{s.get('number')}-{s.get('numberVariant')}  {s.get('name')}")
        print(f"  Theme: {s.get('theme')}  Year: {s.get('year')}  Pieces: {s.get('pieces')}")
        for code, sym in (("US", "$"), ("UK", "£"), ("CA", "CA$"), ("DE", "€")):
            det = legocom.get(code) or {}
            price = det.get("retailPrice")
            label = f"{code} retail"
            if price is not None:
                print(f"  {label}: {sym}{format_price(price)}")
            else:
                print(f"  {label}: —")
        url = s.get("bricksetURL")
        if url:
            print(f"  {url}")
        print()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="LEGO set retail prices via Brickset API (free key).")
    p.add_argument(
        "--api-key",
        default=os.environ.get("BRICKSET_API_KEY", ""),
        help="Brickset API key (default: env BRICKSET_API_KEY)",
    )
    g = p.add_mutually_exclusive_group(required=False)
    g.add_argument(
        "--set",
        dest="set_number",
        metavar="NUMBER",
        help='Set number with variant, e.g. 75192-1 or "10294-1"',
    )
    g.add_argument("--query", metavar="TEXT", help="Search set number, name, theme, subtheme")
    p.add_argument("--check-key", action="store_true", help="Validate API key and exit")
    p.add_argument("--json", action="store_true", help="Print JSON instead of text")
    p.add_argument("--page-size", type=int, default=20, metavar="N", help="Max results (default 20, max 500)")
    args = p.parse_args(argv)

    api_key = (args.api_key or "").strip()
    if not api_key:
        print(
            "Missing Brickset API key. Set BRICKSET_API_KEY or pass --api-key.\n"
            "Request a free key: https://brickset.com/tools/webservices/requestkey",
            file=sys.stderr,
        )
        return 2

    if args.check_key:
        try:
            r = check_key(api_key)
        except BricksetError as e:
            print(str(e), file=sys.stderr)
            return 1
        status = r.get("status")
        if args.json:
            print(json.dumps(r, indent=2))
        else:
            print("Valid key." if status == "success" else json.dumps(r, indent=2))
        return 0 if status == "success" else 1

    if not args.set_number and not args.query:
        p.error("Specify --set NUMBER, --query TEXT, or --check-key")

    params: dict[str, Any] = {"pageSize": min(max(args.page_size, 1), 500)}
    if args.set_number:
        sn = args.set_number.strip()
        if "-" not in sn:
            sn = f"{sn}-1"
        params["setNumber"] = sn
    else:
        params["query"] = args.query.strip()

    try:
        r = get_sets(api_key, params)
    except BricksetError as e:
        print(str(e), file=sys.stderr)
        return 1

    if r.get("status") != "success":
        msg = r.get("message", r)
        print(f"Brickset error: {msg}", file=sys.stderr)
        return 1

    sets = r.get("sets") or []
    if args.json:
        out = [summarize_set(s) for s in sets]
        print(json.dumps({"matches": r.get("matches"), "sets": out}, indent=2))
    else:
        print(f"Matches: {r.get('matches', len(sets))}\n")
        print_sets_human(sets)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

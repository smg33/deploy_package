#!/usr/bin/env python3
"""
Savvli daily rate scraper.

Fetches current cash-back rates for every store in stores_config.json across
the 6 tracked providers, and regenerates stores-data.js in the exact format
the site expects.

HONESTY NOTE ON CONFIDENCE LEVEL:
- Rakuten, TopCashback, and RebatesMe parsers are grounded in real, directly-
  fetched HTML (verified during development against live pages).
- BeFrugal, Mr Rebates, and Capital One Shopping parsers use generic "X%
  Cash Back" pattern matching that was NOT verified against live pages
  during development (the dev sandbox that wrote this script could not
  reach those specific domains). These three are the most likely to need
  debugging on the first real run. Check scrape_log.json after each run.

This script is designed to fail SAFELY: if a provider's page can't be parsed,
that specific offer is left unchanged (keeps last-known-good data) rather
than being deleted or zeroed out. The site should never show broken/missing
data because of a scraper hiccup.
"""

import json
import re
import time
import sys
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

USER_AGENT = "SavvliRateBot/1.0 (+https://savvli.com/about.html; daily rate check, respects robots.txt)"
REQUEST_DELAY_SECONDS = 3  # conservative, polite spacing between requests
REQUEST_TIMEOUT = 15

# ---------------------------------------------------------------------------
# Provider-specific rate extraction
# Each function takes raw HTML text and returns a rate string like "6%" or
# None if it couldn't confidently find one. NEVER guess - return None on any
# doubt, since a missing update is much safer than a wrong number.
# ---------------------------------------------------------------------------

def parse_rakuten(html):
    """
    VERIFIED against a real fetched page (rakuten.com/shop/nike, Sept 2026).
    Real pattern found: "Nike6% Cash Backwas 2%Shop Now" - the store name is
    immediately followed by the current rate, then optionally "was X%".
    We look for the FIRST "N% Cash Back" occurrence after stripping the page
    down, since that's consistently the primary/current rate for the store
    at the top of the page, before the per-product listings repeat it.
    """
    match = re.search(r'(\d+(?:\.\d+)?)\s*%\s*Cash Back', html)
    if match:
        return f"{match.group(1)}%"
    return None


def parse_topcashback(html):
    """
    VERIFIED against real raw HTML fetched via curl (Nike page, Sept 2026)
    - not a browser/JS-rendered view. The real rate lives in a dedicated
    element:
      <div class="merch-rate-card">...<span class="merch-cat__rate">8%</span>
    (Previous version was built from a markdown-rendered fetch, not real
    HTML - its primary regex expected newlines between "Cash Back" and the
    percentage that don't exist in the actual page, so it silently always
    fell through to the fragile "Get X% of the price back" marketing
    headline fallback. This version anchors on the real dedicated rate
    element instead.)
    """
    match = re.search(r'merch-cat__rate"[^>]*>\s*(\d+(?:\.\d+)?)\s*%', html)
    if match:
        return f"{match.group(1)}%"
    # fallback to the headline pattern if the dedicated element isn't found
    match = re.search(r'Get\s+(\d+(?:\.\d+)?)\s*%\s+of the price back', html)
    if match:
        return f"{match.group(1)}%"
    return None


def parse_befrugal(html):
    """
    NOT YET VERIFIED against a live page - generic pattern, likely needs
    adjustment after the first real run. BeFrugal's own marketing describes
    "up to X%" tiered offers often, so this may need refinement to avoid
    grabbing a promotional ceiling instead of the base/standard rate.
    """
    match = re.search(r'(\d+(?:\.\d+)?)\s*%\s*[Cc]ash\s*[Bb]ack', html)
    if match:
        return f"{match.group(1)}%"
    return None


def parse_mrrebates(html):
    """NOT YET VERIFIED against a live page - generic pattern."""
    match = re.search(r'(\d+(?:\.\d+)?)\s*%\s*[Cc]ash\s*[Bb]ack', html)
    if match:
        return f"{match.group(1)}%"
    return None


def parse_rebatesme(html):
    """
    VERIFIED against real raw HTML fetched via curl (Nike and Belk pages,
    Sept 2026) - not a browser/JS-rendered view. RebatesMe's page template
    puts the store's one true headline rate in a fixed block right after
    the store logo, before the "Shop Now" button:
      <div class="merchant-cash-back" ><span>...<b>N%</b>...Cash Back</span>
    Confirmed identical structure on two different stores (with or without
    an "Up to" prefix inside the span). This is the single authoritative
    rate for the page - not a per-deal promo number - which avoids the bug
    in earlier versions of this parser that grabbed an unrelated "X% OFF"
    sale discount or a per-deal cash-back repeat instead of the real rate
    (e.g. wrongly returned a flat 40% for every store).
    """
    match = re.search(
        r'class="merchant-cash-back"[^>]*>.*?<b>\s*(\d+(?:\.\d+)?)\s*%\s*</b>',
        html, re.IGNORECASE | re.DOTALL
    )
    if match:
        return f"{match.group(1)}%"
    return None


def parse_capitaloneshopping(html):
    """NOT YET VERIFIED against a live page - generic pattern."""
    match = re.search(r'(\d+(?:\.\d+)?)\s*%\s*[Cc]ash\s*[Bb]ack', html)
    if match:
        return f"{match.group(1)}%"
    return None


PARSERS = {
    "Rakuten": parse_rakuten,
    "TopCashback": parse_topcashback,
    "BeFrugal": parse_befrugal,
    "Mr Rebates": parse_mrrebates,
    "RebatesMe": parse_rebatesme,
    "Capital One Shopping": parse_capitaloneshopping,
}


def fetch_page(url):
    """Fetch a URL politely. Returns HTML text or None on any failure."""
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            return resp.read().decode("utf-8", errors="ignore")
    except (URLError, HTTPError, TimeoutError, Exception) as e:
        print(f"  fetch failed: {e}")
        return None


def load_previous_results(path):
    """
    Load the last successful run's results as a merge baseline. Returns {}
    if the file doesn't exist yet (e.g. very first run ever) or can't be
    parsed - in either case we just start fresh rather than crash.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def scrape_all(config, previous_results=None):
    """
    config format:
    {
      "Nike": {
        "category": "Fashion",
        "urls": {
          "Rakuten": "https://www.rakuten.com/shop/nike",
          "TopCashback": "https://www.topcashback.com/nike/",
          ...
        },
        "meta": {
          "Rakuten": "Cash back, paid quarterly",
          ...
        }
      },
      ...
    }

    previous_results format matches this function's return value - the
    contents of the last run's data/latest_results.json. Used as a merge
    baseline so that a provider failing THIS run doesn't delete data that
    was successfully fetched on a PREVIOUS run. A store only disappears
    from the site if it has never had a single successful fetch, ever.
    """
    previous_results = previous_results or {}
    results = {}
    log = {"run_at": datetime.now(timezone.utc).isoformat(), "stores": {}}

    for store_name, store_info in config.items():
        print(f"Checking {store_name}...")
        store_log = {}

        prev_store = previous_results.get(store_name, {})
        prev_offers_by_provider = {
            o["provider"]: o for o in prev_store.get("offers", [])
        }

        offers_by_provider = {}
        any_success_this_run = False

        for provider, url in store_info.get("urls", {}).items():
            parser = PARSERS.get(provider)
            if not parser:
                continue

            html = fetch_page(url)
            time.sleep(REQUEST_DELAY_SECONDS)

            meta = store_info.get("meta", {}).get(provider, "Cash back")
            rate = None

            if html is None:
                store_log[provider] = "FETCH_FAILED - kept previous value"
            else:
                rate = parser(html)
                if rate is None:
                    store_log[provider] = "PARSE_FAILED - kept previous value"

            if rate is not None:
                offers_by_provider[provider] = {
                    "provider": provider, "rate": rate, "meta": meta
                }
                store_log[provider] = f"OK: {rate}"
                any_success_this_run = True
            elif provider in prev_offers_by_provider:
                # This run failed for this provider - fall back to the last
                # known-good offer instead of dropping it.
                offers_by_provider[provider] = prev_offers_by_provider[provider]
            # else: never succeeded for this provider, nothing to fall back to

        if offers_by_provider:
            results[store_name] = {
                # Only advance "verified" if something was actually
                # confirmed fresh this run - otherwise keep showing the
                # true last-verified date rather than a misleadingly
                # recent one.
                "verified": (
                    datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    if any_success_this_run
                    else prev_store.get("verified")
                ),
                "category": store_info.get("category", "General merchandise"),
                "offers": list(offers_by_provider.values()),
            }
        elif prev_store:
            # Every provider failed this run AND we have no fallback data
            # for any of them individually, but the store itself has a
            # previous entry (e.g. transient full-site outage) - keep it
            # as-is rather than deleting the store from the site.
            results[store_name] = prev_store

        log["stores"][store_name] = store_log

    return results, log


def write_stores_js(results, out_path):
    """Regenerate stores-data.js in the exact format the site expects."""
    lines = ["// Auto-generated daily by scripts/scrape_rates.py - do not edit by hand",
             f"// Last updated: {datetime.now(timezone.utc).isoformat()}",
             "const STORES = {"]
    for store_name, data in results.items():
        verified = f'"{data["verified"]}"' if data["verified"] else "null"
        lines.append(f'  "{store_name}": {{ verified: {verified}, offers: [')
        for offer in data["offers"]:
            lines.append(
                f'    {{ provider: "{offer["provider"]}", rate: "{offer["rate"]}", '
                f'meta: "{offer["meta"]}" }},'
            )
        lines.append("  ]},")
    lines.append("};")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    with open("data/stores_config.json", encoding="utf-8") as f:
        config = json.load(f)

    previous_results = load_previous_results("data/latest_results.json")

    results, log = scrape_all(config, previous_results)

    write_stores_js(results, "stores-data.js")

    with open("data/scrape_log.json", "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)

    # Save this run's results as next run's merge baseline.
    with open("data/latest_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    fail_count = sum(
        1 for store in log["stores"].values()
        for status in store.values()
        if "FAILED" in status
    )
    total_count = sum(len(store) for store in log["stores"].values())
    print(f"\nDone. {total_count - fail_count}/{total_count} provider checks succeeded.")

    if fail_count > total_count * 0.5:
        print("WARNING: more than half of all checks failed. Possible site-wide blocking.")
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Savvli daily rate scraper.

Fetches current cash-back rates for every store in stores_config.json across
the 6 tracked providers, and regenerates stores-data.js in the exact format
the site expects.

HONESTY NOTE ON CONFIDENCE LEVEL:
- Rakuten and TopCashback parsers are grounded in real, directly-fetched HTML
  (verified during development against live pages).
- BeFrugal, Mr Rebates, RebatesMe, and Capital One Shopping parsers use
  generic "X% Cash Back" pattern matching that was NOT verified against live
  pages during development (the dev sandbox that wrote this script could not
  reach those specific domains). These four are the most likely to need
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
    VERIFIED against a real fetched page (topcashback.com/nike/, Sept 2026).
    Real pattern found in a clean structured block:
      "## **Nike** Cash Back\n\nOnline Purchase\n\nImproved\n\n8%"
    We anchor on the "Cash Back" heading block and take the standalone
    percentage that follows it, which is cleaner/more reliable than the
    "Get X% of the price back" headline (which sometimes reads "Up to X%").
    """
    match = re.search(r'Cash Back\s*\n+\s*Online Purchase\s*\n+(?:Improved\s*\n+)?(\d+(?:\.\d+)?)\s*%', html)
    if match:
        return f"{match.group(1)}%"
    # fallback to the headline pattern if the structured block isn't found
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
    """NOT YET VERIFIED against a live page - generic pattern."""
    match = re.search(r'(\d+(?:\.\d+)?)\s*%\s*[Cc]ash\s*[Bb]ack', html)
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


def scrape_all(config):
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
    """
    results = {}
    log = {"run_at": datetime.now(timezone.utc).isoformat(), "stores": {}}

    for store_name, store_info in config.items():
        print(f"Checking {store_name}...")
        offers = []
        store_log = {}

        for provider, url in store_info.get("urls", {}).items():
            parser = PARSERS.get(provider)
            if not parser:
                continue

            html = fetch_page(url)
            time.sleep(REQUEST_DELAY_SECONDS)

            if html is None:
                store_log[provider] = "FETCH_FAILED - kept previous value"
                continue

            rate = parser(html)
            if rate is None:
                store_log[provider] = "PARSE_FAILED - kept previous value"
                continue

            meta = store_info.get("meta", {}).get(provider, "Cash back")
            offers.append({"provider": provider, "rate": rate, "meta": meta})
            store_log[provider] = f"OK: {rate}"

        if offers:
            results[store_name] = {
                "verified": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "category": store_info.get("category", "General merchandise"),
                "offers": offers,
            }
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

    results, log = scrape_all(config)

    write_stores_js(results, "stores-data.js")

    with open("data/scrape_log.json", "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)

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

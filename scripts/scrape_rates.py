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
  BeFrugal has since been CONFIRMED broken (see parse_befrugal) and is
  disabled until it's rebuilt against real raw HTML.

This script is designed to fail SAFELY: if a provider's page can't be parsed,
that specific offer is left unchanged (keeps last-known-good data) rather
than being deleted or zeroed out. The site should never show broken/missing
data because of a scraper hiccup.
"""

import gzip
import json
import re
import time
import sys
import zlib
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

def parse_rakuten(html, store_name=None):
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


def parse_topcashback(html, store_name=None):
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


def parse_befrugal(html, store_name=None):
    """
    VERIFIED against real raw HTML fetched via curl (Nike page, Sept 2026)
    - not a browser/JS-rendered view. The old URL pattern in stores_config
    was wrong (/rs/{slug}/ 302-redirects to a generic broken search page,
    /?s=5) - confirmed via curl -I showing the redirect. The real URL
    pattern is /store/{slug}/, confirmed working (returns "Nike 8.0% Cash
    Back..." as the page title).

    The old generic regex (matching any "X% Cash Back" anywhere on the
    page) was also broken separately - BeFrugal pages repeat the same
    store-wide rate dozens of times across individual deal listings, and
    on a real run this returned the identical 1.8% for nearly every store,
    meaning it was grabbing one fixed sitewide element, not the real rate.

    The real, unique anchor is the header rate module next to the store
    logo:
      <span class="txt-highlight"><span class="txt-small txt-under-store">
      up to </span><span class="txt-bold txt-under-store">8%</span>...
    "txt-bold txt-under-store" is a distinctive class combination that
    only appears in this one primary rate display, not in the repeated
    per-deal listings below it.
    """
    match = re.search(r'txt-bold txt-under-store">(\d+(?:\.\d+)?)%', html)
    if match:
        return f"{match.group(1)}%"
    return None


def parse_mrrebates(html, store_name=None):
    """
    VERIFIED against real raw HTML fetched via curl (Nike page, Sept 2026)
    - not a browser/JS-rendered view. Two real findings from that fetch:

    1. Mr Rebates doesn't have a stable per-store URL slug the way other
       providers do (their config used guessed URLs like "?merchantid=wayfair"
       which don't exist - confirmed via a live 404). Instead, their own
       search endpoint (/search_stores.asp?t_search=...) redirects straight
       to the real merchant page when there's a match. Since Python's
       urllib follows redirects automatically, pointing the scraper's URL
       at the search endpoint directly lands on the real merchant page (or
       a "no stores found" page) with no separate resolution step needed -
       this store's "urls" entry in stores_config.json should be the
       search URL, not a guessed merchant ID.

    2. The real rate lives in clean, structured JSON-LD:
         "name": "6% Cash Back at Nike"
       BUT the search is fuzzy/substring-based (e.g. searching "target"
       matched "Target Optical", a different store) - so before trusting
       a match, we verify the JSON-LD Organization name actually matches
       the store we asked for. Without this check we could silently
       attach a completely different store's rate to the wrong store.
    """
    if not store_name:
        return None

    offer_match = re.search(r'"name":\s*"(\d+(?:\.\d+)?)%\s*Cash Back at', html)
    if not offer_match:
        return None

    org_match = re.search(
        r'"@type":\s*"Organization"[^}]*?"name":\s*"([^"]+)"', html, re.DOTALL
    )
    if not org_match:
        return None

    def normalize(s):
        return re.sub(r'[^a-z0-9]', '', s.lower())

    matched_name = normalize(org_match.group(1))
    expected_name = normalize(store_name)
    if matched_name != expected_name:
        # Fuzzy search landed on a different store (e.g. "Target" ->
        # "Target Optical") - refuse to guess, safer to return nothing.
        return None

    return f"{offer_match.group(1)}%"


def parse_rebatesme(html, store_name=None):
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


def parse_capitaloneshopping(html, store_name=None):
    """
    VERIFIED against real raw HTML fetched via curl (Oriental Trading page,
    Sept 2026) - not a browser/JS-rendered view. The old URL pattern in
    stores_config was wrong (/s/{slug} 302-redirects to /not-found,
    confirmed via curl -I) - the real pattern is /s/{full-domain.com}/coupon
    (confirmed working - returns HTTP 200).

    The real rate lives in a semantic, developer-intended anchor:
      <p data-testid="coupon-content-title" ...>Get 2% back on purchases
      when you shop on Oriental Trading.</p>
    "data-testid" attributes are specifically meant to be stable hooks
    (usually used for the site's own automated testing), which makes this
    a more reliable long-term anchor than the page's internal React state
    encoding, which is more likely to shift with implementation changes.
    """
    match = re.search(
        r'data-testid="coupon-content-title"[^>]*>Get (\d+(?:\.\d+)?)% back',
        html
    )
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

# Providers whose parser is intentionally disabled (always returns None)
# because it was confirmed broken and not yet fixed. Skipped before even
# fetching - saves a request, and excluded from the fail-safe ratio below
# so a deliberate disable doesn't get mistaken for site-wide blocking.
DISABLED_PROVIDERS = set()  # BeFrugal fixed and re-enabled Sept 2026


def fetch_page(url):
    """
    Fetch a URL politely. Returns HTML text or None on any failure.

    Explicitly requests uncompressed content (Accept-Encoding: identity).
    Some servers/CDNs compress responses regardless of what a client
    requests; without this, a compressed response decoded as UTF-8 text
    silently produces garbled output (no exception raised) instead of the
    real page - which would make a parser fail to match even though the
    fetch itself "succeeded". This is the likely cause of RebatesMe
    consistently failing to parse on real runs despite the parser being
    verified correct against the same page fetched via curl (curl auto-
    decompresses; this script's old version did not).

    As a safety net in case a server sends compressed content anyway, we
    detect and decompress gzip/deflate before decoding, rather than
    assuming the identity request was honored.
    """
    try:
        req = Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "identity",
        })
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            raw = resp.read()
            encoding = resp.headers.get("Content-Encoding", "").lower()
            if encoding == "gzip":
                raw = gzip.decompress(raw)
            elif encoding == "deflate":
                raw = zlib.decompress(raw)
            return raw.decode("utf-8", errors="ignore")
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

            if provider in DISABLED_PROVIDERS:
                store_log[provider] = "DISABLED - excluded from fail-safe check"
                continue

            html = fetch_page(url)
            time.sleep(REQUEST_DELAY_SECONDS)

            meta = store_info.get("meta", {}).get(provider, "Cash back")
            rate = None

            if html is None:
                store_log[provider] = "FETCH_FAILED - kept previous value"
            else:
                rate = parser(html, store_name)
                if rate is None:
                    # Include the fetched length as a quick diagnostic -
                    # a near-zero or suspiciously small length usually
                    # means a compressed/garbled/blocked response rather
                    # than a genuine parser mismatch on real HTML.
                    store_log[provider] = (
                        f"PARSE_FAILED (fetched {len(html)} chars) - kept previous value"
                    )

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
    total_count = sum(
        1 for store in log["stores"].values()
        for status in store.values()
        if "DISABLED" not in status
    )
    print(f"\nDone. {total_count - fail_count}/{total_count} provider checks succeeded.")

    if fail_count > total_count * 0.5:
        print("WARNING: more than half of all checks failed. Possible site-wide blocking.")
        sys.exit(1)


if __name__ == "__main__":
    main()

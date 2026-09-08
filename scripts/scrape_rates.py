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
    at the top of the page, before the per-product listings

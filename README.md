# Savvli Daily Rate Scraper

Automatically checks cash-back rates once a day and regenerates `stores-data.js`,
which the live site loads instead of a hardcoded data block. GitHub Actions runs
this on a schedule and commits the update; Netlify picks up the commit and
redeploys automatically.

## Honest status before you rely on this

**Verified, tested against real fetched pages:**
- Rakuten parser — confirmed correct against a real fetch of rakuten.com/shop/nike
- TopCashback parser — confirmed correct against a real fetch of topcashback.com/nike/

**Not yet verified — will likely need debugging on the first real run:**
- BeFrugal, Mr Rebates, RebatesMe, Capital One Shopping parsers use generic
  "X% Cash Back" pattern matching. I could not reach these domains from my
  development sandbox to check them against real pages.
- **All 336 URLs in `data/stores_config.json` are best-guess slug patterns**
  (e.g. `topcashback.com/nike/`), generated from the one Nike example that
  worked. Most have not been checked individually. Some will 404 or point to
  the wrong page — that's expected on a first run, not a sign something is
  fundamentally broken.

## Before turning this on for real

1. Run it manually once via the "Run workflow" button on the Actions tab
   (don't wait for the schedule).
2. Check `data/scrape_log.json` after that run — it records `OK`,
   `FETCH_FAILED`, or `PARSE_FAILED` for every single store/provider check.
3. Expect a real cleanup pass: fixing wrong URLs, especially for BeFrugal,
   Mr Rebates, RebatesMe, and Capital One Shopping.
4. The scraper is deliberately conservative: any failed check just keeps the
   previous value rather than deleting it or writing garbage. Bad URLs won't
   break the site, they'll just mean that one rate goes stale until fixed.

## How it fits into the site

1. `savvli-ui.html` needs one change: replace the inline `const STORES = {...}`
   block with `<script src="stores-data.js"></script>` — I have not made this
   change to the live site yet, since it should happen alongside actually
   turning this scraper on, not before.
2. Move this whole folder's contents into the Savvli GitHub repo root.
3. Connect that repo to Netlify for auto-deploy on push (this was already a
   pending item from earlier in the project — this is what finally needs it).
4. The Action needs `contents: write` permission to commit back to the repo,
   which is already set in the workflow file, but double check your repo's
   Settings → Actions → General → Workflow permissions allows this.

## Files

- `scripts/scrape_rates.py` — the scraper itself
- `data/stores_config.json` — store name → provider URLs + payout metadata
- `data/scrape_log.json` — generated after each run, one line per check
- `.github/workflows/daily-rate-check.yml` — the schedule
- `stores-data.js` — generated output, this is what the site actually loads

## Risk reminders (from our earlier conversation)

- Runs once daily, deliberately spaced 3 seconds between requests — not
  aggressive, but still real automated traffic against sites whose ToS likely
  restrict this. Going in eyes-open, not naive.
- Treat Rakuten with extra care given the earlier rejected publisher
  application — consider leaving it out of automation and re-verifying it
  manually on occasion instead, if you want to protect a future re-application.
- If a provider starts blocking the bot, `scrape_log.json` will show a wall of
  `FETCH_FAILED` for that provider specifically — that's your signal to slow
  down or stop hitting that one, not push harder.

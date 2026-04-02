# YTJobs → monday.com Sync

Scrapes YTJobs talent search pages and syncs new talent records to your monday.com board.

## What this repo includes

- `sync_ytjobs_to_monday.py`: Scraper + monday sync script.
- `.github/workflows/ytjobs-sync.yml`: GitHub Actions workflow (manual + every 6 hours).
- `.env.example`: Local environment variable template.

## Setup

1. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python -m playwright install chromium
   ```

2. Copy `.env.example` to `.env` and fill values:
   ```bash
   cp .env.example .env
   ```

3. Run initial full backfill (one-time):
   ```bash
   python sync_ytjobs_to_monday.py --no-incremental --max-pages 200
   ```

4. Run dry-run first:
   ```bash
   python sync_ytjobs_to_monday.py --dry-run
   ```

5. Run live sync:
   ```bash
   python sync_ytjobs_to_monday.py
   ```

6. Optional incremental mode (recommended for continuous runs):
   ```bash
   python sync_ytjobs_to_monday.py --incremental --stop-after-known-pages 2
   ```

## GitHub Actions secrets to add

- `MONDAY_API_TOKEN`
- `MONDAY_BOARD_ID` (e.g. `18406893281`)
- `MONDAY_GROUP_AVAILABLE` (e.g. `topics`)
- `MONDAY_GROUP_UNAVAILABLE` (e.g. `group_mm20bark`)
- Optional: `MAX_PAGES`
- Optional: `INCREMENTAL_MODE`, `STOP_AFTER_KNOWN_PAGES`, `INCLUDE_UNAVAILABLE`

## Important

- Do **not** hardcode your monday API key in code.
- Use secrets (`.env` locally, GitHub Actions secrets in CI).
- The YTJobs site may change HTML/API shape; scraper heuristics are designed to be resilient but may require selector updates later.
- Availability routing:
  - `open_for_work == true` → Available group.
  - `open_for_work == false` → Unavailable group.
  - unknown availability defaults to Available unless `INCLUDE_UNAVAILABLE=false` (then unknown/false are skipped).

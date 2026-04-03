#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import re
from dataclasses import dataclass, asdict
from typing import Any

import httpx
from playwright.async_api import async_playwright, Page

YTJOBS_BASE = "https://ytjobs.co"
SEARCH_URL = "https://ytjobs.co/talent/search/all_categories?page={page}"

# =========================
# CONFIG
# =========================

# Keep this as a GitHub secret / environment variable
MONDAY_API_TOKEN = os.getenv("MONDAY_API_TOKEN", "")

# Hardcoded monday config
MONDAY_BOARD_ID = 18406893281
MONDAY_GROUP_AVAILABLE = "topics"
MONDAY_GROUP_UNAVAILABLE = "group_mm20bark"

MONDAY_COLUMNS = {
    "linkedin": "text_mm20d7rp",
    "ytjobs_profile_link": "text_mm20a03h",
    "email": "text_mm2028sd",
    "years_of_experience": "numeric_mm20gyp",
    "priority": "color_mm204cwh",
    "open_for_work": "boolean_mm20xkyn",
    "creators_worked_with": "dropdown_mm20xxmt",
    "views": "numeric_mm20rjas",
}


@dataclass
class TalentRecord:
    name: str
    ytjobs_profile_link: str = ""
    linkedin: str = ""
    email: str = ""
    years_of_experience: float | None = None
    open_for_work: bool | None = None
    creators_worked_with: list[str] | None = None
    views: float | None = None
    priority: str | None = None


def _to_absolute(url: str) -> str:
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return f"{YTJOBS_BASE}{url}"
    return url


def _extract_num(text: str) -> float | None:
    if not text:
        return None
    cleaned = text.lower().replace(",", "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*([kmb])?", cleaned)
    if not m:
        return None
    num = float(m.group(1))
    suffix = m.group(2)
    if suffix == "k":
        num *= 1_000
    elif suffix == "m":
        num *= 1_000_000
    elif suffix == "b":
        num *= 1_000_000_000
    return num


def _walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item)


def _normalize_priority(rec: TalentRecord) -> str:
    score = 0

    if rec.email:
        score += 1
    if rec.linkedin:
        score += 1
    if rec.open_for_work is True:
        score += 1
    if rec.years_of_experience is not None and rec.years_of_experience >= 5:
        score += 1

    if score >= 4:
        return "Critical ⚠️️"
    if score == 3:
        return "High"
    if score == 2:
        return "Medium"
    return "Low"


def _from_dict(d: dict[str, Any]) -> TalentRecord | None:
    keys = {k.lower(): k for k in d.keys()}
    name_key = next((keys[k] for k in keys if k in {"name", "full_name", "talent_name"}), None)
    profile_key = next(
        (
            keys[k]
            for k in keys
            if ("profile" in k and "link" in k)
            or ("url" in k and "ytjobs" in str(d.get(keys[k], "")).lower())
        ),
        None,
    )

    if not name_key:
        return None

    name = str(d.get(name_key, "")).strip()
    if not name:
        return None

    profile = ""
    if profile_key:
        profile = str(d.get(profile_key) or "")

    linkedin = ""
    for k, v in d.items():
        if "linkedin" in k.lower() and isinstance(v, str):
            linkedin = v.strip()
            break

    email = ""
    for k, v in d.items():
        if "email" in k.lower() and isinstance(v, str):
            email = v.strip()
            break

    years = None
    for k, v in d.items():
        lk = k.lower()
        if "year" in lk and "exp" in lk:
            years = _extract_num(str(v))
            break

    open_for_work = None
    for k, v in d.items():
        lk = k.lower()
        if "open" in lk and "work" in lk and isinstance(v, bool):
            open_for_work = v
            break

    views = None
    for k, v in d.items():
        if "view" in k.lower():
            views = _extract_num(str(v))
            break

    creators = None
    for k, v in d.items():
        if "creator" in k.lower() and isinstance(v, list):
            creators = [str(x).strip() for x in v if x]
            break

    rec = TalentRecord(
        name=name,
        ytjobs_profile_link=_to_absolute(profile),
        linkedin=linkedin,
        email=email,
        years_of_experience=years,
        open_for_work=open_for_work,
        creators_worked_with=creators,
        views=views,
    )
    rec.priority = _normalize_priority(rec)
    return rec


async def _scrape_page(page: Page, page_no: int) -> list[TalentRecord]:
    url = SEARCH_URL.format(page=page_no)
    candidates: list[TalentRecord] = []

    async def on_response(resp):
        ct = (resp.headers or {}).get("content-type", "")
        if "application/json" not in ct:
            return
        try:
            data = await resp.json()
        except Exception:
            return
        for node in _walk(data):
            rec = _from_dict(node)
            if rec:
                candidates.append(rec)

    page.on("response", on_response)
    await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
    await page.wait_for_timeout(3000)

    if not candidates:
        raw = await page.evaluate(
            """
            () => {
              const cards = Array.from(document.querySelectorAll('a[href*="/talent/"]'));
              return cards.map(a => {
                const wrap = a.closest('article, li, div') || a.parentElement;
                const txt = wrap ? wrap.innerText : a.innerText;
                return {href: a.getAttribute('href') || '', text: txt || ''};
              });
            }
            """
        )
        for row in raw:
            text = row.get("text", "")
            href = _to_absolute(row.get("href", ""))
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if not lines:
                continue

            name = lines[0]
            linkedin_match = re.search(r"https?://(?:www\.)?linkedin\.com/[^\s]+", text, re.I)
            email_match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)

            rec = TalentRecord(
                name=name,
                ytjobs_profile_link=href,
                linkedin=linkedin_match.group(0) if linkedin_match else "",
                email=email_match.group(0) if email_match else "",
                years_of_experience=_extract_num(text) if "year" in text.lower() else None,
                open_for_work=True if "open for work" in text.lower() else None,
                views=_extract_num(text) if "view" in text.lower() else None,
            )
            rec.priority = _normalize_priority(rec)
            candidates.append(rec)

    unique: dict[str, TalentRecord] = {}
    for rec in candidates:
        key = rec.ytjobs_profile_link or rec.name.lower()
        if key and key not in unique:
            unique[key] = rec

    page.remove_listener("response", on_response)
    return list(unique.values())


class MondayClient:
    def __init__(self, token: str):
        self.client = httpx.Client(
            base_url="https://api.monday.com/v2",
            headers={"Authorization": token, "Content-Type": "application/json"},
            timeout=30.0,
        )

    def _post(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"query": query, "variables": variables or {}}
        r = self.client.post("", json=payload)
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            raise RuntimeError(f"monday API error: {body['errors']}")
        return body["data"]

    def get_existing_profile_links(self, board_id: int, ytjobs_column_id: str) -> set[str]:
        query = """
        query ($board_id: [ID!], $cursor: String) {
          boards (ids: $board_id) {
            items_page(limit: 500, cursor: $cursor) {
              cursor
              items {
                column_values(ids: ["YTJOBS_COL"]) {
                  text
                }
              }
            }
          }
        }
        """.replace("YTJOBS_COL", ytjobs_column_id)

        cursor = None
        results: set[str] = set()

        while True:
            data = self._post(query, {"board_id": board_id, "cursor": cursor})
            page = data["boards"][0]["items_page"]

            for item in page["items"]:
                if not item["column_values"]:
                    continue
                val = item["column_values"][0].get("text")
                if val:
                    results.add(val.strip())

            cursor = page.get("cursor")
            if not cursor:
                break

        return results

    def create_item(self, board_id: int, group_id: str, item_name: str, column_values: dict[str, Any]) -> None:
        query = """
        mutation ($board_id: ID!, $group_id: String!, $item_name: String!, $column_values: JSON!) {
          create_item(board_id: $board_id, group_id: $group_id, item_name: $item_name, column_values: $column_values) {
            id
          }
        }
        """
        self._post(
            query,
            {
                "board_id": str(board_id),
                "group_id": group_id,
                "item_name": item_name,
                "column_values": json.dumps(column_values),
            },
        )


def build_column_values(rec: TalentRecord, col: dict[str, str]) -> dict[str, Any]:
    vals: dict[str, Any] = {
        col["linkedin"]: rec.linkedin,
        col["ytjobs_profile_link"]: rec.ytjobs_profile_link,
        col["email"]: rec.email,
    }

    if rec.years_of_experience is not None:
        vals[col["years_of_experience"]] = rec.years_of_experience

    if rec.open_for_work is not None:
        vals[col["open_for_work"]] = rec.open_for_work

    if rec.creators_worked_with:
        vals[col["creators_worked_with"]] = {"labels": rec.creators_worked_with}

    if rec.views is not None:
        vals[col["views"]] = rec.views

    if rec.priority:
        vals[col["priority"]] = {"label": rec.priority}

    return vals


async def scrape(
    max_pages: int,
    headless: bool,
    known_profile_links: set[str] | None = None,
    incremental_mode: bool = False,
    stop_after_known_pages: int = 2,
) -> list[TalentRecord]:
    known_profile_links = known_profile_links or set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        all_records: list[TalentRecord] = []
        consecutive_fully_known_pages = 0

        for page_no in range(1, max_pages + 1):
            records = await _scrape_page(page, page_no)
            if not records:
                break

            all_records.extend(records)

            if incremental_mode:
                unseen_on_page = [
                    rec
                    for rec in records
                    if rec.ytjobs_profile_link and rec.ytjobs_profile_link not in known_profile_links
                ]

                if unseen_on_page:
                    consecutive_fully_known_pages = 0
                else:
                    consecutive_fully_known_pages += 1

                if consecutive_fully_known_pages >= stop_after_known_pages:
                    break

        await browser.close()

    dedup: dict[str, TalentRecord] = {}
    for rec in all_records:
        key = rec.ytjobs_profile_link or rec.name.lower()
        if key and key not in dedup:
            dedup[key] = rec

    return list(dedup.values())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape YTJobs talent and sync to monday.com")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--incremental", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--stop-after-known-pages",
        type=int,
        default=2,
        help="Incremental mode: stop after this many consecutive pages with no unseen profiles.",
    )
    parser.add_argument(
        "--include-unavailable",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    token = MONDAY_API_TOKEN
    board_id = MONDAY_BOARD_ID
    group_available = MONDAY_GROUP_AVAILABLE
    group_unavailable = MONDAY_GROUP_UNAVAILABLE
    columns = MONDAY_COLUMNS

    if not token and not args.dry_run:
        raise RuntimeError("MONDAY_API_TOKEN is required unless --dry-run is enabled")

    monday = MondayClient(token) if token else None
    existing = set()

    if monday:
        existing = monday.get_existing_profile_links(board_id, columns["ytjobs_profile_link"])

    records = asyncio.run(
        scrape(
            args.max_pages,
            args.headless,
            known_profile_links=existing,
            incremental_mode=args.incremental,
            stop_after_known_pages=max(1, args.stop_after_known_pages),
        )
    )

    print(f"Scraped records: {len(records)}")

    if args.dry_run:
        for rec in records[:10]:
            print(json.dumps(asdict(rec), ensure_ascii=False))
        print("Dry run mode enabled; no monday updates sent.")
        return

    if not monday:
        raise RuntimeError("MONDAY_API_TOKEN is required unless --dry-run is enabled")

    created = 0
    skipped_existing = 0
    skipped_unavailable = 0
    failed = 0

    for rec in records:
        if not rec.ytjobs_profile_link or rec.ytjobs_profile_link in existing:
            skipped_existing += 1
            continue

        if not args.include_unavailable and rec.open_for_work is not True:
            skipped_unavailable += 1
            continue

        group = group_available if rec.open_for_work is not False else group_unavailable
        values = build_column_values(rec, columns)

        try:
            monday.create_item(board_id, group, rec.name, values)
            existing.add(rec.ytjobs_profile_link)
            created += 1
            print(f"Created: {rec.name}")
        except Exception as e:
            failed += 1
            print(f"Failed to create item for {rec.name}: {e}")

    print(f"Created monday items: {created}")
    print(f"Skipped existing: {skipped_existing}")
    print(f"Skipped unavailable: {skipped_unavailable}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    main()

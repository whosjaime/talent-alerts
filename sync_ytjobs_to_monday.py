#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, asdict

import httpx
from playwright.async_api import async_playwright, Page

YTJOBS_BASE = "https://ytjobs.co"
SEARCH_URL = "https://ytjobs.co/talent/search/all_categories?page={page}"

MONDAY_API_TOKEN = os.getenv("MONDAY_API_TOKEN", "")
MONDAY_BOARD_ID = int(os.getenv("MONDAY_BOARD_ID", "18406893281"))
MONDAY_DEFAULT_GROUP_ID = os.getenv("MONDAY_DEFAULT_GROUP_ID", "topics")

# Role groups can be overridden by environment variables.
# Leave a value blank to route that role to MONDAY_DEFAULT_GROUP_ID.
ROLE_TO_GROUP_ID = {
    "Channel Manager": os.getenv("MONDAY_GROUP_CHANNEL_MANAGER", "group_mm1v3xqy"),
    "Strategist": os.getenv("MONDAY_GROUP_STRATEGIST", "group_mm1vazt5"),
    "Producer": os.getenv("MONDAY_GROUP_PRODUCER", "group_mm1vm88z"),
    "Creative Director": os.getenv("MONDAY_GROUP_CREATIVE_DIRECTOR", "group_mm1vqh7r"),
    "Lead Editor": os.getenv("MONDAY_GROUP_LEAD_EDITOR", "group_mm1vfz8e"),
    "Long-Form Editor": os.getenv("MONDAY_GROUP_LONG_FORM_EDITOR", "group_mm1vm5fm"),
    "Short-Form Editor": os.getenv("MONDAY_GROUP_SHORT_FORM_EDITOR", "group_mm1vxj09"),
    "Scriptwriter": os.getenv("MONDAY_GROUP_SCRIPTWRITER", "group_mm1vsj1h"),
    "Personal Assistant": os.getenv("MONDAY_GROUP_PERSONAL_ASSISTANT", "group_mm1vscae"),
    "Engineer": os.getenv("MONDAY_GROUP_ENGINEER", "group_mm1vrk6s"),
    "Developer": os.getenv("MONDAY_GROUP_DEVELOPER", "group_mm1vv541"),
    "Graphic Designer": os.getenv("MONDAY_GROUP_GRAPHIC_DESIGNER", "group_mm1v5svb"),
    "Operations": os.getenv("MONDAY_GROUP_OPERATIONS", "group_mm1vb85k"),
    "Thumbnail Designer": os.getenv("MONDAY_GROUP_THUMBNAIL_DESIGNER", "group_mm1vavxj"),
    "Animator": os.getenv("MONDAY_GROUP_ANIMATOR", "topics"),
    "Content Creator": os.getenv("MONDAY_GROUP_CONTENT_CREATOR", "group_mm1vpgyz"),
}

MONDAY_COLUMNS = {
    "linkedin": "text_mm20d7rp",
    "ytjobs_profile_link": "text_mm20a03h",
    "email": "text_mm2028sd",
    "years_of_experience": "numeric_mm20gyp",
    "priority": "color_mm204cwh",
    "open_for_work": "boolean_mm20xkyn",
    "creators_worked_with": "dropdown_mm20xxmt",
    "views": "numeric_mm20rjas",
    "job_role": "dropdown_mm22xt4g",
}

# Only these can be written to the "Creators Worked With" dropdown
VALID_CREATOR_LABELS = {
    "Editors",
    "Thumbnail Designers",
    "Animators",
    "Scriptwriters",
}

ROLE_ALIASES = {
    "channel manager": "Channel Manager",
    "strategist": "Strategist",
    "content strategist": "Strategist",
    "producer": "Producer",
    "creative director": "Creative Director",
    "editor": "Long-Form Editor",
    "video editor": "Long-Form Editor",
    "lead editor": "Lead Editor",
    "short-form editor": "Short-Form Editor",
    "short form editor": "Short-Form Editor",
    "long-form editor": "Long-Form Editor",
    "long form editor": "Long-Form Editor",
    "scriptwriter": "Scriptwriter",
    "script writer": "Scriptwriter",
    "personal assistant": "Personal Assistant",
    "assistant": "Personal Assistant",
    "engineer": "Engineer",
    "developer": "Developer",
    "graphic designer": "Graphic Designer",
    "designer": "Graphic Designer",
    "operations": "Operations",
    "ops": "Operations",
    "thumbnail designer": "Thumbnail Designer",
    "animator": "Animator",
    "content creator": "Content Creator",
    "creator": "Content Creator",
}

JUNK_NAME_PATTERNS = [
    r"^csrf",
    r"^xsrf",
    r"^visitor_",
    r"^ph_",
    r"^ytjobs_session$",
    r"^yt-",
    r"^_ga",
    r"^_fbp$",
    r"^fr$",
    r"^nid$",
    r"^ysc$",
    r"^landing$",
    r"^test_cookie$",
    r"^lastexternalreferrer",
    r"^termly",
    r"^forum_sort_type$",
    r"^page-has-been-force-refreshed$",
]

PROFILE_PATTERNS = [
    re.compile(r"^https://ytjobs\.co/talent/[^/?#]+/?$", re.I),
    re.compile(r"^https://ytjobs\.co/profile/[^/?#]+/?$", re.I),
]


@dataclass
class TalentRecord:
    name: str
    ytjobs_profile_link: str
    linkedin: str = ""
    email: str = ""
    years_of_experience: float | None = None
    open_for_work: bool | None = None
    creators_worked_with: list[str] | None = None
    creator_summary: str = ""
    views: float | None = None
    priority: str | None = None
    job_role: str | None = None


def _to_absolute(url: str) -> str:
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return f"{YTJOBS_BASE}{url}"
    return url


def _normalize_profile_link(url: str) -> str:
    absolute = _to_absolute(url).strip()
    return absolute.rstrip("/")


def _looks_like_valid_profile_url(url: str) -> bool:
    absolute = _normalize_profile_link(url)
    return any(p.match(absolute) for p in PROFILE_PATTERNS)


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


def _is_junk_name(name: str) -> bool:
    if not name:
        return True
    lowered = name.strip().lower()
    return any(re.search(p, lowered) for p in JUNK_NAME_PATTERNS)


def _normalize_role(raw: str | None) -> str | None:
    if not raw:
        return None
    role = re.sub(r"\s+", " ", raw.strip().lower())
    if role in ROLE_ALIASES:
        return ROLE_ALIASES[role]
    for alias, canonical in ROLE_ALIASES.items():
        if alias in role:
            return canonical
    return None


def _detect_role_from_text(text: str) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    for alias, canonical in sorted(ROLE_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
        if alias in lowered:
            return canonical
    return None


def _detect_open_to_work(text: str) -> bool | None:
    if not text:
        return None
    t = text.lower()
    if any(x in t for x in ["not available", "unavailable", "not open for work"]):
        return False
    if any(x in t for x in ["open to work", "open for work", "hire me", "available for work"]):
        return True
    return None


def _extract_views(text: str) -> float | None:
    if not text:
        return None
    patterns = [
        r"(\d+(?:\.\d+)?)\s*([kmb])?\s+views",
        r"views\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*([kmb])?",
    ]
    lowered = text.lower()
    for pattern in patterns:
        m = re.search(pattern, lowered, re.I)
        if m:
            return _extract_num(f"{m.group(1)}{m.group(2) or ''}")
    return None


def _extract_creator_summary(text: str) -> str:
    if not text:
        return ""
    patterns = [
        r"(worked with[^\n]+)",
        r"(clients?[^\n]+)",
        r"(creators? worked with[^\n]+)",
        r"(experience[^\n]+)",
        r"(past work[^\n]+)",
    ]
    chunks = []
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I):
            chunk = m.group(1).strip()
            if chunk and chunk not in chunks:
                chunks.append(chunk)
    return " | ".join(chunks[:5])


def _extract_creators_dropdown(text: str) -> list[str] | None:
    if not text:
        return None
    lowered = text.lower()
    found = []
    mapping = {
        "editor": "Editors",
        "thumbnail designer": "Thumbnail Designers",
        "animator": "Animators",
        "scriptwriter": "Scriptwriters",
    }
    for needle, label in mapping.items():
        if needle in lowered and label not in found:
            found.append(label)
    return found or None


def _normalize_priority(rec: TalentRecord) -> str:
    if rec.open_for_work is True:
        return "Critical ⚠️️"

    score = 0
    if rec.email:
        score += 1
    if rec.linkedin:
        score += 1
    if rec.years_of_experience is not None and rec.years_of_experience >= 5:
        score += 1
    if rec.views is not None and rec.views >= 100000:
        score += 1

    if score >= 4:
        return "Critical ⚠️️"
    if score == 3:
        return "High"
    if score == 2:
        return "Medium"
    return "Low"


def _pick_profile_link(href: str, nested_links: list[str]) -> str:
    for candidate in [href] + (nested_links or []):
        absolute = _normalize_profile_link(candidate)
        if _looks_like_valid_profile_url(absolute):
            return absolute
    return ""


async def _scrape_directory_page(page: Page, page_no: int) -> list[TalentRecord]:
    url = SEARCH_URL.format(page=page_no)
    print(f"Scraping directory page {page_no}: {url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=90000)
    await page.wait_for_timeout(3000)

    raw = await page.evaluate(
        """
        () => {
          const els = Array.from(document.querySelectorAll('a, button'));
          const rows = [];

          for (const el of els) {
            const text = (el.innerText || el.textContent || '').trim();
            const href = el.tagName.toLowerCase() === 'a' ? (el.getAttribute('href') || '') : '';
            const wrap = el.closest('article, li, [class*="card"], [class*="profile"], [class*="talent"], div') || el.parentElement;
            const blockText = wrap ? (wrap.innerText || wrap.textContent || '') : text;
            const nestedLinks = wrap ? Array.from(wrap.querySelectorAll('a[href]')).map(a => a.getAttribute('href') || '') : [];

            const relevant =
              href.includes('/talent/') ||
              href.includes('/profile/') ||
              nestedLinks.some(x => x.includes('/talent/') || x.includes('/profile/')) ||
              /view profile/i.test(text) ||
              /view full profile/i.test(text);

            if (!relevant) continue;

            rows.push({
              text: blockText || '',
              href: href || '',
              nested_links: nestedLinks
            });
          }

          return rows;
        }
        """
    )

    dedup = {}
    for row in raw:
        text = (row.get("text", "") or "").strip()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            continue

        name = lines[0]
        if _is_junk_name(name):
            continue

        profile_link = _pick_profile_link(row.get("href", ""), row.get("nested_links", []))
        if not profile_link:
            continue

        linkedin_match = re.search(r"https?://(?:www\.)?linkedin\.com/[^\s]+", text, re.I)
        email_match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)

        rec = TalentRecord(
            name=name,
            ytjobs_profile_link=profile_link,
            linkedin=linkedin_match.group(0) if linkedin_match else "",
            email=email_match.group(0) if email_match else "",
            years_of_experience=_extract_num(text) if "year" in text.lower() else None,
            open_for_work=_detect_open_to_work(text),
            creators_worked_with=_extract_creators_dropdown(text),
            creator_summary=_extract_creator_summary(text),
            views=_extract_views(text),
            job_role=_detect_role_from_text(text),
        )
        rec.priority = _normalize_priority(rec)
        dedup[profile_link] = rec

    print(f"Directory page {page_no} valid records found: {len(dedup)}")
    return list(dedup.values())


async def _enrich_profile(page: Page, rec: TalentRecord) -> TalentRecord:
    try:
        await page.goto(rec.ytjobs_profile_link, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(1800)
    except Exception:
        return rec

    body_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    body_text = body_text or ""

    if not rec.job_role:
        rec.job_role = _detect_role_from_text(body_text)

    profile_views = _extract_views(body_text)
    if profile_views is not None:
        rec.views = profile_views

    if rec.open_for_work is None:
        rec.open_for_work = _detect_open_to_work(body_text)

    creators_dropdown = _extract_creators_dropdown(body_text)
    if creators_dropdown:
        rec.creators_worked_with = creators_dropdown

    creator_summary = _extract_creator_summary(body_text)
    if creator_summary:
        rec.creator_summary = creator_summary

    if rec.years_of_experience is None:
        m = re.search(r"(\d+(?:\.\d+)?)\s*\+?\s*years", body_text.lower())
        if m:
            rec.years_of_experience = _extract_num(m.group(1))

    if not rec.linkedin:
        m = re.search(r"https?://(?:www\.)?linkedin\.com/[^\s]+", body_text, re.I)
        if m:
            rec.linkedin = m.group(0)

    if not rec.email:
        m = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", body_text, re.I)
        if m:
            rec.email = m.group(0)

    rec.priority = _normalize_priority(rec)
    return rec


class MondayClient:
    def __init__(self, token: str):
        self.client = httpx.Client(
            base_url="https://api.monday.com/v2",
            headers={"Authorization": token, "Content-Type": "application/json"},
            timeout=30.0,
        )

    def _post(self, query: str, variables: dict | None = None) -> dict:
        r = self.client.post("", json={"query": query, "variables": variables or {}})
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
        results = set()
        while True:
            data = self._post(query, {"board_id": board_id, "cursor": cursor})
            page = data["boards"][0]["items_page"]
            for item in page["items"]:
                if not item["column_values"]:
                    continue
                val = item["column_values"][0].get("text")
                if val:
                    results.add(_normalize_profile_link(val))
            cursor = page.get("cursor")
            if not cursor:
                break
        return results

    def create_item(self, board_id: int, group_id: str, item_name: str, column_values: dict) -> None:
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


def build_column_values(rec: TalentRecord) -> dict:
    vals = {
        MONDAY_COLUMNS["linkedin"]: rec.linkedin,
        MONDAY_COLUMNS["ytjobs_profile_link"]: rec.ytjobs_profile_link,
        MONDAY_COLUMNS["email"]: rec.email,
    }

    if rec.years_of_experience is not None:
        vals[MONDAY_COLUMNS["years_of_experience"]] = rec.years_of_experience

    if rec.open_for_work is not None:
        vals[MONDAY_COLUMNS["open_for_work"]] = rec.open_for_work

    if rec.creators_worked_with:
        vals[MONDAY_COLUMNS["creators_worked_with"]] = {"labels": rec.creators_worked_with}

    if rec.views is not None:
        vals[MONDAY_COLUMNS["views"]] = rec.views

    if rec.priority:
        vals[MONDAY_COLUMNS["priority"]] = {"label": rec.priority}

    if rec.job_role:
        vals[MONDAY_COLUMNS["job_role"]] = {"labels": [rec.job_role]}

    return vals


async def scrape(max_pages: int, headless: bool) -> list[TalentRecord]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context()
        directory_page = await ctx.new_page()
        profile_page = await ctx.new_page()

        all_records = []

        for page_no in range(1, max_pages + 1):
            records = await _scrape_directory_page(directory_page, page_no)
            if not records:
                print(f"No valid records found on page {page_no}; stopping scrape.")
                break

            enriched = []
            for idx, rec in enumerate(records, start=1):
                print(f"  Enriching profile {idx}/{len(records)}: {rec.name}")
                enriched.append(await _enrich_profile(profile_page, rec))

            all_records.extend(enriched)

        await browser.close()

    dedup = {}
    for rec in all_records:
        if rec.ytjobs_profile_link:
            dedup[_normalize_profile_link(rec.ytjobs_profile_link)] = rec
    return list(dedup.values())


def parse_args() -> argparse.Namespace:
    env_max_pages = int(os.getenv("MAX_PAGES", "1000"))
    env_headless = os.getenv("HEADLESS", "true").strip().lower() not in {"0", "false", "no"}
    env_dry_run = os.getenv("DRY_RUN", "false").strip().lower() in {"1", "true", "yes"}

    parser = argparse.ArgumentParser(description="Scrape YTJobs talent and sync to monday.com")
    parser.add_argument("--max-pages", type=int, default=env_max_pages)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=env_headless)
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=env_dry_run)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not MONDAY_API_TOKEN and not args.dry_run:
        raise RuntimeError("MONDAY_API_TOKEN is required unless --dry-run is enabled")

    monday = MondayClient(MONDAY_API_TOKEN) if MONDAY_API_TOKEN else None
    existing = set()

    if monday:
        print("Loading existing monday YTJobs profile links...")
        existing = monday.get_existing_profile_links(MONDAY_BOARD_ID, MONDAY_COLUMNS["ytjobs_profile_link"])
        print(f"Existing monday links: {len(existing)}")

    records = asyncio.run(scrape(args.max_pages, args.headless))
    print(f"Total scraped valid records: {len(records)}")

    if args.dry_run:
        for rec in records[:25]:
            print(json.dumps(asdict(rec), ensure_ascii=False))
        print("Dry run mode enabled; no monday updates sent.")
        return

    created = 0
    skipped_existing = 0
    used_default_group = 0
    failed = 0

    for idx, rec in enumerate(records, start=1):
        if rec.ytjobs_profile_link in existing:
            skipped_existing += 1
            continue

        mapped_group_id = ROLE_TO_GROUP_ID.get(rec.job_role or "")
        group_id = mapped_group_id or MONDAY_DEFAULT_GROUP_ID
        if not mapped_group_id:
            used_default_group += 1
            print(
                f"[{idx}] Role not mapped; using default group '{MONDAY_DEFAULT_GROUP_ID}': "
                f"{rec.name} | role={rec.job_role}"
            )
        values = build_column_values(rec)

        print(
            f"[{idx}] Creating: {rec.name} | role={rec.job_role} | "
            f"group={group_id} | views={rec.views} | open_to_work={rec.open_for_work} | "
            f"creators_summary={rec.creator_summary}"
        )

        try:
            monday.create_item(MONDAY_BOARD_ID, group_id, rec.name, values)
            existing.add(rec.ytjobs_profile_link)
            created += 1
        except Exception as e:
            print(f"[{idx}] First attempt failed for {rec.name}: {e}")
            time.sleep(2)
            try:
                monday.create_item(MONDAY_BOARD_ID, group_id, rec.name, values)
                existing.add(rec.ytjobs_profile_link)
                created += 1
            except Exception as e2:
                failed += 1
                print(f"[{idx}] Failed permanently for {rec.name}: {e2}")

    print("========== FINAL SUMMARY ==========")
    print(f"Total scraped valid records: {len(records)}")
    print(f"Created monday items: {created}")
    print(f"Skipped existing: {skipped_existing}")
    print(f"Used default group for unmapped/unknown role: {used_default_group}")
    print(f"Failed: {failed}")

    if len(records) > 0 and created == 0:
        raise RuntimeError(
            "Scrape completed but 0 monday items were created. "
            "Extraction likely failed or every record was skipped."
        )


if __name__ == "__main__":
    main()

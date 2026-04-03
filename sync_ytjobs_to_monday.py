#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, asdict
from typing import Any

import httpx
from playwright.async_api import async_playwright, Page

YTJOBS_BASE = "https://ytjobs.co"
SEARCH_URL = "https://ytjobs.co/talent/search/all_categories?page={page}"

MONDAY_API_TOKEN = os.getenv("MONDAY_API_TOKEN", "")
MONDAY_BOARD_ID = 18406893281

ROLE_TO_GROUP_ID = {
    "Channel Manager": "topics",
    "Strategist": "group_mm20bark",
    "Producer": "group_mm22zy6t",
    "Creative Director": "group_mm22x74p",
    "Editor": "group_mm22v92w",
    "Scriptwriter": "group_mm22hc00",
    "Thumbnail Designer": "group_mm22jqd",
    "Animator": "group_mm22agrt",
}
DEFAULT_GROUP_ID = "group_mm20bark"

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

VALID_CREATOR_LABELS = {
    "Editors",
    "Thumbnail Designers",
    "Animators",
    "Scriptwriters",
}

VALID_JOB_ROLE_LABELS = {
    "Animator",
    "Channel Manager",
    "Content Creator",
    "Creative Director",
    "Developer",
    "Engineer",
    "Graphic Designer",
    "Motion Designer",
    "Operations Manager",
    "Producer",
    "Researcher",
    "Scriptwriter",
    "Short-Form Editor",
    "Long-Form Editor",
    "Social Media Manager",
    "Community Manager",
    "Podcast Editor",
    "Strategist",
    "Thumbnail Designer",
    "Automation Specialist",
    "Other",
    "Lead Editor",
    "Personal Assistant",
    "Assistant Director",
    "Brand Manager",
}

ROLE_ALIASES = {
    "video editor": "Editor",
    "editor": "Editor",
    "lead editor": "Lead Editor",
    "short form editor": "Short-Form Editor",
    "short-form editor": "Short-Form Editor",
    "long form editor": "Long-Form Editor",
    "long-form editor": "Long-Form Editor",
    "podcast editor": "Podcast Editor",
    "thumbnail designer": "Thumbnail Designer",
    "animator": "Animator",
    "scriptwriter": "Scriptwriter",
    "script writer": "Scriptwriter",
    "producer": "Producer",
    "creative director": "Creative Director",
    "channel manager": "Channel Manager",
    "strategist": "Strategist",
    "content strategist": "Strategist",
    "social media manager": "Social Media Manager",
    "community manager": "Community Manager",
    "operations manager": "Operations Manager",
    "motion designer": "Motion Designer",
    "graphic designer": "Graphic Designer",
    "content creator": "Content Creator",
    "developer": "Developer",
    "engineer": "Engineer",
    "researcher": "Researcher",
    "automation specialist": "Automation Specialist",
    "personal assistant": "Personal Assistant",
    "assistant director": "Assistant Director",
    "brand manager": "Brand Manager",
}

GROUP_ROLE_NORMALIZATION = {
    "Lead Editor": "Editor",
    "Short-Form Editor": "Editor",
    "Long-Form Editor": "Editor",
    "Podcast Editor": "Editor",
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
    job_role: str | None = None


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


def _clean_creators(creators: list[str] | None) -> list[str] | None:
    if not creators:
        return None
    cleaned = []
    for item in creators:
        val = str(item).strip()
        if val in VALID_CREATOR_LABELS and val not in cleaned:
            cleaned.append(val)
    return cleaned or None


def _detect_open_for_work_from_text(text: str) -> bool | None:
    if not text:
        return None

    t = text.lower()

    unavailable_signals = [
        "not available",
        "unavailable",
        "not open for work",
        "closed to work",
    ]
    available_signals = [
        "open to work",
        "open for work",
        "hire me",
        "available for work",
        "available now",
    ]

    for signal in unavailable_signals:
        if signal in t:
            return False

    for signal in available_signals:
        if signal in t:
            return True

    return None


def _normalize_role(raw_role: str | None) -> str | None:
    if not raw_role:
        return None

    role = re.sub(r"\s+", " ", raw_role.strip().lower())
    if role in ROLE_ALIASES:
        return ROLE_ALIASES[role]

    for alias, canonical in ROLE_ALIASES.items():
        if alias in role:
            return canonical

    title_case = " ".join(word.capitalize() for word in role.split())
    if title_case in VALID_JOB_ROLE_LABELS:
        return title_case

    return None


def _detect_job_role_from_text(text: str) -> str | None:
    if not text:
        return None

    lowered = text.lower()
    ordered_aliases = sorted(ROLE_ALIASES.items(), key=lambda x: len(x[0]), reverse=True)

    for alias, canonical in ordered_aliases:
        if alias in lowered:
            return canonical

    return None


def _group_for_role(job_role: str | None) -> str:
    if not job_role:
        return DEFAULT_GROUP_ID
    normalized_for_group = GROUP_ROLE_NORMALIZATION.get(job_role, job_role)
    return ROLE_TO_GROUP_ID.get(normalized_for_group, DEFAULT_GROUP_ID)


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

    profile = str(d.get(profile_key) or "") if profile_key else ""

    linkedin = ""
    email = ""
    years = None
    open_for_work = None
    views = None
    creators = None
    raw_role = None

    for k, v in d.items():
        lk = k.lower()

        if not linkedin and "linkedin" in lk and isinstance(v, str):
            linkedin = v.strip()

        if not email and "email" in lk and isinstance(v, str):
            email = v.strip()

        if years is None and "year" in lk and "exp" in lk:
            years = _extract_num(str(v))

        if open_for_work is None and "open" in lk and "work" in lk and isinstance(v, bool):
            open_for_work = v

        if views is None and "view" in lk:
            views = _extract_num(str(v))

        if creators is None and "creator" in lk and isinstance(v, list):
            creators = [str(x).strip() for x in v if x]

        if raw_role is None and any(token in lk for token in ["role", "title", "position", "job"]):
            if isinstance(v, str) and v.strip():
                raw_role = v.strip()

    if open_for_work is None:
        open_for_work = _detect_open_for_work_from_text(json.dumps(d, ensure_ascii=False))

    job_role = _normalize_role(raw_role)
    if job_role is None:
        job_role = _detect_job_role_from_text(json.dumps(d, ensure_ascii=False))

    rec = TalentRecord(
        name=name,
        ytjobs_profile_link=_to_absolute(profile),
        linkedin=linkedin,
        email=email,
        years_of_experience=years,
        open_for_work=open_for_work,
        creators_worked_with=_clean_creators(creators),
        views=views,
        job_role=job_role or "Other",
    )
    rec.priority = _normalize_priority(rec)
    return rec


def _extract_views_from_profile_text(text: str) -> float | None:
    patterns = [
        r"(\d+(?:\.\d+)?)\s*([kmb])?\s+views",
        r"views\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*([kmb])?",
    ]
    lowered = text.lower()
    for pattern in patterns:
        m = re.search(pattern, lowered, re.I)
        if m:
            raw = f"{m.group(1)}{m.group(2) or ''}"
            return _extract_num(raw)
    return None


def _extract_role_from_profile_text(text: str) -> str | None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for line in lines[:12]:
        role = _normalize_role(line)
        if role:
            return role
    return _detect_job_role_from_text(text)


def _extract_creators_from_profile_text(text: str) -> list[str] | None:
    lowered = text.lower()
    found = []

    keyword_map = {
        "editor": "Editors",
        "editors": "Editors",
        "thumbnail designer": "Thumbnail Designers",
        "thumbnail designers": "Thumbnail Designers",
        "animator": "Animators",
        "animators": "Animators",
        "scriptwriter": "Scriptwriters",
        "scriptwriters": "Scriptwriters",
    }

    for key, label in keyword_map.items():
        if key in lowered and label not in found:
            found.append(label)

    return found or None


async def _enrich_profile(page: Page, rec: TalentRecord) -> TalentRecord:
    if not rec.ytjobs_profile_link:
        return rec

    try:
        await page.goto(rec.ytjobs_profile_link, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(2500)
    except Exception:
        return rec

    body_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    body_text = body_text or ""

    if not rec.job_role or rec.job_role == "Other":
        detected_role = _extract_role_from_profile_text(body_text)
        if detected_role:
            rec.job_role = detected_role

    profile_views = _extract_views_from_profile_text(body_text)
    if profile_views is not None:
        rec.views = profile_views

    if rec.open_for_work is None:
        rec.open_for_work = _detect_open_for_work_from_text(body_text)

    if not rec.creators_worked_with:
        creators = _extract_creators_from_profile_text(body_text)
        if creators:
            rec.creators_worked_with = _clean_creators(creators)

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

    print(f"Scraping directory page {page_no}: {url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=90000)
    await page.wait_for_timeout(4000)

    if not candidates:
        raw = await page.evaluate(
            """
            () => {
              const cards = Array.from(document.querySelectorAll('a[href*="/talent/"]'));
              return cards.map(a => {
                const wrap = a.closest('article, li, div') || a.parentElement;
                const txt = wrap ? wrap.innerText : a.innerText;

                const img = wrap ? wrap.querySelector('img') : null;
                const imgAlt = img ? (img.getAttribute('alt') || '') : '';
                const imgTitle = img ? (img.getAttribute('title') || '') : '';
                const imgSrc = img ? (img.getAttribute('src') || '') : '';

                return {
                  href: a.getAttribute('href') || '',
                  text: txt || '',
                  img_alt: imgAlt,
                  img_title: imgTitle,
                  img_src: imgSrc
                };
              });
            }
            """
        )

        for row in raw:
            text = (row.get("text", "") or "").strip()
            href = _to_absolute(row.get("href", ""))
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if not lines:
                continue

            name = lines[0]
            linkedin_match = re.search(r"https?://(?:www\.)?linkedin\.com/[^\s]+", text, re.I)
            email_match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)

            availability_blob = " ".join(
                [
                    text,
                    row.get("img_alt", "") or "",
                    row.get("img_title", "") or "",
                    row.get("img_src", "") or "",
                ]
            )

            rec = TalentRecord(
                name=name,
                ytjobs_profile_link=href,
                linkedin=linkedin_match.group(0) if linkedin_match else "",
                email=email_match.group(0) if email_match else "",
                years_of_experience=_extract_num(text) if "year" in text.lower() else None,
                open_for_work=_detect_open_for_work_from_text(availability_blob),
                views=_extract_views_from_profile_text(text),
                creators_worked_with=_extract_creators_from_profile_text(text),
                job_role=_extract_role_from_profile_text(text) or "Other",
            )
            rec.creators_worked_with = _clean_creators(rec.creators_worked_with)
            rec.priority = _normalize_priority(rec)
            candidates.append(rec)

    unique: dict[str, TalentRecord] = {}
    for rec in candidates:
        key = rec.ytjobs_profile_link or rec.name.lower()
        if key and key not in unique:
            unique[key] = rec

    page.remove_listener("response", on_response)
    page_records = list(unique.values())
    print(f"Directory page {page_no} records found: {len(page_records)}")
    return page_records


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

    if rec.job_role:
        vals[col["job_role"]] = {"labels": [rec.job_role]}

    return vals


async def scrape(max_pages: int, headless: bool) -> list[TalentRecord]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context()
        directory_page = await ctx.new_page()
        profile_page = await ctx.new_page()

        all_records: list[TalentRecord] = []

        for page_no in range(1, max_pages + 1):
            records = await _scrape_page(directory_page, page_no)
            if not records:
                print(f"No records found on page {page_no}; stopping scrape.")
                break

            enriched_records = []
            for idx, rec in enumerate(records, start=1):
                print(f"  Enriching profile {idx}/{len(records)}: {rec.name}")
                enriched = await _enrich_profile(profile_page, rec)
                enriched_records.append(enriched)

            all_records.extend(enriched_records)

        await browser.close()

    dedup: dict[str, TalentRecord] = {}
    for rec in all_records:
        key = rec.ytjobs_profile_link or rec.name.lower()
        if key and key not in dedup:
            dedup[key] = rec

    return list(dedup.values())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape YTJobs talent and sync to monday.com")
    parser.add_argument("--max-pages", type=int, default=1000)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=False)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    token = MONDAY_API_TOKEN
    board_id = MONDAY_BOARD_ID
    columns = MONDAY_COLUMNS

    if not token and not args.dry_run:
        raise RuntimeError("MONDAY_API_TOKEN is required unless --dry-run is enabled")

    monday = MondayClient(token) if token else None
    existing = set()

    if monday:
        print("Loading existing monday YTJobs profile links...")
        existing = monday.get_existing_profile_links(board_id, columns["ytjobs_profile_link"])
        print(f"Existing monday links: {len(existing)}")

    records = asyncio.run(scrape(args.max_pages, args.headless))
    print(f"Total scraped records: {len(records)}")

    if args.dry_run:
        for rec in records[:25]:
            print(json.dumps(asdict(rec), ensure_ascii=False))
        print("Dry run mode enabled; no monday updates sent.")
        return

    if not monday:
        raise RuntimeError("MONDAY_API_TOKEN is required unless --dry-run is enabled")

    created = 0
    skipped_existing = 0
    failed = 0

    for idx, rec in enumerate(records, start=1):
        if not rec.ytjobs_profile_link:
            print(f"[{idx}] Skipping {rec.name} - missing YTJobs profile link")
            continue

        if rec.ytjobs_profile_link in existing:
            skipped_existing += 1
            continue

        group_id = _group_for_role(rec.job_role)
        values = build_column_values(rec, columns)

        print(
            f"[{idx}] Creating: {rec.name} | role={rec.job_role} | "
            f"group={group_id} | views={rec.views} | open_to_work={rec.open_for_work} | "
            f"priority={rec.priority} | creators={rec.creators_worked_with}"
        )

        try:
            monday.create_item(board_id, group_id, rec.name, values)
            existing.add(rec.ytjobs_profile_link)
            created += 1
            print(f"[{idx}] Created: {rec.name}")
        except Exception as e:
            print(f"[{idx}] First attempt failed for {rec.name}: {e}")
            time.sleep(2)
            try:
                monday.create_item(board_id, group_id, rec.name, values)
                existing.add(rec.ytjobs_profile_link)
                created += 1
                print(f"[{idx}] Created on retry: {rec.name}")
            except Exception as e2:
                failed += 1
                print(f"[{idx}] Failed permanently for {rec.name}: {e2}")

    print("========== FINAL SUMMARY ==========")
    print(f"Total scraped records: {len(records)}")
    print(f"Created monday items: {created}")
    print(f"Skipped existing: {skipped_existing}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    main()

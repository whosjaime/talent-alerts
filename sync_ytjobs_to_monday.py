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


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"Warning: invalid {name}={raw!r}; using default {default}")
        return default


MONDAY_BOARD_ID = _env_int("MONDAY_BOARD_ID", 18406893281)
MONDAY_DEFAULT_GROUP_ID = os.getenv("MONDAY_DEFAULT_GROUP_ID", "topics")

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
    "linkedin": os.getenv("MONDAY_LINKEDIN_COLUMN_ID", "text_mm20d7rp"),
    "ytjobs_profile_link": os.getenv("MONDAY_YTJOBS_PROFILE_LINK_COLUMN_ID", "text_mm20a03h"),
    "email": os.getenv("MONDAY_EMAIL_COLUMN_ID", "text_mm2028sd"),
    "years_of_experience": os.getenv("MONDAY_YOE_COLUMN_ID", "numeric_mm20gyp"),
    "priority": os.getenv("MONDAY_PRIORITY_COLUMN_ID", "color_mm204cwh"),
    "open_for_work": os.getenv("MONDAY_OPEN_FOR_WORK_COLUMN_ID", "boolean_mm20xkyn"),
    "creators_worked_with": os.getenv("MONDAY_CREATORS_WORKED_WITH_COLUMN_ID", "text_mm20xxmt"),
    "views": os.getenv("MONDAY_VIEWS_COLUMN_ID", "text_mm20rjas"),
    "job_role": os.getenv("MONDAY_JOB_ROLE_COLUMN_ID", "dropdown_mm22xt4g"),
    "niche": os.getenv("MONDAY_NICHE_COLUMN_ID", "long_text_mm26cehz"),
    "socials": os.getenv("MONDAY_SOCIALS_COLUMN_ID", "link_mm26njaz"),
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

NICHE_KEYWORDS = {
    "Gaming": ["gaming", "fortnite", "minecraft", "warzone", "call of duty", "twitch", "streamer"],
    "Finance": ["finance", "investing", "stocks", "crypto", "real estate", "money"],
    "Beauty": ["beauty", "makeup", "skincare", "fashion", "grwm"],
    "Fitness": ["fitness", "workout", "gym", "bodybuilding", "health"],
    "Tech": ["tech", "software", "ai", "developer", "gadgets", "coding"],
    "Business": ["business", "entrepreneur", "marketing", "sales", "startup"],
    "Education": ["education", "tutorial", "explainer", "teaching", "course"],
    "Podcast": ["podcast", "interview", "conversation"],
    "Food": ["food", "cooking", "recipe", "chef"],
    "Lifestyle": ["lifestyle", "vlog", "travel", "daily life"],
    "Commentary": ["commentary", "reaction", "drama", "internet culture"],
    "Entertainment": ["challenge", "prank", "comedy", "entertainment", "viral"],
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
    r"^talent$",
    r"^join as talent$",
    r"^loginpost a jobjoin as talent$",
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
    creators_worked_with: str = ""
    views: str = ""
    priority: str | None = None
    job_role: str | None = None
    niche: str = ""
    twitter: str = ""
    youtube: str = ""


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


def _is_junk_block_text(text: str) -> bool:
    if not text:
        return False
    lowered = " ".join(text.lower().split())
    if lowered in {"join as talent", "talent"}:
        return True
    return "post a job" in lowered and "join as talent" in lowered


def _normalize_role(raw: str | None) -> str | None:
    if not raw:
        return None
    role = re.sub(r"\s+", " ", raw.strip().lower())
    if role in ROLE_ALIASES:
        return ROLE_ALIASES[role]
    for alias, canonical in sorted(ROLE_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
        if alias in role:
            return canonical
    return None


def _detect_role_from_text(text: str) -> str | None:
    return _normalize_role(text)


def _detect_open_to_work_text(text: str) -> bool | None:
    if not text:
        return None
    t = text.lower()

    if any(x in t for x in ["not available", "unavailable", "not open for work"]):
        return False

    if any(x in t for x in ["hire me", "open to work", "open for work", "available for work"]):
        return True

    return None


def _extract_views_text(text: str) -> str:
    if not text:
        return ""
    patterns = [
        r"(\d+(?:\.\d+)?)\s*([kmb])?\s+views",
        r"views\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*([kmb])?",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            num = m.group(1)
            suffix = (m.group(2) or "").upper()
            return f"{num}{suffix} Views".strip()
    return ""


def _extract_creator_summary(text: str) -> str:
    if not text:
        return ""

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    chunks = []

    patterns = [
        r"(worked with[^\n]+)",
        r"(clients?[^\n]+)",
        r"(creators? worked with[^\n]+)",
        r"(experience[^\n]+)",
        r"(past work[^\n]+)",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I):
            chunk = " ".join(m.group(1).split())
            if chunk and chunk not in chunks:
                chunks.append(chunk)

    client_section = []
    capture = False
    for line in lines:
        lower = line.lower()
        if lower in {"clients", "verified clients"} or "clients" == lower:
            capture = True
            continue
        if capture:
            if len(line.split()) <= 6 and not re.search(r"(view|profile|portfolio|posts|timeline)", lower):
                client_section.append(line)
            if len(client_section) >= 8:
                break

    if client_section:
        client_text = ", ".join(dict.fromkeys(client_section))
        if client_text not in chunks:
            chunks.append(client_text)

    return " | ".join(chunks[:5])


def _extract_niche(text: str) -> str:
    if not text:
        return ""
    lowered = text.lower()
    scores = {}

    for niche, keywords in NICHE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in lowered)
        if score:
            scores[niche] = score

    if not scores:
        return ""

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top = [name for name, _ in ranked[:3]]
    return ", ".join(top)


def _extract_social_links(text: str) -> tuple[str, str, str]:
    linkedin = ""
    twitter = ""
    youtube = ""

    m = re.search(r"https?://(?:www\.)?linkedin\.com/[^\s)>\]]+", text, re.I)
    if m:
        linkedin = m.group(0).rstrip(".,)")

    twitter_patterns = [
        r"https?://(?:www\.)?(?:twitter\.com|x\.com)/[A-Za-z0-9_]+",
        r"@[A-Za-z0-9_]{2,}",
    ]
    for pattern in twitter_patterns:
        m = re.search(pattern, text, re.I)
        if m:
            raw = m.group(0).rstrip(".,)")
            if raw.startswith("@"):
                twitter = f"https://x.com/{raw[1:]}"
            else:
                twitter = raw
            break

    youtube_patterns = [
        r"https?://(?:www\.)?youtube\.com/[^\s)>\]]+",
        r"https?://youtu\.be/[^\s)>\]]+",
    ]
    for pattern in youtube_patterns:
        m = re.search(pattern, text, re.I)
        if m:
            youtube = m.group(0).rstrip(".,)")
            break

    return linkedin, twitter, youtube


def _normalize_priority(rec: TalentRecord) -> str:
    if rec.open_for_work is True and rec.email:
        return "Critical"
    if rec.open_for_work is True:
        return "High"
    if rec.open_for_work is False:
        return "Low"
    return "Medium"


def _pick_profile_link(href: str, nested_links: list[str]) -> str:
    for candidate in [href] + (nested_links or []):
        absolute = _normalize_profile_link(candidate)
        if _looks_like_valid_profile_url(absolute):
            return absolute
    return ""


def _pick_primary_social(rec: TalentRecord) -> str:
    if rec.linkedin:
        return rec.linkedin
    if rec.twitter:
        return rec.twitter
    if rec.youtube:
        return rec.youtube
    return rec.ytjobs_profile_link


async def _detect_open_to_work_from_page(page: Page, body_text: str) -> bool | None:
    try:
        hire_button = await page.locator("text=Hire Me").count()
        if hire_button > 0:
            return True
    except Exception:
        pass

    try:
        avatar_badge = await page.evaluate(
            """
            () => {
              const all = Array.from(document.querySelectorAll('*'));
              for (const el of all) {
                const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (text.includes('hire me')) return true;

                const cls = (el.className && typeof el.className === 'string') ? el.className.toLowerCase() : '';
                const aria = (el.getAttribute && (el.getAttribute('aria-label') || '') || '').toLowerCase();
                const alt = (el.getAttribute && (el.getAttribute('alt') || '') || '').toLowerCase();

                if (cls.includes('hire') || cls.includes('open') || aria.includes('hire me') || alt.includes('hire me')) {
                  return true;
                }
              }
              return false;
            }
            """
        )
        if avatar_badge:
            return True
    except Exception:
        pass

    return _detect_open_to_work_text(body_text)


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
        if _is_junk_block_text(text):
            continue

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            continue

        name = lines[0]
        if _is_junk_name(name):
            continue

        profile_link = _pick_profile_link(row.get("href", ""), row.get("nested_links", []))
        if not profile_link:
            continue

        linkedin, twitter, youtube = _extract_social_links(text)

        rec = TalentRecord(
            name=name,
            ytjobs_profile_link=profile_link,
            linkedin=linkedin,
            email="",
            years_of_experience=_extract_num(text) if "year" in text.lower() else None,
            open_for_work=_detect_open_to_work_text(text),
            creators_worked_with=_extract_creator_summary(text),
            views=_extract_views_text(text),
            priority=None,
            job_role=_detect_role_from_text(text),
            niche=_extract_niche(text),
            twitter=twitter,
            youtube=youtube,
        )
        rec.priority = _normalize_priority(rec)

        has_signal = any(
            [
                rec.linkedin,
                rec.years_of_experience is not None,
                rec.open_for_work is not None,
                rec.views,
                rec.job_role is not None,
                rec.niche,
                rec.creators_worked_with,
            ]
        )
        if not has_signal:
            continue

        dedup[profile_link] = rec

    print(f"Directory page {page_no} valid records found: {len(dedup)}")
    return list(dedup.values())


async def _enrich_profile(page: Page, rec: TalentRecord) -> TalentRecord:
    try:
        await page.goto(rec.ytjobs_profile_link, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(1800)
    except Exception as e:
        print(f"Profile load failed for {rec.name}: {e}")
        return rec

    body_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    body_text = body_text or ""

    page_content = await page.content()

    if not rec.job_role:
        rec.job_role = _detect_role_from_text(body_text)

    open_from_page = await _detect_open_to_work_from_page(page, body_text)
    if open_from_page is not None:
        rec.open_for_work = open_from_page

    views_text = _extract_views_text(body_text)
    if views_text:
        rec.views = views_text

    creator_summary = _extract_creator_summary(body_text)
    if creator_summary:
        rec.creators_worked_with = creator_summary

    if not rec.niche:
        rec.niche = _extract_niche(body_text)

    if rec.years_of_experience is None:
        m = re.search(r"(\d+(?:\.\d+)?)\s*\+?\s*years", body_text.lower())
        if m:
            rec.years_of_experience = _extract_num(m.group(1))

    linkedin, twitter, youtube = _extract_social_links(body_text + "\n" + page_content)

    if not rec.linkedin and linkedin:
        rec.linkedin = linkedin
    if not rec.twitter and twitter:
        rec.twitter = twitter
    if not rec.youtube and youtube:
        rec.youtube = youtube

    email_matches = re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", body_text, re.I)
    public_emails = [e for e in email_matches if "private" not in body_text.lower()]
    if not rec.email and public_emails:
        rec.email = public_emails[0]

    rec.priority = _normalize_priority(rec)
    return rec


class MondayClient:
    def __init__(self, token: str):
        self.client = httpx.Client(
            base_url="https://api.monday.com/v2",
            headers={"Authorization": token, "Content-Type": "application/json"},
            timeout=60.0,
        )

    def _post(self, query: str, variables: dict | None = None) -> dict:
        payload = {"query": query, "variables": variables or {}}
        r = self.client.post("", json=payload)
        print("MONDAY STATUS:", r.status_code)
        if r.status_code >= 400:
            print("MONDAY RESPONSE:", r.text)
        r.raise_for_status()

        body = r.json()
        if body.get("errors"):
            print("MONDAY ERRORS:", json.dumps(body["errors"], ensure_ascii=False))
            raise RuntimeError(f"monday API error: {body['errors']}")
        return body["data"]

    def get_existing_profile_links(self, board_id: int, ytjobs_column_id: str) -> set[str]:
        first_query = """
        query ($board_id: [ID!]) {
          boards(ids: $board_id) {
            items_page(limit: 500) {
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

        next_query = """
        query ($cursor: String!) {
          next_items_page(limit: 500, cursor: $cursor) {
            cursor
            items {
              column_values(ids: ["YTJOBS_COL"]) {
                text
              }
            }
          }
        }
        """.replace("YTJOBS_COL", ytjobs_column_id)

        results = set()

        data = self._post(first_query, {"board_id": [str(board_id)]})
        boards = data.get("boards", [])
        if not boards:
            return results

        page = boards[0]["items_page"]

        while True:
            for item in page["items"]:
                if not item["column_values"]:
                    continue
                val = item["column_values"][0].get("text")
                if val:
                    results.add(_normalize_profile_link(val))

            cursor = page.get("cursor")
            if not cursor:
                break

            data = self._post(next_query, {"cursor": cursor})
            page = data["next_items_page"]

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
        MONDAY_COLUMNS["linkedin"]: rec.linkedin or "",
        MONDAY_COLUMNS["ytjobs_profile_link"]: rec.ytjobs_profile_link or "",
        MONDAY_COLUMNS["email"]: rec.email or "",
        MONDAY_COLUMNS["creators_worked_with"]: rec.creators_worked_with or "",
        MONDAY_COLUMNS["views"]: rec.views or "",
        MONDAY_COLUMNS["niche"]: rec.niche or "",
    }

    if rec.years_of_experience is not None:
        vals[MONDAY_COLUMNS["years_of_experience"]] = rec.years_of_experience

    if rec.open_for_work is not None:
        vals[MONDAY_COLUMNS["open_for_work"]] = rec.open_for_work

    if rec.priority:
        vals[MONDAY_COLUMNS["priority"]] = {"label": rec.priority}

    if rec.job_role:
        vals[MONDAY_COLUMNS["job_role"]] = {"labels": [rec.job_role]}

    social_url = _pick_primary_social(rec)
    if social_url:
        vals[MONDAY_COLUMNS["socials"]] = {"url": social_url, "text": "Profile"}

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
                needs_enrichment = (
                    not rec.job_role
                    or not rec.views
                    or not rec.creators_worked_with
                    or rec.open_for_work is None
                    or not rec.niche
                )
                if needs_enrichment:
                    print(f"  Enriching profile {idx}/{len(records)}: {rec.name}")
                    rec = await _enrich_profile(profile_page, rec)
                enriched.append(rec)

            all_records.extend(enriched)

        await browser.close()

    dedup = {}
    for rec in all_records:
        if rec.ytjobs_profile_link:
            dedup[_normalize_profile_link(rec.ytjobs_profile_link)] = rec
    return list(dedup.values())


def parse_args() -> argparse.Namespace:
    env_max_pages = int(os.getenv("MAX_PAGES", "100"))
    env_headless = os.getenv("HEADLESS", "true").strip().lower() not in {"0", "false", "no"}
    env_dry_run = os.getenv("DRY_RUN", "false").strip().lower() in {"1", "true", "yes"}
    env_open_for_work_only = os.getenv("OPEN_FOR_WORK_ONLY", "false").strip().lower() in {"1", "true", "yes"}

    parser = argparse.ArgumentParser(description="Scrape YTJobs talent and sync to monday.com")
    parser.add_argument("--max-pages", type=int, default=env_max_pages)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=env_headless)
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=env_dry_run)
    parser.add_argument(
        "--open-for-work-only",
        action=argparse.BooleanOptionalAction,
        default=env_open_for_work_only,
        help="Only create monday leads for profiles explicitly marked open for work.",
    )
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
    skipped_not_open_for_work = 0
    failed = 0

    for idx, rec in enumerate(records, start=1):
        if rec.ytjobs_profile_link in existing:
            skipped_existing += 1
            continue

        if args.open_for_work_only and rec.open_for_work is not True:
            skipped_not_open_for_work += 1
            print(f"[{idx}] Skipping not-open-for-work: {rec.name} | open_for_work={rec.open_for_work}")
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
            f"group={group_id} | open_for_work={rec.open_for_work} | "
            f"priority={rec.priority} | niche={rec.niche} | views={rec.views}"
        )

        try:
            monday.create_item(MONDAY_BOARD_ID, group_id, rec.name, values)
            existing.add(rec.ytjobs_profile_link)
            created += 1
            time.sleep(0.2)
        except Exception as e:
            print(f"[{idx}] First attempt failed for {rec.name}: {e}")
            time.sleep(2)
            try:
                monday.create_item(MONDAY_BOARD_ID, group_id, rec.name, values)
                existing.add(rec.ytjobs_profile_link)
                created += 1
                time.sleep(0.2)
            except Exception as e2:
                failed += 1
                print(f"[{idx}] Failed permanently for {rec.name}: {e2}")

    print("========== FINAL SUMMARY ==========")
    print(f"Total scraped valid records: {len(records)}")
    print(f"Created monday items: {created}")
    print(f"Skipped existing: {skipped_existing}")
    print(f"Skipped not open for work: {skipped_not_open_for_work}")
    print(f"Used default group for unmapped/unknown role: {used_default_group}")
    print(f"Failed: {failed}")

    if len(records) > 0 and created == 0:
        print(
            "WARNING: Scrape completed but 0 monday items were created. "
            "Either all records already existed, were filtered, or monday rejected the writes."
        )


if __name__ == "__main__":
    main()

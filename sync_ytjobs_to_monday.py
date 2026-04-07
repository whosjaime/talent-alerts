#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, asdict
from urllib.parse import urljoin

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

ROLE_TO_GROUP_ID = {
    "Channel Manager": "topics",
    "Strategist": "group_mm20bark",
    "Producer": "group_mm22zy6t",
    "Creative Director": "group_mm22x74p",
    "Long-Form Editor": "group_mm22v92w",
    "Lead Editor": "group_mm22v92w",
    "Short-Form Editor": "group_mm22v92w",
    "Scriptwriter": "group_mm22hc00",
    "Thumbnail Designer": "group_mm22jqd",
    "Animator": "group_mm22agrt",
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
    # CHANNEL MANAGER
    "channel manager": "Channel Manager",
    "youtube manager": "Channel Manager",
    "manager": "Channel Manager",
    "production manager": "Channel Manager",
    "project manager": "Channel Manager",
    "content manager": "Channel Manager",
    "brand manager": "Channel Manager",
    "account manager": "Channel Manager",
    "operations manager": "Channel Manager",
    "growth manager": "Channel Manager",

    # STRATEGIST
    "strategist": "Strategist",
    "youtube strategist": "Strategist",
    "content strategist": "Strategist",
    "growth strategist": "Strategist",

    # PRODUCER
    "producer": "Producer",
    "youtube producer": "Producer",
    "content producer": "Producer",
    "video producer": "Producer",
    "executive producer": "Producer",

    # CREATIVE DIRECTOR
    "creative director": "Creative Director",
    "creative lead": "Creative Director",
    "head of creative": "Creative Director",

    # EDITOR
    "editor": "Long-Form Editor",
    "video editor": "Long-Form Editor",
    "youtube editor": "Long-Form Editor",
    "lead editor": "Lead Editor",
    "senior editor": "Lead Editor",
    "short-form editor": "Short-Form Editor",
    "short form editor": "Short-Form Editor",
    "reels editor": "Short-Form Editor",
    "tiktok editor": "Short-Form Editor",
    "long-form editor": "Long-Form Editor",
    "long form editor": "Long-Form Editor",

    # SCRIPTWRITER
    "scriptwriter": "Scriptwriter",
    "script writer": "Scriptwriter",
    "writer": "Scriptwriter",
    "youtube writer": "Scriptwriter",
    "content writer": "Scriptwriter",

    # THUMBNAIL
    "thumbnail designer": "Thumbnail Designer",
    "thumbnail artist": "Thumbnail Designer",
    "thumb designer": "Thumbnail Designer",
    "thumbnail": "Thumbnail Designer",

    # ANIMATOR
    "animator": "Animator",
    "motion designer": "Animator",
    "motion graphics": "Animator",
    "motion graphic designer": "Animator",
    "3d animator": "Animator",
    "2d animator": "Animator",
}

DISPLAY_ROLE_ALIASES = {
    "Channel Manager": "Channel Manager",
    "Creative Director": "Creative Director",
    "Video Editor": "Long-Form Editor",
    "Editor": "Long-Form Editor",
    "Lead Editor": "Lead Editor",
    "Thumbnail Designer": "Thumbnail Designer",
    "YouTube Strategist": "Strategist",
    "Strategist": "Strategist",
    "Producer": "Producer",
    "YouTube Producer": "Producer",
    "Scriptwriter": "Scriptwriter",
    "Writer": "Scriptwriter",
    "Animator": "Animator",
    "Motion Designer": "Animator",
    "Production Manager": "Channel Manager",
    "Project Manager": "Channel Manager",
    "Manager": "Channel Manager",
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
    return urljoin(YTJOBS_BASE, url)


def _normalize_profile_link(url: str) -> str:
    absolute = _to_absolute(url).strip()
    return absolute.rstrip("/")


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
    if any(x in t for x in ["hire me", "open to work", "open for work", "available for work", "book me"]):
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
    chunks: list[str] = []

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
        if lower in {"clients", "verified clients"} or lower == "clients":
            capture = True
            continue
        if capture:
            if len(line.split()) <= 8 and not re.search(r"(view|profile|portfolio|posts|timeline|faq|blog|jobs|talent)", lower):
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
            twitter = f"https://x.com/{raw[1:]}" if raw.startswith("@") else raw
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


def _pick_primary_social(rec: TalentRecord) -> str:
    if rec.linkedin:
        return rec.linkedin
    if rec.twitter:
        return rec.twitter
    if rec.youtube:
        return rec.youtube
    return rec.ytjobs_profile_link


async def _accept_cookies(page: Page) -> None:
    possible_texts = ["Accept", "I Accept", "Accept All", "Allow all"]
    for txt in possible_texts:
        try:
            btn = page.get_by_text(txt, exact=True).first
            if await btn.count() > 0:
                await btn.click(timeout=2500)
                await page.wait_for_timeout(1200)
                print("Accepted cookie banner.")
                return
        except Exception:
            pass


async def _get_visible_cards(page: Page) -> list[dict]:
    role_names = list(DISPLAY_ROLE_ALIASES.keys())
    return await page.evaluate(
        """(roleNames) => {
            const roleSet = new Set(roleNames);
            const cards = [];
            const maxX = window.innerWidth * 0.52;

            const all = Array.from(document.querySelectorAll("body *"));
            for (const el of all) {
                const rect = el.getBoundingClientRect();
                if (rect.width < 180 || rect.height < 40) continue;
                if (rect.x > maxX) continue;
                if (rect.y < 120) continue;

                const text = (el.innerText || el.textContent || "").trim();
                if (!text) continue;

                const lines = text.split(/\\n+/).map(x => x.trim()).filter(Boolean);
                if (lines.length < 2) continue;

                let name = "";
                let role = "";

                for (let i = 0; i < lines.length - 1; i++) {
                    if (roleSet.has(lines[i + 1])) {
                        name = lines[i];
                        role = lines[i + 1];
                        break;
                    }
                }

                if (!name || !role) continue;
                if (name.length > 80) continue;

                cards.push({
                    name,
                    role,
                    x: rect.x,
                    y: rect.y,
                    width: rect.width,
                    height: rect.height
                });
            }

            const dedup = [];
            const seen = new Set();
            for (const c of cards.sort((a, b) => a.y - b.y || a.x - b.x)) {
                const key = `${c.name}||${c.role}`;
                if (seen.has(key)) continue;
                seen.add(key);
                dedup.push(c);
            }
            return dedup;
        }""",
        role_names,
    )


async def _wait_for_panel(page: Page, clicked_name: str) -> bool:
    checks = [
        lambda: page.get_by_text("View full profile", exact=False).first.wait_for(timeout=4500),
        lambda: page.get_by_text("Portfolio", exact=True).first.wait_for(timeout=4500),
        lambda: page.get_by_text("Hire Me", exact=True).first.wait_for(timeout=4500),
        lambda: page.get_by_text(clicked_name, exact=True).nth(1).wait_for(timeout=4500),
    ]
    for check in checks:
        try:
            await check()
            return True
        except Exception:
            continue
    return False


async def _extract_panel_text(page: Page) -> str:
    try:
        panel_text = await page.evaluate(
            """() => {
                const minX = window.innerWidth * 0.42;
                let bestText = "";
                let bestLen = 0;

                const all = Array.from(document.querySelectorAll("body *"));
                for (const el of all) {
                    const rect = el.getBoundingClientRect();
                    if (rect.x < minX || rect.width < 250 || rect.height < 120) continue;
                    const text = (el.innerText || el.textContent || "").trim();
                    if (!text) continue;
                    if (text.length > bestLen && (text.includes("Portfolio") || text.includes("Hire Me") || text.includes("Profile"))) {
                        bestText = text;
                        bestLen = text.length;
                    }
                }

                return bestText || (document.body ? document.body.innerText : "");
            }"""
        )
        return panel_text or ""
    except Exception:
        try:
            return await page.locator("body").inner_text()
        except Exception:
            return ""


async def _extract_panel_profile_link(page: Page) -> str:
    try:
        link = page.get_by_text("View full profile", exact=False).first
        href = await link.get_attribute("href")
        if href:
            return _normalize_profile_link(href)
    except Exception:
        pass

    try:
        href = await page.evaluate(
            """() => {
                const anchors = Array.from(document.querySelectorAll('a[href]'));
                const target = anchors.find(a => ((a.innerText || a.textContent || '').toLowerCase().includes('view full profile')));
                return target ? target.getAttribute('href') : '';
            }"""
        )
        if href:
            return _normalize_profile_link(href)
    except Exception:
        pass

    return ""


async def _scrape_clicked_panel(page: Page, card: dict, page_no: int, idx: int) -> TalentRecord | None:
    click_x = card["x"] + min(card["width"] * 0.82, card["width"] - 14)
    click_y = card["y"] + card["height"] / 2

    try:
        await page.mouse.click(click_x, click_y)
        await page.wait_for_timeout(1400)
    except Exception as e:
        print(f"Failed clicking card {card['name']}: {e}")
        return None

    panel_ready = await _wait_for_panel(page, card["name"])
    if not panel_ready:
        print(f"Panel did not open for {card['name']}")
        return None

    panel_text = await _extract_panel_text(page)
    page_content = await page.content()
    profile_link = await _extract_panel_profile_link(page)

    if not profile_link:
        slug = re.sub(r"[^a-z0-9]+", "-", card["name"].lower()).strip("-")
        profile_link = f"{SEARCH_URL.format(page=page_no)}#inline-{page_no}-{idx}-{slug}"

    linkedin, twitter, youtube = _extract_social_links(panel_text + "\n" + page_content)

    years = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*\+?\s*years", panel_text.lower())
    if m:
        years = _extract_num(m.group(1))

    email = ""
    email_matches = re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", panel_text, re.I)
    if email_matches and "private" not in panel_text.lower():
        email = email_matches[0]

    rec = TalentRecord(
        name=card["name"],
        ytjobs_profile_link=profile_link,
        linkedin=linkedin,
        email=email,
        years_of_experience=years,
        open_for_work=_detect_open_to_work_text(panel_text),
        creators_worked_with=_extract_creator_summary(panel_text),
        views=_extract_views_text(panel_text),
        priority=None,
        job_role=DISPLAY_ROLE_ALIASES.get(card["role"]) or _detect_role_from_text(panel_text) or _normalize_role(card["role"]),
        niche=_extract_niche(panel_text),
        twitter=twitter,
        youtube=youtube,
    )
    rec.priority = _normalize_priority(rec)
    return rec


async def _scrape_directory_page(page: Page, page_no: int) -> list[TalentRecord]:
    url = SEARCH_URL.format(page=page_no)
    print(f"Scraping directory page {page_no}: {url}")

    await page.goto(url, wait_until="networkidle", timeout=90000)
    await page.wait_for_timeout(3500)
    await _accept_cookies(page)
    await page.wait_for_timeout(1500)

    title = await page.title()
    body_preview = await page.locator("body").inner_text()
    print("PAGE TITLE:", title)
    print("BODY PREVIEW:", body_preview[:1000])

    cards = await _get_visible_cards(page)
    print(f"Cards detected: {len(cards)}")
    for c in cards[:10]:
        print(f"CARD: {c['name']} | {c['role']} | x={c['x']:.0f} y={c['y']:.0f}")

    records: list[TalentRecord] = []
    seen_links: set[str] = set()

    for idx, card in enumerate(cards, start=1):
        if _is_junk_name(card["name"]):
            continue

        print(f"Clicking [{idx}/{len(cards)}]: {card['name']} | {card['role']}")
        rec = await _scrape_clicked_panel(page, card, page_no, idx)
        if not rec:
            continue

        normalized_link = _normalize_profile_link(rec.ytjobs_profile_link)
        if normalized_link in seen_links:
            continue

        seen_links.add(normalized_link)
        records.append(rec)
        await page.wait_for_timeout(500)

    print(f"Directory page {page_no} valid records found: {len(records)}")
    return records


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

    if rec.open_for_work is True:
        vals[MONDAY_COLUMNS["open_for_work"]] = {"checked": True}
    elif rec.open_for_work is False:
        vals[MONDAY_COLUMNS["open_for_work"]] = {"checked": False}

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
        ctx = await browser.new_context(viewport={"width": 1600, "height": 1200})
        page = await ctx.new_page()

        all_records: list[TalentRecord] = []

        for page_no in range(1, max_pages + 1):
            try:
                records = await _scrape_directory_page(page, page_no)
            except Exception as e:
                print(f"Failed scraping page {page_no}: {e}")
                continue

            if not records:
                print(f"No valid records found on page {page_no}; continuing.")
                continue

            all_records.extend(records)
            await page.wait_for_timeout(1000)

        await browser.close()

    dedup: dict[str, TalentRecord] = {}
    for rec in all_records:
        if rec.ytjobs_profile_link:
            dedup[_normalize_profile_link(rec.ytjobs_profile_link)] = rec

    return list(dedup.values())


def parse_args() -> argparse.Namespace:
    env_max_pages = int(os.getenv("MAX_PAGES", "10"))
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
    skipped_not_open_for_work = 0
    skipped_unmapped = 0
    failed = 0

    for idx, rec in enumerate(records, start=1):
        normalized_link = _normalize_profile_link(rec.ytjobs_profile_link)

        if normalized_link in existing:
            skipped_existing += 1
            continue

        if args.open_for_work_only and rec.open_for_work is not True:
            skipped_not_open_for_work += 1
            print(f"[{idx}] Skipping not-open-for-work: {rec.name} | open_for_work={rec.open_for_work}")
            continue

        group_id = ROLE_TO_GROUP_ID.get(rec.job_role or "")
        if not group_id:
            skipped_unmapped += 1
            print(f"[{idx}] Skipping because role is not mapped to a monday group: {rec.name} | role={rec.job_role}")
            continue

        values = build_column_values(rec)

        print(
            f"[{idx}] Creating: {rec.name} | role={rec.job_role} | "
            f"group={group_id} | open_for_work={rec.open_for_work} | "
            f"priority={rec.priority} | niche={rec.niche} | views={rec.views}"
        )

        try:
            monday.create_item(MONDAY_BOARD_ID, group_id, rec.name, values)
            existing.add(normalized_link)
            created += 1
            time.sleep(0.2)
        except Exception as e:
            print(f"[{idx}] First attempt failed for {rec.name}: {e}")
            time.sleep(2)
            try:
                monday.create_item(MONDAY_BOARD_ID, group_id, rec.name, values)
                existing.add(normalized_link)
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
    print(f"Skipped unmapped role: {skipped_unmapped}")
    print(f"Failed: {failed}")

    if len(records) > 0 and created == 0:
        print(
            "WARNING: Scrape completed but 0 monday items were created. "
            "Either all records already existed, were filtered, were unmapped, or monday rejected the writes."
        )


if __name__ == "__main__":
    main()

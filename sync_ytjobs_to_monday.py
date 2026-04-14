#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, asdict, field
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
    "Other": "group_mm28n3kf",
}

MONDAY_COLUMNS = {
    "linkedin": os.getenv("MONDAY_LINKEDIN_COLUMN_ID", "text_mm20d7rp"),
    "ytjobs_profile_link": os.getenv("MONDAY_YTJOBS_PROFILE_LINK_COLUMN_ID", "text_mm20a03h"),
    "email": os.getenv("MONDAY_EMAIL_COLUMN_ID", "text_mm2028sd"),
    "years_of_experience": os.getenv("MONDAY_YOE_COLUMN_ID", "numeric_mm20gyp"),
    "open_for_work": os.getenv("MONDAY_OPEN_FOR_WORK_COLUMN_ID", "boolean_mm20xkyn"),
    "creators_worked_with": os.getenv("MONDAY_CREATORS_WORKED_WITH_COLUMN_ID", "long_text_mm27zpbz"),
    "views": os.getenv("MONDAY_VIEWS_COLUMN_ID", "text_mm27hzep"),
    "job_role": os.getenv("MONDAY_JOB_ROLE_COLUMN_ID", "dropdown_mm22xt4g"),
    "niche": os.getenv("MONDAY_NICHE_COLUMN_ID", "long_text_mm26cehz"),
    "location": os.getenv("MONDAY_LOCATION_COLUMN_ID", "text_mm27dy8q"),
    "socials": os.getenv("MONDAY_SOCIALS_COLUMN_ID", "text_mm2c57z4"),
}

ROLE_ALIASES = {
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
    "strategist": "Strategist",
    "youtube strategist": "Strategist",
    "content strategist": "Strategist",
    "growth strategist": "Strategist",
    "producer": "Producer",
    "youtube producer": "Producer",
    "content producer": "Producer",
    "video producer": "Producer",
    "executive producer": "Producer",
    "creative director": "Creative Director",
    "creative lead": "Creative Director",
    "head of creative": "Creative Director",
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
    "scriptwriter": "Scriptwriter",
    "script writer": "Scriptwriter",
    "writer": "Scriptwriter",
    "youtube writer": "Scriptwriter",
    "content writer": "Scriptwriter",
    "thumbnail designer": "Thumbnail Designer",
    "thumbnail artist": "Thumbnail Designer",
    "thumb designer": "Thumbnail Designer",
    "thumbnail": "Thumbnail Designer",
    "animator": "Animator",
    "motion designer": "Animator",
    "motion graphics": "Animator",
    "motion graphic designer": "Animator",
    "3d animator": "Animator",
    "2d animator": "Animator",
    "other": "Other",
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
    "Other": "Other",
}

NICHE_KEYWORDS = {
    "Gaming": ["gaming", "fortnite", "minecraft", "warzone", "call of duty", "twitch", "streamer"],
    "Finance": ["finance", "investing", "stocks", "crypto", "real estate", "money", "economy"],
    "Beauty": ["beauty", "makeup", "skincare", "fashion", "grwm"],
    "Fitness": ["fitness", "workout", "gym", "bodybuilding", "health"],
    "Tech": ["tech", "software", "ai", "developer", "gadgets", "coding"],
    "Business": ["business", "entrepreneur", "marketing", "sales", "startup"],
    "Education": ["education", "tutorial", "explainer", "teaching", "course"],
    "Podcast": ["podcast", "interview", "conversation"],
    "Food": ["food", "cooking", "recipe", "chef"],
    "Lifestyle": ["lifestyle", "vlog", "travel", "daily life", "people & blogs"],
    "Commentary": ["commentary", "reaction", "drama", "internet culture"],
    "Entertainment": ["challenge", "prank", "comedy", "entertainment", "viral"],
    "Economy": ["economy"],
    "People & Blogs": ["people & blogs"],
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

GENERIC_CREATOR_WORDS = {
    "about", "portfolio", "experience", "verified clients", "client reviews", "roles",
    "categories", "confirmed info", "hire me", "open to work", "views", "subscribers",
    "youtube", "linkedin", "twitter", "x", "instagram", "tiktok", "public", "private",
    "videos", "likes", "profile", "clients", "timeline", "posts", "see more",
}


@dataclass
class TalentRecord:
    name: str
    ytjobs_profile_link: str
    linkedin: str = ""
    email: str = ""
    years_of_experience: float | None = None
    open_for_work: bool | None = None
    creators_worked_with: str = ""
    views: float | None = None
    job_role: str | None = None
    niche: str = ""
    location: str = ""
    twitter: str = ""
    twitter_handle: str = ""
    youtube: str = ""
    _profile_text: str = field(default="", repr=False)


def _to_absolute(url: str) -> str:
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
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


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for v in values:
        clean = (v or "").strip()
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


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
    if not text:
        return None

    m = re.search(
        r"Roles\s*(.*?)(?:\nConfirmed info|\nExperience|\nAbout|\nPortfolio|\nVerified Clients|\nClient Reviews|\nCategories|$)",
        text,
        re.I | re.S,
    )
    if m:
        lines = [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]
        for line in lines:
            normalized = _normalize_role(line)
            if normalized:
                return normalized

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


def _parse_compact_number(num_text: str, suffix: str) -> int:
    num = float(num_text.replace(",", ""))
    suffix = suffix.lower()
    if suffix in {"b", "billion"}:
        num *= 1_000_000_000
    elif suffix in {"m", "million"}:
        num *= 1_000_000
    elif suffix in {"k", "thousand"}:
        num *= 1_000
    return round(num)


def _extract_views_number(text: str) -> float | None:
    if not text:
        return None

    text = re.sub(r"\s+", " ", text).strip()

    patterns = [
        r"(\d+(?:,\d{3})+)\+?\s*views\b",
        r"(\d+(?:\.\d+)?)\+?\s*([bmk])\s*views\b",
        r"(\d+(?:\.\d+)?)\+?\s*(billion|million|thousand)\s*views\b",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if not m:
            continue

        if len(m.groups()) == 1 or (len(m.groups()) > 1 and m.group(2) is None):
            return float(m.group(1).replace(",", ""))

        return _parse_compact_number(m.group(1), m.group(2))

    return None


def _extract_public_email(text: str) -> str:
    if not text:
        return ""

    emails = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)

    blocked_exact = {
        "support@ytjobs.co",
        "hello@ytjobs.co",
        "team@ytjobs.co",
        "info@ytjobs.co",
    }

    for email in emails:
        e = email.strip().lower()
        if e in blocked_exact:
            continue
        if "ytjobs.co" in e:
            continue
        if "noreply" in e or "no-reply" in e:
            continue
        return email.strip()

    return ""


def _clean_social(url: str) -> str:
    return url.rstrip(".,);]}>\"'")


def _extract_twitter_handle(text: str) -> str:
    if not text:
        return ""
    text_without_emails = re.sub(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        " ",
        text,
    )
    m = re.search(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]{2,15})\b", text_without_emails, re.I)
    if m:
        return f"@{m.group(1)}"
    return ""


def _extract_social_links(text: str) -> tuple[str, str, str, str]:
    linkedin = ""
    twitter = ""
    twitter_handle = ""
    youtube = ""

    linkedin_patterns = [
        r"https?://(?:www\.)?linkedin\.com/[^\s<>\])\"']+",
        r"(?:linkedin\.com/[^\s<>\])\"']+)",
    ]
    for pattern in linkedin_patterns:
        m = re.search(pattern, text, re.I)
        if m:
            linkedin = _clean_social(m.group(0))
            if not linkedin.startswith("http"):
                linkedin = "https://" + linkedin
            break

    twitter_patterns = [
        r"https?://(?:www\.)?(?:twitter\.com|x\.com)/[A-Za-z0-9_]+",
        r"(?:twitter\.com|x\.com)/[A-Za-z0-9_]+",
    ]
    for pattern in twitter_patterns:
        m = re.search(pattern, text, re.I)
        if m:
            twitter = _clean_social(m.group(0))
            if not twitter.startswith("http"):
                twitter = "https://" + twitter
            break

    if twitter:
        m = re.search(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]{2,15})\b", twitter, re.I)
        if m:
            twitter_handle = f"@{m.group(1)}"
    else:
        twitter_handle = _extract_twitter_handle(text)

    youtube_patterns = [
        r"https?://(?:www\.)?youtube\.com/[^\s<>\])\"']+",
        r"https?://youtu\.be/[^\s<>\])\"']+",
        r"(?:youtube\.com/[^\s<>\])\"']+)",
    ]
    for pattern in youtube_patterns:
        m = re.search(pattern, text, re.I)
        if m:
            youtube = _clean_social(m.group(0))
            if not youtube.startswith("http"):
                youtube = "https://" + youtube
            break

    return linkedin, twitter, twitter_handle, youtube


def _pick_primary_social_text(rec: TalentRecord) -> str:
    if rec.twitter_handle:
        return rec.twitter_handle
    return ""


def _extract_niche(text: str) -> str:
    if not text:
        return ""

    allowed = {
        "Gaming", "Finance", "Beauty", "Fitness", "Tech", "Business",
        "Education", "Podcast", "Food", "Lifestyle", "Commentary",
        "Entertainment", "Economy", "People & Blogs",
    }

    found = []

    m = re.search(
        r"Categories\s*(.*?)(?:\nConfirmed info|\nExperience|\nAbout|\nPortfolio|\nVerified Clients|\nClient Reviews|$)",
        text,
        re.I | re.S,
    )
    if m:
        lines = [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]
        for line in lines:
            if line in allowed and line not in found:
                found.append(line)

    lowered = text.lower()
    keyword_scores = {}
    for niche, keywords in NICHE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in lowered)
        if score:
            keyword_scores[niche] = score

    for niche, _ in sorted(keyword_scores.items(), key=lambda x: x[1], reverse=True):
        if niche in allowed and niche not in found:
            found.append(niche)

    return ", ".join(found[:4])


def _extract_years_of_experience(text: str) -> float | None:
    if not text:
        return None

    patterns = [
        r"(\d+(?:\.\d+)?)\s*\+?\s*years(?:\s+of\s+experience)?",
        r"experience\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*\+?\s*years",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return _extract_num(m.group(1))
    return None


def _looks_like_creator_name(value: str) -> bool:
    if not value:
        return False

    v = re.sub(r"\s+", " ", value).strip()
    lowered = v.lower()

    bad_phrases = [
        "verified", "subscribers", "videos", "likes", "views",
        "great communication", "pleasure to work together", "dedicated specialist",
        "professional", "excellent", "always a pleasure", "confirmed info",
        "client reviews", "portfolio", "about", "roles", "experience",
        "public", "private", "profile", "timeline", "posts", "see more",
    ]

    if lowered in GENERIC_CREATOR_WORDS:
        return False
    if any(p in lowered for p in bad_phrases):
        return False
    if re.search(r"https?://", v, re.I):
        return False
    if len(v) < 2 or len(v) > 60:
        return False
    if len(v.split()) > 6:
        return False
    if re.fullmatch(r"[\d.,+\- ]+", v):
        return False

    return True


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


async def _wait_for_profile_ready(profile_page: Page) -> None:
    await profile_page.wait_for_load_state("domcontentloaded")
    try:
        await profile_page.wait_for_load_state("networkidle", timeout=6000)
    except Exception:
        pass

    anchors = [
        profile_page.get_by_text("About", exact=True).first,
        profile_page.get_by_text("Portfolio", exact=True).first,
        profile_page.get_by_text("Experience", exact=True).first,
        profile_page.get_by_text("Hire Me", exact=True).first,
    ]

    for locator in anchors:
        try:
            await locator.wait_for(timeout=3000)
            return
        except Exception:
            continue

    await profile_page.wait_for_timeout(600)


async def _get_visible_cards(page: Page) -> list[dict]:
    role_names = list(DISPLAY_ROLE_ALIASES.keys())
    cards = await page.evaluate(
        """(roleNames) => {
            const roleSet = new Set(roleNames);
            const anchors = Array.from(document.querySelectorAll('a[href*="/talent/profile/"]'));
            const out = [];

            for (const a of anchors) {
                const href = a.getAttribute('href') || '';
                if (!href || href.includes('/talent/search/')) continue;

                const text = (a.innerText || a.textContent || '').trim();
                if (!text) continue;

                const lines = text.split(/\\n+/).map(x => x.trim()).filter(Boolean);
                if (lines.length < 2) continue;

                let name = '';
                let role = '';

                for (let i = 0; i < lines.length - 1; i++) {
                    if (roleSet.has(lines[i + 1])) {
                        name = lines[i];
                        role = lines[i + 1];
                        break;
                    }
                }

                if (!name || !role) continue;

                const rect = a.getBoundingClientRect();
                out.push({ name, role, href, x: rect.x, y: rect.y });
            }

            const seen = new Set();
            return out
                .sort((a, b) => a.y - b.y || a.x - b.x)
                .filter(x => {
                    const key = `${x.name}|${x.role}|${x.href}`;
                    if (seen.has(key)) return false;
                    seen.add(key);
                    return true;
                });
        }""",
        role_names,
    )
    return cards


async def _collect_profile_text(profile_page: Page) -> tuple[str, str]:
    html = await profile_page.content()
    sections = []

    labels = [
        "About",
        "Portfolio",
        "Experience",
        "Verified Clients",
        "Client Reviews",
        "Roles",
        "Categories",
        "Confirmed info",
    ]

    for label in labels:
        try:
            loc = profile_page.get_by_text(label, exact=True).first
            if await loc.count() > 0:
                text = await loc.locator("xpath=..").inner_text(timeout=1500)
                if text and text.strip():
                    sections.append(text.strip())
        except Exception:
            pass

    try:
        body_text = await profile_page.locator("body").inner_text()
    except Exception:
        body_text = ""

    combined_text = "\n\n".join(_dedupe_keep_order(sections + [body_text]))
    return combined_text, html


async def _wait_for_confirmed_info_dialog(profile_page: Page) -> bool:
    try:
        dialog = profile_page.locator('[role="dialog"]').last
        if await dialog.count() == 0:
            return False
        await dialog.wait_for(state="visible", timeout=800)
        txt = await dialog.inner_text(timeout=800)
        return bool(txt and "Confirmed info" in txt)
    except Exception:
        return False


async def _click_confirmed_info_candidate(profile_page: Page, candidate) -> bool:
    try:
        await candidate.scroll_into_view_if_needed()
    except Exception:
        pass

    try:
        await candidate.click(timeout=800)
    except Exception:
        try:
            await candidate.evaluate(
                """
                (el) => {
                    const target =
                        el.closest('button,[role="button"],a') ||
                        el.querySelector?.('button,[role="button"],a') ||
                        el;
                    target.click();
                }
                """
            )
        except Exception:
            return False

    await profile_page.wait_for_timeout(250)
    return await _wait_for_confirmed_info_dialog(profile_page)


async def _open_confirmed_info_modal(profile_page: Page) -> bool:
    candidates = [
        profile_page.get_by_text("Confirmed info", exact=True),
        profile_page.locator("[role='button'], button, a").filter(has_text="Confirmed info"),
        profile_page.locator("div, section").filter(has_text="Confirmed info"),
    ]

    for locator in candidates:
        try:
            count = await locator.count()
        except Exception:
            count = 0

        for i in range(min(count, 2)):
            try:
                candidate = locator.nth(i)
                if await _click_confirmed_info_candidate(profile_page, candidate):
                    print("Confirmed info modal opened.", flush=True)
                    return True
            except Exception:
                continue

    print("Could not open Confirmed info modal.", flush=True)
    return False


async def _close_modal_if_open(profile_page: Page) -> None:
    try:
        dialog = profile_page.locator('[role="dialog"]').last
        if await dialog.count() > 0:
            close_candidates = [
                dialog.locator('[aria-label="Close"]'),
                dialog.get_by_text("×", exact=True),
                dialog.get_by_text("✕", exact=True),
                dialog.get_by_text("X", exact=True),
                dialog.locator("button").last,
            ]

            for locator in close_candidates:
                try:
                    if await locator.count() > 0:
                        await locator.first.click(timeout=500)
                        await profile_page.wait_for_timeout(150)
                        return
                except Exception:
                    continue
    except Exception:
        pass

    try:
        await profile_page.keyboard.press("Escape")
        await profile_page.wait_for_timeout(100)
    except Exception:
        pass


async def _extract_confirmed_info_modal_text(profile_page: Page) -> str:
    opened = await _open_confirmed_info_modal(profile_page)
    if not opened:
        return ""

    try:
        dialog = profile_page.locator('[role="dialog"]').last
        if await dialog.count() > 0:
            txt = await dialog.inner_text(timeout=1200)
            if txt and "Confirmed info" in txt:
                print("CONFIRMED INFO MODAL TEXT:", repr(txt[:500]), flush=True)
                return txt
        return ""
    finally:
        await _close_modal_if_open(profile_page)


def _extract_socials_from_confirmed_modal(modal_text: str) -> str:
    if not modal_text:
        return ""

    flat = re.sub(r"\s+", " ", modal_text).strip()

    patterns = [
        r"Public\s+@([A-Za-z0-9_]{2,15})\s+Talent[’'`]?s personal Twitter account is verified via Twitter API",
        r"@([A-Za-z0-9_]{2,15})\s+Talent[’'`]?s personal Twitter account is verified via Twitter API",
    ]

    for pattern in patterns:
        m = re.search(pattern, flat, re.I)
        if m:
            return f"@{m.group(1)}"

    m = re.search(r"(?<![\w.])@([A-Za-z0-9_]{2,15})\b", flat)
    if m:
        return f"@{m.group(1)}"

    return ""


def _extract_location_from_confirmed_modal(modal_text: str) -> str:
    if not modal_text:
        return ""

    flat = re.sub(r"\s+", " ", modal_text).strip()

    patterns = [
        r"Public\s+([A-Za-z0-9 .,'\\-]+?)\s+Talent[’'`]?s location is verified via browser API",
        r"([A-Za-z0-9 .,'\\-]+?)\s+Talent[’'`]?s location is verified via browser API",
    ]

    for pattern in patterns:
        m = re.search(pattern, flat, re.I)
        if m:
            value = re.sub(r"\s+", " ", m.group(1)).strip(" ,.-")
            if value and "Confirmed info" not in value and "Why is this important" not in value:
                return value

    return ""


async def _extract_top_views(profile_page: Page) -> float | None:
    try:
        body_text = await profile_page.locator("body").inner_text()
    except Exception:
        body_text = ""

    stat_patterns = [
        r"(\d+(?:\.\d+)?)\s*([BKM])\s*Views",
        r"(\d+(?:,\d{3})+)\s*Views",
    ]

    for pattern in stat_patterns:
        m = re.search(pattern, body_text, re.I)
        if m:
            return _extract_views_number(m.group(0))

    candidates = [
        profile_page.get_by_text("Views", exact=False),
        profile_page.locator("text=/[0-9.,]+\\s*[BMK]?\\s*Views/i"),
    ]

    for locator in candidates:
        try:
            count = await locator.count()
            for i in range(min(count, 10)):
                text = (await locator.nth(i).inner_text(timeout=1000)).strip()
                val = _extract_views_number(text)
                if val is not None:
                    return val
        except Exception:
            pass

    return None


async def _extract_creators(profile_page: Page, combined_text: str) -> str:
    names = []

    section_patterns = [
        r"Verified Clients\s*(.*?)(?:\nTimeline|\nPosts|\nClient Reviews|\nRoles|\nCategories|\nExperience|\nAbout|\nPortfolio|$)",
        r"Clients\s*(.*?)(?:\nTimeline|\nPosts|\nClient Reviews|\nRoles|\nCategories|\nExperience|\nAbout|\nPortfolio|$)",
        r"Worked With\s*(.*?)(?:\nClient Reviews|\nRoles|\nCategories|\nExperience|\nAbout|\nPortfolio|$)",
    ]

    for pattern in section_patterns:
        m = re.search(pattern, combined_text, re.I | re.S)
        if m:
            lines = [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]
            for line in lines:
                line = re.sub(r"\s+", " ", line).strip()
                if _looks_like_creator_name(line):
                    names.append(line)

    candidate_locators = [
        profile_page.locator(".swiper-wrapper .swiper-slide"),
        profile_page.locator('[class*="swiper-slide"]'),
        profile_page.locator("img[alt]").locator("xpath=.."),
    ]

    for locator in candidate_locators:
        try:
            count = await locator.count()
            for i in range(min(count, 80)):
                text = (await locator.nth(i).inner_text(timeout=1000)).strip()
                if not text:
                    continue

                for line in text.splitlines():
                    cleaned = re.sub(r"\s+", " ", line).strip()
                    if _looks_like_creator_name(cleaned):
                        names.append(cleaned)
        except Exception:
            pass

    blocked = {
        "Verified Clients", "Portfolio", "Profile", "Clients", "Timeline", "Posts",
        "Videos", "Views", "Likes", "Channel Manager", "Video Editor", "Recommended", "see more",
    }

    final_names = []
    for n in _dedupe_keep_order(names):
        if n in blocked:
            continue
        if re.search(r"\bsubscribers\b", n, re.I):
            continue
        final_names.append(n)

    return ", ".join(final_names[:20])


async def _scrape_profile(context, card: dict) -> TalentRecord | None:
    profile_url = _normalize_profile_link(card["href"])
    if not profile_url:
        return None

    profile_page = await context.new_page()
    try:
        print(f"Opening profile: {card['name']} | {profile_url}")
        await profile_page.goto(profile_url, wait_until="domcontentloaded", timeout=90000)
        await _wait_for_profile_ready(profile_page)
        await profile_page.wait_for_timeout(400)

        combined_text, html = await _collect_profile_text(profile_page)
        confirmed_modal_text = await _extract_confirmed_info_modal_text(profile_page)
        full_text = "\n".join([combined_text, html, confirmed_modal_text]).strip()

        linkedin, twitter, twitter_handle, youtube = _extract_social_links(full_text)

        modal_twitter_handle = _extract_socials_from_confirmed_modal(confirmed_modal_text)
        if modal_twitter_handle:
            twitter_handle = modal_twitter_handle

        email = _extract_public_email(full_text)
        top_views = await _extract_top_views(profile_page)
        views = top_views if top_views is not None else _extract_views_number(full_text)
        niche = _extract_niche(full_text)
        years = _extract_years_of_experience(full_text)
        open_for_work = _detect_open_to_work_text(full_text)

        modal_location = _extract_location_from_confirmed_modal(confirmed_modal_text)
        location = modal_location or ""

        primary_role = (
            DISPLAY_ROLE_ALIASES.get(card["role"])
            or _detect_role_from_text(full_text)
            or _normalize_role(card["role"])
        )

        creators = await _extract_creators(profile_page, full_text)

        rec = TalentRecord(
            name=card["name"],
            ytjobs_profile_link=profile_url,
            linkedin=linkedin,
            email=email,
            years_of_experience=years,
            open_for_work=open_for_work,
            creators_worked_with=creators,
            views=views,
            job_role=primary_role,
            niche=niche,
            location=location,
            twitter=twitter,
            twitter_handle=twitter_handle,
            youtube=youtube,
            _profile_text=full_text,
        )

        print("EMAIL PARSED:", rec.email)
        print("VIEWS PARSED:", rec.views)
        print("CREATORS PARSED:", rec.creators_worked_with)
        print("NICHE PARSED:", rec.niche)
        print("MODAL RAW TEXT:", repr(confirmed_modal_text[:500]))
        print("LOCATION PARSED:", rec.location)
        print("TWITTER HANDLE PARSED:", rec.twitter_handle)
        print("ROLE PARSED:", rec.job_role)

        return rec

    except Exception as e:
        print(f"Failed scraping profile {card['name']} | {profile_url}: {e}")
        return None
    finally:
        await profile_page.close()


async def _scrape_directory_page(page: Page, context, page_no: int) -> list[TalentRecord]:
    url = SEARCH_URL.format(page=page_no)
    print(f"Scraping directory page {page_no}: {url}")

    await page.goto(url, wait_until="networkidle", timeout=90000)
    await page.wait_for_timeout(1500)
    await _accept_cookies(page)
    await page.wait_for_timeout(400)

    title = await page.title()
    body_preview = await page.locator("body").inner_text()
    print("PAGE TITLE:", title)
    print("BODY PREVIEW:", body_preview[:1000])

    cards = await _get_visible_cards(page)
    print(f"Cards detected: {len(cards)}")
    for c in cards[:10]:
        print(f"CARD: {c['name']} | {c['role']} | {c['href']}")

    records: list[TalentRecord] = []
    seen_record_keys: set[str] = set()

    for idx, card in enumerate(cards, start=1):
        if _is_junk_name(card["name"]):
            continue

        normalized_link = _normalize_profile_link(card["href"])
        if not normalized_link:
            continue

        print(f"Scraping [{idx}/{len(cards)}]: {card['name']} | {card['role']}")
        rec = await _scrape_profile(context, card)
        if not rec:
            continue

        dedupe_key = normalized_link
        if dedupe_key in seen_record_keys:
            continue

        seen_record_keys.add(dedupe_key)
        records.append(rec)

        await page.wait_for_timeout(100)

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
        MONDAY_COLUMNS["niche"]: rec.niche or "",
        MONDAY_COLUMNS["location"]: rec.location or "",
        MONDAY_COLUMNS["socials"]: _pick_primary_social_text(rec),
        MONDAY_COLUMNS["creators_worked_with"]: rec.creators_worked_with or "",
    }

    if rec.years_of_experience is not None:
        vals[MONDAY_COLUMNS["years_of_experience"]] = rec.years_of_experience

    if rec.views is not None:
        vals[MONDAY_COLUMNS["views"]] = str(int(rec.views))

    if rec.open_for_work is True:
        vals[MONDAY_COLUMNS["open_for_work"]] = {"checked": True}
    elif rec.open_for_work is False:
        vals[MONDAY_COLUMNS["open_for_work"]] = {"checked": False}

    if rec.job_role:
        vals[MONDAY_COLUMNS["job_role"]] = {"labels": [rec.job_role]}

    return vals


async def scrape(max_pages: int, headless: bool) -> list[TalentRecord]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context(viewport={"width": 1600, "height": 1200})
        page = await ctx.new_page()

        all_records: list[TalentRecord] = []

        for page_no in range(1, max_pages + 1):
            try:
                records = await _scrape_directory_page(page, ctx, page_no)
            except Exception as e:
                print(f"Failed scraping page {page_no}: {e}")
                continue

            if not records:
                print(f"No valid records found on page {page_no}; continuing.")
                continue

            all_records.extend(records)
            await page.wait_for_timeout(150)

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

    print(
        f"STARTING RUN | max_pages={args.max_pages} | headless={args.headless} | "
        f"dry_run={args.dry_run} | open_for_work_only={args.open_for_work_only}",
        flush=True,
    )

    if not MONDAY_API_TOKEN and not args.dry_run:
        raise RuntimeError("MONDAY_API_TOKEN is required unless --dry-run is enabled")

    monday = MondayClient(MONDAY_API_TOKEN) if MONDAY_API_TOKEN else None
    existing = set()

    if monday:
        print("Loading existing monday YTJobs profile links...", flush=True)
        existing = monday.get_existing_profile_links(MONDAY_BOARD_ID, MONDAY_COLUMNS["ytjobs_profile_link"])
        print(f"Existing monday links: {len(existing)}", flush=True)

    records = asyncio.run(scrape(args.max_pages, args.headless))
    print(f"Total scraped valid records: {len(records)}")

    if args.dry_run:
        for rec in records[:50]:
            print(json.dumps(asdict(rec), ensure_ascii=False))
        print("Dry run mode enabled; no monday updates sent.")
        return

    created = 0
    skipped_existing = 0
    skipped_not_open_for_work = 0
    skipped_unmapped = 0
    failed = 0

    created_local_keys: set[str] = set()

    for idx, rec in enumerate(records, start=1):
        normalized_link = _normalize_profile_link(rec.ytjobs_profile_link)
        local_key = normalized_link

        if local_key in created_local_keys:
            skipped_existing += 1
            continue

        if normalized_link in existing:
            skipped_existing += 1
            print(f"[{idx}] Skipping existing monday item: {rec.name} | {normalized_link}")
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
            f"[{idx}] Creating: {rec.name} | role={rec.job_role} | group={group_id} | "
            f"open_for_work={rec.open_for_work} | niche={rec.niche} | views={rec.views} | "
            f"email={rec.email} | location={rec.location} | creators={rec.creators_worked_with} | "
            f"social={_pick_primary_social_text(rec)}"
        )
        print("MONDAY COLUMN VALUES:", json.dumps(values, ensure_ascii=False))

        try:
            monday.create_item(MONDAY_BOARD_ID, group_id, rec.name, values)
            created_local_keys.add(local_key)
            existing.add(normalized_link)
            created += 1
            time.sleep(0.2)
        except Exception as e:
            print(f"[{idx}] First attempt failed for {rec.name}: {e}")
            time.sleep(2)
            try:
                monday.create_item(MONDAY_BOARD_ID, group_id, rec.name, values)
                created_local_keys.add(local_key)
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

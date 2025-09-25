# src/gather.py
from __future__ import annotations

import re
import time
import html
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Dict, Any, Iterable, Optional
from urllib.parse import urlparse, parse_qs, unquote, urlsplit, urlunsplit

import feedparser
import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REQ_TIMEOUT = 12  # seconds
MAX_PARAGRAPHS_PER_ARTICLE = 12  # keep it tight; the TTS step will stitch these

# Conservative default Houston-area education feeds (feel free to expand/modify)
DEFAULT_RSS_FEEDS: List[str] = [
    # Major local outlets
    "https://www.houstonpublicmedia.org/feed/",                         # HPM
    "https://www.khou.com/feeds/rss/category/news/local/education.xml", # KHOU education
    "https://abc13.com/feed/",                                          # ABC13 (sitewide; we filter by terms)
    "https://www.click2houston.com/arcio/rss/category/news/local/",     # KPRC local
    "https://communityimpact.com/houston/feed/",                        # Community Impact (Houston)
    # District & nearby districts — many publish “news” or “press” RSS
    "https://www.houstonisd.org/rss.aspx?DomainID=1&ModuleInstanceID=35946&PageID=1",  # HISD site-wide RSS
    "https://www.fortbendisd.com/site/RSS.aspx?DomainID=1&ModuleInstanceID=581&PageID=1",  # FBISD
    "https://www.katyisd.org/site/RSS.aspx?DomainID=4&ModuleInstanceID=77&PageID=1",       # Katy ISD
    "https://www.aldineisd.org/feed/",                                                     # Aldine ISD (WP)
    "https://kleinisd.net/site/rss.aspx?DomainID=4&ModuleInstanceID=24&PageID=1",         # Klein ISD
]

# --------------------------------------------------------------------------------------
# Utilities
# --------------------------------------------------------------------------------------

def _clean_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _strip_tracking(u: str) -> str:
    """
    Remove obvious tracking params; also unwrap Google News style URLs when seen.
    """
    if not u:
        return u

    # Unwrap news.google.com links like .../articles/....?url=<real>&...
    try:
        parsed = urlparse(u)
        if "news.google." in parsed.netloc:
            qs = parse_qs(parsed.query)
            if "url" in qs and qs["url"]:
                return unquote(qs["url"][0])
    except Exception:
        pass

    # Remove common tracking parameters
    try:
        parts = urlsplit(u)
        query = parts.query
        if query:
            kept = []
            for kv in query.split("&"):
                k = kv.split("=", 1)[0].lower()
                if k.startswith("utm_") or k in {"fbclid", "gclid"}:
                    continue
                kept.append(kv)
            new_query = "&".join(kept)
            u = urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))
    except Exception:
        pass

    return u


def _request_url(url: str) -> Optional[requests.Response]:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            timeout=REQ_TIMEOUT,
            allow_redirects=True,
        )
        # Only accept HTML-ish
        ctype = resp.headers.get("Content-Type", "")
        if "text/html" not in ctype and "application/xhtml" not in ctype:
            return None
        if resp.status_code >= 400:
            return None
        return resp
    except requests.RequestException:
        return None


def _extract_text_from_html(html_text: str) -> str:
    """
    Lightweight text extraction without readability/lxml_html_clean.
    Prioritizes <article>, then main content wrappers, then paragraphs.
    """
    soup = BeautifulSoup(html_text, "html.parser")

    # remove scripts/styles/nav/aside/figcaptions which often add noise
    for bad in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
        bad.decompose()

    # Prefer <article> blocks
    candidates: List[BeautifulSoup] = list(soup.find_all("article"))

    # Common CMS containers when <article> is missing
    if not candidates:
        for sel in [
            "[role=main]",
            ".post-content",
            ".entry-content",
            ".article__content",
            ".c-article-body",
            ".story-body",
            "#main-content",
            ".content",
        ]:
            found = soup.select_one(sel)
            if found:
                candidates.append(found)

    # Fallback to body
    root = candidates[0] if candidates else soup.body or soup

    # Gather paragraphs
    paragraphs: List[str] = []
    for p in root.find_all(["p", "li"]):
        txt = _clean_whitespace(p.get_text(" ", strip=True))
        # drop boilerplate or tiny crumbs
        if len(txt) >= 40 and not txt.lower().startswith(("copyright", "©", "photo:", "video:")):
            paragraphs.append(txt)
        if len(paragraphs) >= MAX_PARAGRAPHS_PER_ARTICLE:
            break

    # Fallback if nothing
    if not paragraphs:
        txt = _clean_whitespace(root.get_text(" ", strip=True))
        return txt[:2000]

    blob = " ".join(paragraphs)
    return blob[:4000]


def _best_published(entry) -> datetime:
    """
    Get a timezone-aware datetime (UTC) from a feed entry, best effort.
    """
    dt: Optional[datetime] = None
    for key in ("published_parsed", "updated_parsed"):
        t = getattr(entry, key, None) or entry.get(key)
        if t:
            try:
                dt = datetime(*t[:6], tzinfo=timezone.utc)
                break
            except Exception:
                pass
    return dt or datetime.now(tz=timezone.utc)


def _host_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _text_matches_terms(text: str, terms: Iterable[str]) -> bool:
    if not terms:
        return True
    t = text.lower()
    return any(term.lower() in t for term in terms)

# --------------------------------------------------------------------------------------
# Core API (used by main.py)
# --------------------------------------------------------------------------------------

@dataclass
class Item:
    title: str
    link: str
    source: str
    published: datetime
    body: str


def fetch_feeds(terms: List[str], rss_feeds: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Parse the given RSS/Atom feeds (or DEFAULT_RSS_FEEDS), fetch each article page,
    extract readable text, and return normalized items.

    Returns a list of dicts with keys: title, link, source, published, body
    """
    feeds = rss_feeds or DEFAULT_RSS_FEEDS
    items: List[Item] = []

    for feed_url in feeds:
        try:
            parsed = feedparser.parse(feed_url)
        except Exception:
            continue

        for entry in parsed.entries or []:
            try:
                # Title / link
                title = _clean_whitespace(entry.get("title", ""))
                link = entry.get("link") or entry.get("id") or ""
                link = _strip_tracking(link)
                if not title or not link:
                    continue

                # Filter by terms across title + summary
                summary = _clean_whitespace(html.unescape(entry.get("summary", "")))
                haystack = f"{title} {summary}"
                if not _text_matches_terms(haystack, terms):
                    continue

                # Fetch article
                resp = _request_url(link)
                if not resp:
                    continue
                final_url = _strip_tracking(resp.url)  # after redirects
                source = _host_of(final_url)

                text = _extract_text_from_html(resp.text)
                if not text or len(text) < 120:
                    # If extraction too thin, skip; avoids “copyright / license” boilerplate
                    continue

                published = _best_published(entry)

                items.append(Item(
                    title=title,
                    link=final_url,
                    source=source,
                    published=published,
                    body=text,
                ))

                # Be gentle with sites
                time.sleep(0.2)

            except Exception:
                # Never let a single bad entry kill the run
                continue

    # Convert dataclass list -> plain dicts for downstream code
    out: List[Dict[str, Any]] = [
        {
            "title": it.title,
            "link": it.link,
            "source": it.source,
            "published": it.published,
            "body": it.body,
        }
        for it in items
    ]
    return out


def select_and_enrich(
    raw_items: List[Dict[str, Any]],
    max_articles: int = 12,
    whitelist_domains: Optional[Iterable[str]] = None,
    max_chars: int = 2000,
) -> List[Dict[str, Any]]:
    """
    - Deduplicate by canonical link
    - (Optional) Filter to whitelist of domains (netloc match; 'www.' stripped)
    - Sort newest first
    - Truncate body to max_chars
    """
    if whitelist_domains:
        wl = {d.lower().lstrip().rstrip().removeprefix("www.") for d in whitelist_domains}
    else:
        wl = None

    seen: set[str] = set()
    cleaned: List[Dict[str, Any]] = []

    for it in raw_items:
        link = _strip_tracking(it.get("link", ""))
        if not link or link in seen:
            continue
        seen.add(link)

        host = _host_of(link).removeprefix("www.")
        if wl and host not in wl:
            continue

        body = it.get("body", "")
        if len(body) > max_chars:
            body = body[:max_chars].rsplit(" ", 1)[0] + "…"

        cleaned.append({
            "title": _clean_whitespace(it.get("title", "")),
            "link": link,
            "source": host or it.get("source", ""),
            "published": it.get("published") or datetime.now(tz=timezone.utc),
            "body": body,
        })

    # newest first
    cleaned.sort(key=lambda x: x["published"], reverse=True)
    return cleaned[:max_articles]

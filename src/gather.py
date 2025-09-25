# src/gather.py
# Robust feed gatherer that avoids Google News redirects and heavy HTML libs.
# Compatible with main.py: provides fetch_feeds(...) and select_and_enrich(...)

from __future__ import annotations

import re
import html
import time
import urllib.parse as up
from typing import List, Dict, Any, Optional, Iterable
from datetime import datetime, timezone

import requests
import feedparser
from bs4 import BeautifulSoup
from dateutil import parser as dtparse


# --------- Config: default RSS feeds (safe, direct sources; no Google News) ----------
DEFAULT_RSS_FEEDS: List[str] = [
    # Regionals / education beats around Houston
    "https://www.houstonchronicle.com/neighborhood/feed/education",  # may 302 if paywalled; handled
    "https://www.communityimpact.com/feeds/houston/education.rss",
    "https://www.click2houston.com/arc/outboundfeeds/rss/category/news/education/?outputType=xml",
    "https://abc13.com/feed/education/",
    # Districts (official news pages that expose RSS; some may be atom)
    "https://www.houstonisd.org/site/RSS.aspx?DomainID=4&ModuleInstanceID=35923&PageID=1&PMIID=35922",  # HISD
    "https://www.katyisd.org/site/RSS.aspx?DomainID=4&ModuleInstanceID=52&PageID=1&PMIID=50",
    "https://www.fortbendisd.com/site/RSS.aspx?DomainID=4&ModuleInstanceID=12&PageID=1&PMIID=10",
    "https://www.springbranchisd.com/site/RSS.aspx?DomainID=4&ModuleInstanceID=12&PageID=1&PMIID=10",
    "https://www.springisd.org/site/RSS.aspx?DomainID=4&ModuleInstanceID=12&PageID=1&PMIID=10",
    "https://www.aliefisd.net/site/RSS.aspx?DomainID=4&ModuleInstanceID=12&PageID=1&PMIID=10",
    "https://www.pasadenaisd.org/site/RSS.aspx?DomainID=4&ModuleInstanceID=12&PageID=1&PMIID=10",
    # Community papers frequently covering ISDs
    "https://www.katytimes.com/search/?f=rss&l=25&t=article&cs=news%2Ceducation,schools&sd=desc",
    "https://www.fortbendstar.com/search/?f=rss&l=25&t=article&cs=news%2Ceducation,schools&sd=desc",
    "https://www.baytownsun.com/search/?f=rss&l=25&t=article&cs=news%2Ceducation,schools&sd=desc",
]

# Friendly UA to avoid anti-bot JS shells (e.g., Angular placeholders)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml"})


# --------- Utilities ---------
def _tznow() -> datetime:
    return datetime.now(timezone.utc)


def _norm_domain(url: str) -> str:
    try:
        host = up.urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def _strip_tracking(u: str) -> str:
    try:
        p = up.urlparse(u)
        q = up.parse_qs(p.query)
        # If it's a redirector carrying the true url in `url` or `u`
        for key in ("url", "u"):
            if key in q and q[key]:
                return _strip_tracking(q[key][0])
        # Remove typical tracking params
        clean_q = {k: v for k, v in q.items() if not k.lower().startswith(("utm_", "gclid", "fbclid"))}
        return up.urlunparse(p._replace(query=up.urlencode({k: v[0] for k, v in clean_q.items()})))
    except Exception:
        return u


def _looks_google_proxy(u: str) -> bool:
    d = _norm_domain(u)
    return d in {
        "news.google.com",
        "google.com",
        "g.co",
        "gstatic.com",
        "news.url.google.com",
    }


def _parse_dt(entry: Any) -> datetime:
    # prefer published_parsed / updated_parsed; fallback parse
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            return datetime(*val[:6], tzinfo=timezone.utc)
    for key in ("published", "updated", "date"):
        txt = entry.get(key)
        if txt:
            try:
                return dtparse.parse(txt).astimezone(timezone.utc)
            except Exception:
                pass
    return _tznow()


def _clean_text(t: str) -> str:
    t = html.unescape(t or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _fetch_article_text(url: str, max_chars: int = 2000) -> str:
    """Fetch article and return a readable summary (first few paragraphs)."""
    try:
        r = HTTP.get(url, timeout=12, allow_redirects=True)
        r.raise_for_status()
        s = BeautifulSoup(r.text, "html.parser")

        # Common boilerplate to drop
        for tag in s.select(
            "script,style,nav,footer,header,form,aside,[role='navigation'],"
            ".ad,.advertisement,.promo,.paywall,.subscribe,.social-share"
        ):
            tag.decompose()

        # Prefer article container if present
        article = s.find("article") or s
        paragraphs = []
        for p in article.find_all(["p", "li"]):
            txt = _clean_text(p.get_text(" ", strip=True))
            if txt and len(txt) > 40:  # skip tiny nav crumbs
                paragraphs.append(txt)
            if sum(len(x) for x in paragraphs) >= max_chars:
                break

        body = " ".join(paragraphs)
        # Fallback to meta description if we got nothing useful
        if len(body) < 140:
            meta = s.find("meta", attrs={"name": "description"}) or s.find("meta", attrs={"property": "og:description"})
            if meta and meta.get("content"):
                body = _clean_text(meta["content"])
        return body[:max_chars].rstrip()
    except Exception:
        return ""


# --------- Public API ---------
def fetch_feeds(terms: List[str], rss_feeds: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Pull items from a list of RSS/Atom feeds.
    - terms: used only for later filtering in select_and_enrich (we still collect broadly here)
    - rss_feeds: optional override/extension; if None, uses DEFAULT_RSS_FEEDS
    Returns raw items with keys: title, link, source, published, summary
    """
    feeds = list(DEFAULT_RSS_FEEDS)
    if rss_feeds:
        # de-duplicate while retaining order
        seen = set(_strip_tracking(f) for f in feeds)
        for f in rss_feeds:
            u = _strip_tracking(f)
            if u not in seen:
                feeds.append(u)
                seen.add(u)

    items: List[Dict[str, Any]] = []
    for f in feeds:
        try:
            parsed = feedparser.parse(f)
            for e in parsed.entries:
                link = e.get("link") or ""
                if not link:
                    continue

                # Try to resolve redirectors to the true publisher URL
                clean_link = _strip_tracking(link)
                if _looks_google_proxy(clean_link):
                    # Skip Google proxy links entirely (they caused Angular/gstatic noise)
                    continue

                title = _clean_text(e.get("title", ""))
                if not title:
                    continue

                source = _norm_domain(clean_link) or _norm_domain(f)
                published = _parse_dt(e)
                summary = _clean_text(e.get("summary", ""))

                items.append(
                    {
                        "title": title,
                        "link": clean_link,
                        "source": source,
                        "published": published,
                        "summary": summary,
                    }
                )
        except Exception as ex:
            print(f"[gather] feed error {f}: {ex}")
            continue

        # Be a polite citizen
        time.sleep(0.2)

    return items


def select_and_enrich(
    raw_items: List[Dict[str, Any]],
    max_articles: int = 12,
    whitelist_domains: Optional[Iterable[str]] = None,
    max_chars: int = 2000,
    must_match_terms: bool = False,  # accepted for compatibility; implemented below
    terms: Optional[List[str]] = None,  # if provided, apply matching here
    **_ignored: Any,  # swallow any future args to avoid TypeError
) -> List[Dict[str, Any]]:
    """
    Filter, de-duplicate, fetch article bodies, and return enriched items with keys:
    title, link, source, published (aware), body
    """
    wl: Optional[set[str]] = None
    if whitelist_domains:
        wl = {(_norm_domain(d) or d).lower().lstrip("www.") for d in whitelist_domains if d}

    # Dedup by final link
    seen_links: set[str] = set()
    filtered: List[Dict[str, Any]] = []
    for it in raw_items:
        link = _strip_tracking(it["link"])
        dom = _norm_domain(link)
        if wl and dom not in wl:
            continue
        if link in seen_links:
            continue

        title = it["title"]
        # Optional keyword matching
        if terms:
            title_low = title.lower()
            summ_low = (it.get("summary") or "").lower()
            matches = any(t.lower() in title_low or t.lower() in summ_low for t in terms if t.strip())
            if must_match_terms and not matches:
                continue
            # If not must_match_terms, we still keep items even if no match

        seen_links.add(link)
        filtered.append(it)

    # Sort newest first
    filtered.sort(key=lambda x: x.get("published") or _tznow(), reverse=True)

    # Enrich with body text
    enriched: List[Dict[str, Any]] = []
    for it in filtered[: max_articles * 2]:  # pull a few extra in case some bodies fail
        body = _fetch_article_text(it["link"], max_chars=max_chars)
        if not body and must_match_terms:
            # If we require matching terms and have no body to check against, skip it
            continue

        enriched.append(
            {
                "title": it["title"],
                "link": it["link"],
                "source": it["source"],
                "published": it.get("published") or _tznow(),
                "body": body or (it.get("summary") or ""),
            }
        )
        if len(enriched) >= max_articles:
            break

    return enriched


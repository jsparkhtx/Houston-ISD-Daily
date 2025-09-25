# src/gather.py
#
# Collect candidate stories and enrich them with clean text bodies.
# Focused on Greater Houston ISDs via a Google News RSS query + robust
# unwrapping of aggregator links.

from __future__ import annotations

import re
import time
import html
import logging
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, urlsplit, parse_qs, quote_plus

import requests
import feedparser
from bs4 import BeautifulSoup
from readability import Document
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone

# ---------- config / constants ----------

HTTP_TIMEOUT = 12
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})

HOUSTON_ISD_TERMS = [
    "Houston ISD", "HISD",
    "Aldine ISD", "Alief ISD", "Channelview ISD", "Clear Creek ISD",
    "Crosby ISD", "Cy-Fair ISD", "Cypress-Fairbanks ISD", "Fort Bend ISD", "FBISD",
    "Galena Park ISD", "Goose Creek CISD", "Huffman ISD", "Humble ISD",
    "Katy ISD", "Klein ISD", "Pasadena ISD", "Sheldon ISD",
    "Spring ISD", "Spring Branch ISD",
]

# ---------- helpers ----------

def _hostname(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""

def _is_google_wrapper(url: str) -> bool:
    host = _hostname(url)
    return (
        host.endswith("news.google.com") or
        host.endswith("google.com") or
        host.endswith("gstatic.com")
    )

def _unwrap_google_url(url: str) -> str:
    """
    For Google News RSS links, try to extract the real publisher URL.
    We try in this order:
      1) 'url' query param (many items include it)
      2) Follow HEAD/GET redirects to the ultimate destination
    """
    try:
        qs = parse_qs(urlsplit(url).query)
        candidate = qs.get("url", [None])[0]
        if candidate:
            return candidate
    except Exception:
        pass

    # Fallback: follow redirects
    try:
        # HEAD first; if some sites block HEAD, fall back to GET with stream=True
        r = SESSION.head(url, allow_redirects=True, timeout=HTTP_TIMEOUT)
        if 300 <= r.status_code < 400 and "Location" in r.headers:
            return r.headers["Location"]
        if r.url and r.url != url:
            return r.url
    except Exception:
        try:
            r = SESSION.get(url, allow_redirects=True, timeout=HTTP_TIMEOUT, stream=True)
            final = r.url or url
            r.close()
            return final
        except Exception:
            pass
    return url

def _clean_text(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _to_aware(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def _recent_only(items: List[Dict[str, Any]], hours: int = 24) -> List[Dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for it in items:
        try:
            pub = it.get("published")
            if isinstance(pub, datetime) and pub >= cutoff:
                out.append(it)
        except Exception:
            continue
    return out

def _google_query_feed(terms: List[str]) -> str:
    """
    Build a Google News RSS 'search' feed URL, properly URL-encoding the query.
    We avoid quotes around phrases — Google treats words together,
    and quote_plus handles spaces safely.
    """
    q = " OR ".join(terms) if terms else " OR ".join(HOUSTON_ISD_TERMS)
    q_enc = quote_plus(q)
    # when:24h -> last 24 hours; hl/gl/ceid to force English/US.
    return f"https://news.google.com/rss/search?q={q_enc}+when%3A24h&hl=en-US&gl=US&ceid=US%3Aen"

# ---------- fetching ----------

def _fetch_feed(url: str) -> List[Dict[str, Any]]:
    """
    Parse an RSS/Atom feed and return basic entries with best-guess source URLs.
    """
    rv: List[Dict[str, Any]] = []
    try:
        parsed = feedparser.parse(url)
    except Exception as e:
        logging.warning("[gather] feed parse error %s: %s", url, e)
        return rv

    for e in parsed.entries:
        link = e.get("link") or ""
        if not link:
            continue

        # Try to make the link the real publisher URL
        final_link = _unwrap_google_url(link) if _is_google_wrapper(link) else link
        host = _hostname(final_link) or _hostname(link)

        # Published time -> aware datetime
        pub_dt: Optional[datetime] = None
        if "published_parsed" in e and e.published_parsed:
            try:
                pub_dt = datetime.fromtimestamp(time.mktime(e.published_parsed), tz=timezone.utc)
            except Exception:
                pub_dt = None
        elif "published" in e:
            try:
                pub_dt = parsedate_to_datetime(e.published)
            except Exception:
                pub_dt = None

        rv.append({
            "title": _clean_text(e.get("title", "")),
            "link": final_link,
            "raw_link": link,
            "source": host or "",
            "published": _to_aware(pub_dt),
            "summary": _clean_text(e.get("summary", "")),
        })

    return rv

def fetch_feeds(terms: List[str]) -> List[Dict[str, Any]]:
    """
    Entry point used by main.py
    Returns raw, recent (<=24h) items from Google News search feed.
    """
    url = _google_query_feed(terms)
    entries = _fetch_feed(url)
    # Deduplicate by (title, source)
    seen = set()
    uniq: List[Dict[str, Any]] = []
    for it in entries:
        key = (it["title"].lower(), it["source"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    return _recent_only(uniq, hours=24)

# ---------- enrichment (read body text) ----------

def _og_description(soup: BeautifulSoup) -> Optional[str]:
    meta = soup.find("meta", attrs={"property": "og:description"})
    if meta and meta.get("content"):
        return _clean_text(meta["content"])
    meta2 = soup.find("meta", attrs={"name": "description"})
    if meta2 and meta2.get("content"):
        return _clean_text(meta2["content"])
    return None

def _paragraphs(soup: BeautifulSoup) -> str:
    parts: List[str] = []
    for p in soup.find_all("p"):
        txt = _clean_text(p.get_text(" ", strip=True))
        if len(txt) >= 40:  # skip nav/short crumbs
            parts.append(txt)
        if sum(len(x) for x in parts) > 3000:
            break
    return " ".join(parts)

def _extract_body(url: str, max_chars: int = 2000) -> str:
    """
    Try (1) Readability main content, (2) OG/description, (3) <p> fallback.
    """
    try:
        r = SESSION.get(url, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        html_text = r.text
    except Exception:
        return ""

    # Some sites block Readability-lxml on malformed markup — always have fallbacks.
    try:
        doc = Document(html_text)
        content_html = doc.summary(html_partial=True)
        soup = BeautifulSoup(content_html, "html.parser")
        text = _clean_text(soup.get_text(" ", strip=True))
        if len(text) < 120:  # too short → try full page soup instead
            soup_full = BeautifulSoup(html_text, "html.parser")
            og = _og_description(soup_full)
            if og and len(og) > 40:
                text = og
            else:
                text = _paragraphs(soup_full)
    except Exception:
        soup_full = BeautifulSoup(html_text, "html.parser")
        og = _og_description(soup_full)
        if og and len(og) > 40:
            text = og
        else:
            text = _paragraphs(soup_full)

    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    return text

def select_and_enrich(
    raw_items: List[Dict[str, Any]],
    max_articles: int = 12,
    whitelist_domains: Optional[List[str]] = None,
    max_chars: int = 2000,
) -> List[Dict[str, Any]]:
    """
    Filter + enrich with body text.
    - If whitelist_domains is provided, only keep items whose source ends with one of those domains.
    - Trim to max_articles after enrichment priority (newest first).
    """
    items = sorted(raw_items, key=lambda x: x.get("published", datetime.now(timezone.utc)), reverse=True)

    if whitelist_domains:
        wl = {d.strip().lower().lstrip("www.") for d in whitelist_domains if d and d.strip()}
        def ok(src: str) -> bool:
            s = (src or "").lower().lstrip("www.")
            return any(s == d or s.endswith("." + d) for d in wl)
        items = [it for it in items if ok(it.get("source", ""))]

    enriched: List[Dict[str, Any]] = []
    for it in items:
        body = _extract_body(it["link"], max_chars=max_chars)
        if not body:
            # As a last resort, keep the summary if it has substance
            body = it.get("summary") or ""
        body = _clean_text(body)
        if not body:
            continue
        enriched.append({
            **it,
            "body": body,
        })
        if len(enriched) >= max_articles:
            break

    return enriched

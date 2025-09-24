# src/gather.py
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urlparse, urlsplit

import feedparser
import requests
from bs4 import BeautifulSoup
from readability import Document


# -----------------------------
# Robust publisher-link unwrap
# -----------------------------
def _unwrap_google_news_link(entry) -> str:
    """
    Try hard to get the real publisher URL from a Google News RSS entry.
    Priority:
      1) feedburner_origlink (some feeds)
      2) any entry.links[].href not pointing to news.google.com
      3) first <a href> in the summary that is not news.google.com
      4) link's ?url= param if present
      5) follow redirects once and take final URL if it leaves news.google.com
      6) fallback to entry.link
    """
    # 1) feedburner_origlink
    try:
        orig = getattr(entry, "feedburner_origlink", None) or entry.get("feedburner_origlink")
        if orig and "news.google.com" not in orig:
            return orig
    except Exception:
        pass

    # 2) entry.links[] real href
    try:
        for l in entry.get("links", []):
            href = l.get("href")
            if href and "news.google.com" not in href:
                return href
    except Exception:
        pass

    # 3) URL inside the summary html
    try:
        sm = entry.get("summary", "") or ""
        if sm:
            for href in _hrefs_from_html(sm):
                if "news.google.com" not in href:
                    return href
    except Exception:
        pass

    # 4) ?url= param on entry.link
    try:
        link = entry.link
        parsed = urlsplit(link)
        if "news.google.com" in parsed.netloc:
            qs = parse_qs(parsed.query)
            if "url" in qs and qs["url"]:
                real = unquote(qs["url"][0])
                if real:
                    return real
    except Exception:
        pass

    # 5) follow redirect once
    try:
        link = entry.link
        r = requests.get(
            link,
            timeout=10,
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code == 200 and r.url and "news.google.com" not in r.url:
            return r.url
    except Exception:
        pass

    # 6) fallback
    try:
        return entry.link
    except Exception:
        return ""


def _hrefs_from_html(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    hrefs = []
    for a in soup.find_all("a", href=True):
        hrefs.append(a["href"])
    return hrefs


def _domain_of(url: str) -> str:
    try:
        d = urlparse(url).netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return "unknown"


# -----------------------------
# Fetch
# -----------------------------
def fetch_feeds(terms: List[str]) -> List[Dict[str, Any]]:
    """Fetch Google News RSS results for each term (last 2 days)."""
    all_items: List[Dict[str, Any]] = []
    for term in terms:
        q = quote_plus(f'"{term}"')  # ensure phrase, URL-encoded
        url = f"https://news.google.com/rss/search?q={q}+when:2d&hl=en-US&gl=US&ceid=US:en"
        feed = feedparser.parse(url)
        print(f"[gather] term={term!r} fetched={len(feed.entries)} url={url}")

        for entry in feed.entries:
            link = _unwrap_google_news_link(entry)
            dom = _domain_of(link)
            title = entry.title

            item = {
                "title": title,
                "link": link,
                "published": _parse_date(entry.get("published")),
                "summary": entry.get("summary", ""),
                "source": dom,
            }
            print(f"[gather] candidate: {title} — {dom} — {link}")
            all_items.append(item)

    return all_items


# -----------------------------
# Select & Enrich
# -----------------------------
def select_and_enrich(
    items: List[Dict[str, Any]],
    max_articles: int,
    whitelist_domains: Optional[List[str]] = None,
    max_chars: int = 2000,
    must_match_terms: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Deduplicate, optionally filter by domain/keywords, and enrich with body text.
    Extraction order: readability -> soup <p> -> cleaned RSS summary fallback.
    """
    seen = set()
    selected: List[Dict[str, Any]] = []

    wl = set((d or "").strip().lower().lstrip("www.") for d in (whitelist_domains or []))
    mm = [t.strip().lower() for t in (must_match_terms or []) if t and t.strip()]

    for it in sorted(items, key=lambda x: x["published"] or datetime.utcnow(), reverse=True):
        url = it["link"]
        dom = it.get("source") or _domain_of(url)
        key = (it["title"], url)
        if key in seen:
            continue

        # keyword guardrail: require 'isd' or a district phrase in title/summary
        title_l = (it.get("title") or "").lower()
        summary_l = (it.get("summary") or "").lower()
        if mm and not _title_or_summary_matches(title_l, summary_l, mm):
            print(f"[gather] SKIP (no ISD/district match): {it['title']}")
            continue

        if wl and dom not in wl:
            print(f"[gather] SKIP (domain not whitelisted): {it['title']} — {dom}")
            continue

        # extract body text
        body, method = _extract_body(url)

        # fallback to RSS summary if extractor came up short
        if not body or len(body) < 200:
            fallback = _clean_html(it.get("summary", "") or "")
            if fallback and len(fallback) > (len(body) if body else 0):
                body = fallback
                method = "rss-summary"

        if not body:
            print(f"[gather] SKIP (no usable body): {it['title']} — {dom}")
            continue

        body = strip_boilerplate(body)
        if len(body) > max_chars:
            body = body[:max_chars] + "…"

        it["body"] = body
        it["source"] = dom
        snip = body[:120].replace("\n", " ")
        print(f"[gather] KEPT via {method}: {it['title']} — {dom} — len={len(body)} — {snip}…")

        selected.append(it)
        seen.add(key)
        if len(selected) >= max_articles:
            break

    print(f"[gather] selected {len(selected)} articles (limit={max_articles})")
    return selected


def _title_or_summary_matches(title_l: str, summary_l: str, terms: Iterable[str]) -> bool:
    if "isd" in title_l or "isd" in summary_l:
        return True
    for t in terms:
        if t in title_l or t in summary_l:
            return True
    return False


# -----------------------------
# Extraction
# -----------------------------
def _extract_body(url: str) -> Tuple[Optional[str], str]:
    """Return (text, method_used)."""
    html: Optional[str] = None

    # 1) readability
    try:
        html = _get(url)
        if html:
            doc = Document(html)
            article_html = doc.summary(html_partial=True)
            text = _clean_html(article_html)
            if text and len(text) >= 200:
                return text, "readability"
    except Exception as e:
        print(f"[gather] readability ERROR for {url}: {e}")

    # 2) soup <p>
    try:
        html = html or _get(url)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
            text = " ".join(paragraphs)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                return text, "soup"
    except Exception as e:
        print(f"[gather] soup ERROR for {url}: {e}")

    return None, "none"


def _get(url: str) -> Optional[str]:
    try:
        r = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            print(f"[gather] ERROR {r.status_code} fetching {url}")
            return None
        return r.text
    except Exception as e:
        print(f"[gather] GET ERROR for {url}: {e}")
        return None


# -----------------------------
# Text utilities
# -----------------------------
def strip_boilerplate(text: str) -> str:
    bads = [
        "Subscribe to our newsletter",
        "All rights reserved",
        "Continue Reading",
        "Advertisement",
        "Ad – ",
        "Sign up for our",
        "Copyright",
    ]
    for b in bads:
        text = text.replace(b, " ")
    return re.sub(r"\s+", " ", text).strip()


def _clean_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(pub: Optional[str]):
    try:
        return datetime(*feedparser._parse_date(pub)[:6])
    except Exception:
        return datetime.utcnow()

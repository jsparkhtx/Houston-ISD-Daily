# src/gather.py
# Fetch RSS items, resolve real publisher URLs, extract readable article text,
# and return enriched items ready for TTS.

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse, urlsplit

import feedparser
import requests
from bs4 import BeautifulSoup
from readability import Document

# -------- HTTP helpers --------
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_TIMEOUT = 15


def _get(url: str) -> requests.Response:
    return requests.get(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
        allow_redirects=True,
        timeout=_TIMEOUT,
    )


# -------- URL utilities --------
_GOOGLE_NEWS = {"news.google.com", "news.url.google.com", "www.google.com"}
_BLOCKED = {"gstatic.com", "www.gstatic.com"}


def _domain(u: str | None) -> str:
    if not u:
        return ""
    try:
        return urlsplit(u).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _from_google_redirect(u: str) -> Optional[str]:
    """news.google.com/rss links carry ?url= (or ?u=) to the origin site."""
    try:
        p = urlparse(u)
        if p.netloc in _GOOGLE_NEWS:
            qs = parse_qs(p.query)
            cand = (qs.get("url") or qs.get("u") or [None])[0]
            if cand:
                return cand
    except Exception:
        pass
    return None


def _best_link(entry: Dict[str, Any]) -> Optional[str]:
    for k in ("feedburner_origlink", "origLink", "originallink"):
        v = entry.get(k)
        if v:
            return v
    if entry.get("link"):
        return _from_google_redirect(entry["link"]) or entry["link"]
    for l in entry.get("links") or []:
        if isinstance(l, dict) and l.get("href"):
            return _from_google_redirect(l["href"]) or l["href"]
    return None


# -------- text helpers --------
def _clean_html(s: str) -> str:
    if not s:
        return ""
    s = BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
    return html.unescape(s)


def _readability_text(html_text: str) -> str:
    doc = Document(html_text)
    frag = doc.summary(html_partial=True)
    return _clean_html(frag)


def _bs_text(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "html.parser")
    art = soup.find("article")
    if art:
        t = art.get_text(" ", strip=True)
        if len(t) > 120:
            return t
    parts: List[str] = []
    for p in soup.find_all("p"):
        t = p.get_text(" ", strip=True)
        if t:
            parts.append(t)
    return " ".join(parts)


def _og_desc(u: str) -> Optional[str]:
    try:
        r = _get(u)
        soup = BeautifulSoup(r.text, "html.parser")
        tag = soup.find("meta", property="og:description") or soup.find(
            "meta", attrs={"name": "description"}
        )
        if tag and tag.get("content"):
            return _clean_html(tag["content"])
    except Exception:
        pass
    return None


def _extract_body(u: str) -> Tuple[str, str]:
    r = _get(u)
    try:
        t = _readability_text(r.text)
        if len(t) > 200:
            return t, "readability"
    except Exception:
        pass
    try:
        t = _bs_text(r.text)
        if len(t) > 200:
            return t, "soup"
    except Exception:
        pass
    d = _og_desc(r.url or u)
    if d:
        return d, "og:description"
    return "", "none"


# -------- feed collection --------
def _google_query_feed(terms: List[str]) -> str:
    if terms:
        q = " OR ".join(f'"{t}"' if " " in t else t for t in terms)
    else:
        q = "Houston ISD OR Fort Bend ISD OR Katy ISD"
    return f"https://news.google.com/rss/search?q={q}+when:24h&hl=en-US&gl=US&ceid=US:en"


def fetch_feeds(terms: List[str], extra_feeds: List[str]) -> List[Dict[str, Any]]:
    feed_urls: List[str] = list(extra_feeds or [])
    feed_urls.append(_google_query_feed(terms))

    items: List[Dict[str, Any]] = []
    for fu in feed_urls:
        try:
            parsed = feedparser.parse(fu)
            for e in parsed.entries:
                link = _best_link(e) or ""
                dom = _domain(link) or _domain(e.get("link", ""))
                title = _clean_html(e.get("title", ""))
                summary = _clean_html(e.get("summary", ""))

                published = datetime.now(timezone.utc)
                if getattr(e, "published_parsed", None):
                    published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)

                items.append(
                    {
                        "title": title,
                        "summary": summary,
                        "link": link,
                        "source": dom,
                        "published": published,
                    }
                )
        except Exception as ex:
            print(f"[gather] feed error {fu}: {ex}")

    items.sort(key=lambda x: x["published"], reverse=True)
    print(f"[gather] fetched {len(items)} raw entries from {len(feed_urls)} feeds")
    return items


# -------- selection/enrichment --------
def strip_boilerplate(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    # cut common license/copyright tails if they appear far into the text
    m = re.search(r"(?i)(all rights reserved|the software is provided \"as is\")", text)
    if m and m.start() > 300:
        text = text[: m.start()].rstrip()
    return text


def select_and_enrich(
    items: List[Dict[str, Any]],
    max_articles: int,
    whitelist_domains: Optional[List[str]] = None,
    max_chars: int = 2000,
    must_match_terms: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    MIN_BODY = 120
    MIN_SUMMARY = 60

    wl = set((d or "").strip().lower().lstrip("www.") for d in (whitelist_domains or []))
    terms_l = [t.strip().lower() for t in (must_match_terms or []) if t and t.strip()]
    kept: List[Dict[str, Any]] = []
    seen = set()

    for it in items:
        url = it.get("link") or ""
        real = _from_google_redirect(url) or url
        dom = _domain(real)

        if not real or dom in _BLOCKED:
            print(f"[gather] SKIP blocked/empty: {it.get('title')}")
            continue
        if wl and dom not in wl:
            print(f"[gather] SKIP not whitelisted: {dom} | {it.get('title')}")
            continue
        key = (it.get("title"), real)
        if key in seen:
            continue

        hay = f"{(it.get('title') or '').lower()} {(it.get('summary') or '').lower()}"
        if terms_l and not any(t in hay for t in terms_l):
            print(f"[gather] SKIP no-term-match: {dom} | {it.get('title')}")
            continue

        body, method = _extract_body(real)
        if not body or len(body) < MIN_BODY:
            og = _og_desc(real)
            if og and len(og) >= MIN_BODY:
                body, method = og, "og:description"
        if (not body or len(body) < MIN_BODY) and it.get("summary"):
            s = _clean_html(it["summary"])
            if s and len(s) >= MIN_BODY:
                body, method = s, "rss-summary"

        if not body or len(body) < MIN_BODY:
            print(f"[gather] SKIP no-usable-body: {dom} | {it.get('title')}")
            continue

        body = strip_boilerplate(body)
        if len(body) > max_chars:
            body = body[:max_chars] + "…"

        it["body"] = body
        it["source"] = dom
        it["link"] = real
        kept.append(it)
        seen.add(key)
        print(f"[gather] KEPT via {method}: {dom} | len={len(body)} | {it['title']}")

        if len(kept) >= max_articles:
            break

    if not kept:
        print("[gather] backstop: summary-only")
        for it in items:
            real = _from_google_redirect(it.get("link") or "") or (it.get("link") or "")
            dom = _domain(real)
            if wl and dom not in wl:
                continue
            s = _clean_html(it.get("summary") or "")
            if s and len(s) >= MIN_SUMMARY:
                s = strip_boilerplate(s)
                if len(s) > max_chars:
                    s = s[:max_chars] + "…"
                it["body"] = s
                it["source"] = dom
                it["link"] = real
                kept.append(it)
                if len(kept) >= max_articles:
                    break

    print(f"[gather] selected {len(kept)} articles (limit={max_articles})")
    return kept

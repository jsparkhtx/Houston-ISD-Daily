# src/gather.py
# Utilities to collect RSS items, resolve real publisher links (not news.google),
# and extract readable article text with multiple fallbacks.

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

# ---------- HTTP helpers ----------

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_REQ_TIMEOUT = 15


def _http_get(url: str) -> requests.Response:
    """GET with reasonable headers and redirects enabled."""
    return requests.get(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
        allow_redirects=True,
        timeout=_REQ_TIMEOUT,
    )


# ---------- URL / feed helpers ----------

_GOOGLE_HOSTS = {"news.google.com", "news.url.google.com", "www.google.com"}
_GSTATIC_HOSTS = {"www.gstatic.com", "gstatic.com"}


def _domain_of(url: str | None) -> str:
    if not url:
        return ""
    try:
        return urlsplit(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _is_blocked(url: str) -> bool:
    d = _domain_of(url)
    return d in _GSTATIC_HOSTS


def _maybe_original_from_google(link: str) -> Optional[str]:
    """
    If a Google News RSS link contains ?url=<orig>, return that original URL.
    """
    try:
        p = urlparse(link)
        if p.netloc in _GOOGLE_HOSTS:
            qs = parse_qs(p.query)
            u = qs.get("url") or qs.get("u")
            if u and isinstance(u, list) and u[0]:
                return u[0]
    except Exception:
        pass
    return None


def _entry_best_link(entry: Dict[str, Any]) -> Optional[str]:
    """
    Try hard to get the real publisher URL from a feed entry.
    Handles Google News, feedburner, and generic 'links'.
    """
    # 1) feedburner/original link fields some feeds include
    for k in ("feedburner_origlink", "origLink", "originallink"):
        v = entry.get(k)
        if v:
            return v

    # 2) common places
    link = entry.get("link") or entry.get("id")
    if link:
        # news.google.com -> extract ?url=
        real = _maybe_original_from_google(link)
        if real:
            return real
        # otherwise use as-is
        return link

    # 3) links array
    for l in entry.get("links") or []:
        if isinstance(l, dict) and l.get("href"):
            real = _maybe_original_from_google(l["href"])
            return real or l["href"]

    return None


def _clean_html(s: str) -> str:
    if not s:
        return ""
    s = BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
    return html.unescape(s)


# ---------- extraction ----------

_PARAGRAPH_RE = re.compile(r"\S")


def _readability_extract(html_text: str) -> str:
    doc = Document(html_text)
    cleaned = doc.summary(html_partial=True)
    text = _clean_html(cleaned)
    return text


def _bs4_extract(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "html.parser")

    # Try article tag first
    art = soup.find("article")
    if art:
        text = art.get_text(" ", strip=True)
        if text and len(text) > 100:
            return text

    # Otherwise collect paragraphs that look like content
    paras: List[str] = []
    for p in soup.find_all("p"):
        t = p.get_text(" ", strip=True)
        if t and _PARAGRAPH_RE.search(t):
            paras.append(t)
    return " ".join(paras)


def _fetch_og_description(url: str) -> Optional[str]:
    try:
        r = _http_get(url)
        soup = BeautifulSoup(r.text, "html.parser")
        tag = soup.find("meta", property="og:description") or soup.find(
            "meta", attrs={"name": "description"}
        )
        if tag and tag.get("content"):
            return _clean_html(tag["content"])
    except Exception:
        pass
    return None


def _extract_body(url: str) -> Tuple[str, str]:
    """
    Return (text, method_used). method_used is for logging.
    """
    r = _http_get(url)
    # If Google redirected us transparently, use final URL for domain/whitelist later
    final_url = r.url or url

    # 1) readability
    try:
        text = _readability_extract(r.text)
        if text and len(text) > 200:
            return text, "readability"
    except Exception:
        pass

    # 2) bs4 paragraph fallback
    try:
        text = _bs4_extract(r.text)
        if text and len(text) > 200:
            return text, "soup"
    except Exception:
        pass

    # 3) og:description as last resort
    og = _fetch_og_description(final_url)
    if og:
        return og, "og:description"

    return "", "none"


# ---------- selection pipeline ----------

def _title_or_summary_matches(title_l: str, summary_l: str, terms_l: List[str]) -> bool:
    hay = f"{title_l} {summary_l}"
    return any(t in hay for t in terms_l)


def fetch_feeds(terms: List[str]) -> List[Dict[str, Any]]:
    """
    Build a small set of Google News RSS queries from the ISD terms.
    We resolve each entry to original publisher URLs in select_and_enrich().
    """
    feeds: List[str] = []
    base = "https://news.google.com/rss/search?q={q}+when:24h&hl=en-US&gl=US&ceid=US:en"
    # Join terms into a single OR query to keep the feed list short
    if terms:
        query = " OR ".join(f'"{t}"' if " " in t else t for t in terms)
    else:
        query = "Houston ISD OR Fort Bend ISD OR Katy ISD"
    feeds.append(base.format(q=query))

    out: List[Dict[str, Any]] = []
    for url in feeds:
        try:
            parsed = feedparser.parse(url)
            for e in parsed.entries:
                link = _entry_best_link(e) or ""
                dom = _domain_of(link) or _domain_of(e.get("link", ""))
                title = _clean_html(e.get("title", ""))
                summary = _clean_html(e.get("summary", ""))

                # published
                published = datetime.now(timezone.utc)
                if getattr(e, "published_parsed", None):
                    published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)

                out.append(
                    {
                        "title": title,
                        "summary": summary,
                        "link": link,
                        "source": dom,
                        "published": published,
                    }
                )
        except Exception as ex:
            print(f"[gather] feed error {url}: {ex}")

    # Newest first
    out.sort(key=lambda x: x["published"], reverse=True)
    print(f"[gather] fetched {len(out)} raw entries")
    return out


def strip_boilerplate(text: str) -> str:
    # light trimming of repeated whitespace and boilerplatey tails
    text = re.sub(r"\s+", " ", text).strip()
    # drop obvious copyright/license blocks (like angular.dev license we kept hitting)
    cut = re.search(r"(?i)(all rights reserved|the software is provided \"as is\"|copyright \d{4})", text)
    if cut and cut.start() > 300:
        text = text[: cut.start()].rstrip()
    return text


def select_and_enrich(
    items: List[Dict[str, Any]],
    max_articles: int,
    whitelist_domains: Optional[List[str]] = None,
    max_chars: int = 2000,
    must_match_terms: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Pick the best items, extract body text, relax minimums so we keep content,
    and avoid news.google/gstatic placeholders.
    """
    MIN_BODY = 120         # relaxed floor
    MIN_SUMMARY = 60       # backstop floor

    wl = set((d or "").strip().lower().lstrip("www.") for d in (whitelist_domains or []))
    terms_l = [t.strip().lower() for t in (must_match_terms or []) if t and t.strip()]

    selected: List[Dict[str, Any]] = []
    seen = set()

    for it in items:
        url = it.get("link") or ""
        # If Google News style link slipped through, try to extract again
        real = _maybe_original_from_google(url)
        if real:
            url = real

        dom = _domain_of(url)
        if not url or _is_blocked(url):
            print(f"[gather] SKIP (blocked or empty): {it.get('title')}")
            continue
        if wl and dom not in wl:
            print(f"[gather] SKIP (domain not whitelisted): {it.get('title')} — {dom}")
            continue
        key = (it.get("title"), url)
        if key in seen:
            continue

        title_l = (it.get("title") or "").lower()
        summary_l = (it.get("summary") or "").lower()
        if terms_l and not _title_or_summary_matches(title_l, summary_l, terms_l):
            print(f"[gather] SKIP (no ISD match): {it.get('title')}")
            continue

        # Fetch & extract
        body, method = _extract_body(url)

        # If too short, try og:description or cleaned RSS summary
        if not body or len(body) < MIN_BODY:
            og = _fetch_og_description(url)
            if og and len(og) >= MIN_BODY:
                body, method = og, "og:description"
        if (not body or len(body) < MIN_BODY) and it.get("summary"):
            s = _clean_html(it["summary"])
            if s and len(s) >= MIN_BODY:
                body, method = s, "rss-summary"

        if not body or len(body) < MIN_BODY:
            print(f"[gather] SKIP (no usable body): {it.get('title')} — {dom}")
            continue

        body = strip_boilerplate(body)
        if len(body) > max_chars:
            body = body[:max_chars] + "…"

        it["body"] = body
        it["source"] = dom
        it["link"] = url  # ensure it’s the resolved link
        selected.append(it)
        seen.add(key)
        print(f"[gather] KEPT via {method}: {it['title']} — {dom} — len={len(body)}")

        if len(selected) >= max_articles:
            break

    # Backstop: if nothing survived, keep summary-only items so the show isn’t empty
    if not selected:
        print("[gather] backstop: keeping summary-only items")
        for it in items:
            url = it.get("link") or ""
            dom = _domain_of(url)
            if wl and dom not in wl:
                continue
            text = _clean_html(it.get("summary") or "") or (_fetch_og_description(url) or "")
            if text and len(text) >= MIN_SUMMARY:
                text = strip_boilerplate(text)
                if len(text) > max_chars:
                    text = text[:max_chars] + "…"
                it["body"] = text
                it["source"] = dom
                it["link"] = url
                selected.append(it)
                if len(selected) >= max_articles:
                    break

    print(f"[gather] selected {len(selected)} articles (limit={max_articles})")
    return selected

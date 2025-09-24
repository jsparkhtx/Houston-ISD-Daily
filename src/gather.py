# src/gather.py
import json
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse, urlsplit

import feedparser
import requests
from bs4 import BeautifulSoup
from readability import Document

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://news.google.com/",
}

NEWS_HOST = "news.google.com"
BLOCKED_NETLOCS = {
    "news.google.com",
    "www.google.com",
    "google.com",
    "gstatic.com",
    "www.gstatic.com",
    "googleusercontent.com",
    "www.googleusercontent.com",
    "gvt2.com",
    "www.gvt2.com",
}

def _is_blocked(url: str) -> bool:
    if not url:
        return True
    net = (urlparse(url).netloc or "").lower()
    if net.startswith("www."):
        net = net[4:]
    if net in BLOCKED_NETLOCS:
        return True
    # Block annoying script endpoints
    if "gstatic.com" in net and "/_/boq-dots/" in url:
        return True
    return False

def _domain_of(url: str) -> str:
    try:
        d = urlparse(url).netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return "unknown"

# ----------------------------------------------------
# Google News unwrapping (incl. JSON-LD/script scraping)
# ----------------------------------------------------
def _unwrap_google_entry(entry) -> str:
    """
    Return the publisher URL for a Google News RSS entry.
    Tries (in order):
      1) feedburner_origlink
      2) entry.links[] href not on news.google.com
      3) first <a href> in summary not on news.google.com (resolving ./articles/… if present)
      4) link's ?url= param
      5) resolve any news.google.com link (rss/articles/*, /articles/*, etc.)
      6) fallback: entry.link
    """
    # 1) explicit original link
    try:
        orig = getattr(entry, "feedburner_origlink", None) or entry.get("feedburner_origlink")
        if orig and not _is_blocked(orig):
            return orig
    except Exception:
        pass

    # 2) entry.links[]
    try:
        for l in entry.get("links", []):
            href = l.get("href")
            if href and not _is_blocked(href):
                return href
    except Exception:
        pass

    # 3) link in summary
    try:
        sm = entry.get("summary", "") or ""
        for href in _hrefs_from_html(sm):
            if href.startswith("./"):
                href_abs = urljoin(f"https://{NEWS_HOST}/", href)
                real = _resolve_news_google_link(href_abs)
                if real and not _is_blocked(real):
                    return real
            if href and not _is_blocked(href):
                return href
    except Exception:
        pass

    # 4) ?url= param on entry.link
    try:
        link = entry.link
        parsed = urlsplit(link)
        if NEWS_HOST in (parsed.netloc or "").lower():
            qs = parse_qs(parsed.query)
            if "url" in qs and qs["url"]:
                real = unquote(qs["url"][0])
                if real and not _is_blocked(real):
                    return real
    except Exception:
        pass

    # 5) resolve any google link
    try:
        link = entry.link
        if NEWS_HOST in (urlparse(link).netloc or "").lower():
            real = _resolve_news_google_link(link)
            if real and not _is_blocked(real):
                return real
    except Exception:
        pass

    # 6) fallback
    try:
        return entry.link
    except Exception:
        return ""

def _resolve_news_google_link(google_url: str) -> Optional[str]:
    """
    Resolve a news.google.com URL to the publisher URL. Handles:
      - normal redirects
      - meta-refresh pages
      - "Continue" anchors
      - relative ./articles/* links (follows one extra hop)
      - JSON-LD <script> with "url"
      - any absolute URLs embedded in script text (filtered)
    """
    try:
        if google_url.startswith("./"):
            google_url = urljoin(f"https://{NEWS_HOST}/", google_url)

        r = requests.get(google_url, headers=UA, timeout=12, allow_redirects=True)
        # If we already landed off Google, done.
        if r.status_code == 200 and r.url and not _is_blocked(r.url):
            return r.url

        html = r.text or ""
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")

        candidates: List[str] = []

        # meta refresh
        meta = soup.find("meta", attrs={"http-equiv": re.compile(r"refresh", re.I)})
        if meta:
            content = meta.get("content") or ""
            m = re.search(r"url\s*=\s*([^;]+)", content, flags=re.I)
            if m:
                candidates.append(_absolutize_google_href(m.group(1).strip().strip("'\"")))

        # anchors
        for a in soup.find_all("a", href=True):
            href = _absolutize_google_href(a["href"])
            candidates.append(href)
            # follow one more ./articles hop
            if _is_google_article(href):
                try:
                    r2 = requests.get(href, headers=UA, timeout=12, allow_redirects=True)
                    if r2.status_code == 200 and r2.url and not _is_blocked(r2.url):
                        candidates.append(r2.url)
                    soup2 = BeautifulSoup(r2.text or "", "html.parser")
                    candidates.extend(_extract_from_html_variants(soup2))
                except Exception:
                    pass

        # canonical
        canon = soup.find("link", rel=lambda x: x and "canonical" in x)
        if canon:
            candidates.append(_absolutize_google_href(canon.get("href") or ""))

        # JSON-LD blocks
        for s in soup.find_all("script", type=lambda x: x and "ld+json" in x):
            text = s.get_text(strip=True)
            try:
                data = json.loads(text)
                for u in _walk_urls_in_json(data):
                    candidates.append(u)
            except Exception:
                # fallback: regex any URLs in the JSON text
                candidates.extend(_urls_from_text(text))

        # Any absolute URLs in any script content (last resort)
        for s in soup.find_all("script"):
            candidates.extend(_urls_from_text(s.get_text() or ""))

        # Filter and return the first publisher-looking URL
        for u in candidates:
            if not _is_blocked(u) and u.startswith("http"):
                return u
    except Exception:
        return None

    return None

def _extract_from_html_variants(soup: BeautifulSoup) -> List[str]:
    out: List[str] = []
    meta = soup.find("meta", attrs={"http-equiv": re.compile(r"refresh", re.I)})
    if meta:
        content = meta.get("content") or ""
        m = re.search(r"url\s*=\s*([^;]+)", content, flags=re.I)
        if m:
            out.append(_absolutize_google_href(m.group(1).strip().strip("'\"")))
    for a in soup.find_all("a", href=True):
        out.append(_absolutize_google_href(a["href"]))
    link_canon = soup.find("link", rel=lambda x: x and "canonical" in x)
    if link_canon:
        out.append(_absolutize_google_href(link_canon.get("href") or ""))
    return out

def _absolutize_google_href(href: str) -> str:
    if not href:
        return ""
    if href.startswith("./"):
        return urljoin(f"https://{NEWS_HOST}/", href)
    return href

def _is_google_article(url: str) -> bool:
    if not url:
        return False
    return NEWS_HOST in (urlparse(url).netloc or "").lower()

def _hrefs_from_html(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    return [a["href"] for a in soup.find_all("a", href=True)]

def _urls_from_text(text: str) -> List[str]:
    return re.findall(r"https?://[^\s\"'<>()]+", text or "")

def _walk_urls_in_json(obj) -> Iterable[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "url" and isinstance(v, str):
                yield v
            else:
                yield from _walk_urls_in_json(v)
    elif isinstance(obj, list):
        for it in obj:
            yield from _walk_urls_in_json(it)

# -----------------------------
# Fetch Google News RSS
# -----------------------------
def fetch_feeds(terms: List[str]) -> List[Dict[str, Any]]:
    """Fetch Google News RSS results for each term (last 2 days)."""
    all_items: List[Dict[str, Any]] = []
    for term in terms:
        q = quote_plus(f'"{term}"')  # phrase match
        url = f"https://news.google.com/rss/search?q={q}+when:2d&hl=en-US&gl=US&ceid=US:en"
        feed = feedparser.parse(url)
        print(f"[gather] term={term!r} fetched={len(feed.entries)} url={url}")

        for entry in feed.entries:
            resolved = _unwrap_google_entry(entry)
            dom = _domain_of(resolved)
            title = entry.title

            item = {
                "title": title,
                "link": resolved,
                "published": _parse_date(entry.get("published")),
                "summary": entry.get("summary", ""),
                "source": dom,
            }
            print(f"[gather] candidate: {title} — {dom} — {resolved}")
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
    Extraction order: readability -> soup <p> -> og:description -> RSS summary.
    """
    seen = set()
    selected: List[Dict[str, Any]] = []

    wl = set((d or "").strip().lower().lstrip("www.") for d in (whitelist_domains or []))
    mm = [t.strip().lower() for t in (must_match_terms or []) if t and t.strip()]

    for it in sorted(items, key=lambda x: x["published"] or datetime.utcnow(), reverse=True):
        url = it["link"]
        dom = it.get("source") or _domain_of(url)
        if not url or _is_blocked(url):
            print(f"[gather] SKIP (blocked url): {it.get('title')}")
            continue

        key = (it["title"], url)
        if key in seen:
            continue

        title_l = (it.get("title") or "").lower()
        summary_l = (it.get("summary") or "").lower()
        if mm and not _title_or_summary_matches(title_l, summary_l, mm):
            print(f"[gather] SKIP (no ISD/district match): {it['title']}")
            continue

        if wl and dom not in wl:
            print(f"[gather] SKIP (domain not whitelisted): {it['title']} — {dom}")
            continue

        body, method = _extract_body(url)

        if not body or len(body) < 200:
            og = _fetch_og_description(url)
            if og and len(og) > (len(body) if body else 0):
                body, method = og, "og:description"

        if not body or len(body) < 200:
            fallback = _clean_html(it.get("summary", "") or "")
            if fallback and len(fallback) > (len(body) if body else 0):
                body, method = fallback, "rss-summary"

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
# Extraction helpers
# -----------------------------
def _extract_body(url: str) -> Tuple[Optional[str], str]:
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

def _fetch_og_description(url: str) -> Optional[str]:
    try:
        html = _get(url)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        og = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
        if og:
            desc = (og.get("content") or "").strip()
            desc = re.sub(r"\s+", " ", desc)
            return desc if desc else None
    except Exception:
        return None
    return None

def _get(url: str) -> Optional[str]:
    try:
        r = requests.get(url, timeout=12, headers=UA)
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

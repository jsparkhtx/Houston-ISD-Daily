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
        "KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://news.google.com/",
}

NEWS_HOST = "news.google.com"
# Strong blocklist: anything Google, static asset CDNs, frameworks, etc.
BLOCKED_NETLOCS = {
    "news.google.com", "google.com", "www.google.com",
    "gstatic.com", "www.gstatic.com",
    "googleusercontent.com", "www.googleusercontent.com",
    "gvt2.com", "www.gvt2.com",
    "ampproject.org", "www.ampproject.org",
    "apple.news", "www.apple.news",
    "angular.dev", "www.angular.dev",
    "fonts.googleapis.com", "fonts.gstatic.com",
    "cdn.ampproject.org",
}

def _is_blocked(url: str) -> bool:
    if not url:
        return True
    try:
        net = (urlparse(url).netloc or "").lower()
    except Exception:
        return True
    if net.startswith("www."):
        net = net[4:]
    if net in BLOCKED_NETLOCS:
        return True
    # Block Google’s inline DOTS script endpoint outright
    if "gstatic.com" in net and "/_/boq-dots/" in url:
        return True
    # Ignore obvious non-article file types
    if re.search(r"\.(js|css|map|svg|png|jpg|jpeg|gif|webp)(\?|$)", url, re.I):
        return True
    return False

def _domain_of(url: str) -> str:
    try:
        d = urlparse(url).netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return "unknown"

def _hrefs_from_html(html: str) -> List[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    return [a["href"] for a in soup.find_all("a", href=True)]

def _absolutize_google_href(href: str) -> str:
    if not href:
        return ""
    if href.startswith("./"):
        return urljoin(f"https://{NEWS_HOST}/", href)
    return href

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

def _is_google_article(url: str) -> bool:
    if not url:
        return False
    return NEWS_HOST in (urlparse(url).netloc or "").lower()

def _resolve_news_google_link(google_url: str) -> Optional[str]:
    """
    Resolve a news.google.com URL to the publisher URL using only:
      - redirects
      - meta-refresh
      - anchors (including ./articles/... one extra hop)
      - canonical
      - JSON-LD 'url'
    """
    try:
        if google_url.startswith("./"):
            google_url = urljoin(f"https://{NEWS_HOST}/", google_url)

        r = requests.get(google_url, headers=UA, timeout=12, allow_redirects=True)
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
            if _is_google_article(href):
                try:
                    r2 = requests.get(href, headers=UA, timeout=12, allow_redirects=True)
                    if r2.status_code == 200 and r2.url and not _is_blocked(r2.url):
                        candidates.append(r2.url)
                    soup2 = BeautifulSoup(r2.text or "", "html.parser")
                    # also pick canonical from the hop page
                    canon2 = soup2.find("link", rel=lambda x: x and "canonical" in x)
                    if canon2:
                        candidates.append(_absolutize_google_href(canon2.get("href") or ""))
                except Exception:
                    pass

        # canonical
        canon = soup.find("link", rel=lambda x: x and "canonical" in x)
        if canon:
            candidates.append(_absolutize_google_href(canon.get("href") or ""))

        # JSON-LD with "url"
        for s in soup.find_all("script", type=lambda x: x and "ld+json" in x):
            text = s.get_text(strip=True)
            try:
                data = json.loads(text)
                for u in _walk_urls_in_json(data):
                    candidates.append(u)
            except Exception:
                # ignore malformed JSON
                pass

        for u in candidates:
            if u and u.startswith("http") and not _is_blocked(u):
                return u
    except Exception:
        return None
    return None

def _unwrap_google_entry(entry) -> str:
    # 1) explicit original
    orig = (getattr(entry, "feedburner_origlink", None) or entry.get("feedburner_origlink"))
    if orig and not _is_blocked(orig):
        return orig

    # 2) any non-Google link in entry.links[]
    for l in entry.get("links", []):
        href = l.get("href")
        if href and not _is_blocked(href):
            return href

    # 3) a link in the summary (resolve ./articles)
    for href in _hrefs_from_html(entry.get("summary", "") or ""):
        if href.startswith("./"):
            href_abs = urljoin(f"https://{NEWS_HOST}/", href)
            real = _resolve_news_google_link(href_abs)
            if real and not _is_blocked(real):
                return real
        if href and not _is_blocked(href):
            return href

    # 4) ?url= param
    link = entry.link
    parsed = urlsplit(link)
    if NEWS_HOST in (parsed.netloc or "").lower():
        qs = parse_qs(parsed.query)
        if "url" in qs and qs["url"]:
            real = unquote(qs["url"][0])
            if real and not _is_blocked(real):
                return real

    # 5) resolve via HTML
    if NEWS_HOST in (urlparse(link).netloc or "").lower():
        real = _resolve_news_google_link(link)
        if real and not _is_blocked(real):
            return real

    # 6) fallback
    return entry.link

# -----------------------------
# Fetch Google News RSS
# -----------------------------
def fetch_feeds(terms: List[str]) -> List[Dict[str, Any]]:
    all_items: List[Dict[str, Any]] = []
    for term in terms:
        q = quote_plus(f'"{term}"')  # phrase match
        url = f"https://news.google.com/rss/search?q={q}+when:2d&hl=en-US&gl=US&ceid=US:en"
        feed = feedparser.parse(url)
        print(f"[gather] term={term!r} fetched={len(feed.entries)} url={url}")

        for entry in feed.entries:
            resolved = _unwrap_google_entry(entry)
            dom = _domain_of(resolved)
            item = {
                "title": entry.title,
                "link": resolved,
                "published": _parse_date(entry.get("published")),
                "summary": entry.get("summary", ""),
                "source": dom,
            }
            print(f"[gather] candidate: {entry.title} — {dom} — {resolved}")
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

        if wl and dom not in wl:
            print(f"[gather] SKIP (domain not whitelisted): {it['title']} — {dom}")
            continue

        key = (it["title"], url)
        if key in seen:
            continue

        title_l = (it.get("title") or "").lower()
        summary_l = (it.get("summary") or "").lower()
        if mm and not _title_or_summary_matches(title_l, summary_l, mm):
            print(f"[gather] SKIP (no ISD/district match): {it['title']}")
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
        print(f"[gather] KEPT via {method}: {it['title']} — {dom} — len={len(body)}")
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
    # readability first
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

    # soup <p> fallback
    try:
        html = html or _get(url)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
            text = re.sub(r"\s+", " ", " ".join(paragraphs)).strip()
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
            return re.sub(r"\s+", " ", desc)
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

def strip_boilerplate(text: str) -> str:
    bads = [
        "Subscribe to our newsletter",
        "All rights reserved",
        "Continue Reading",
        "Advertisement",
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
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()

def _parse_date(pub: Optional[str]):
    try:
        return datetime(*feedparser._parse_date(pub)[:6])
    except Exception:
        return datetime.utcnow()

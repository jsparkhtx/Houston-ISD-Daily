# src/gather.py
import feedparser
from datetime import datetime
from typing import List, Dict, Any
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, quote_plus

# readability first, soup as fallback
from readability import Document


def fetch_feeds(terms: List[str]) -> List[Dict[str, Any]]:
    """Fetch Google News RSS feeds for each search term (last 2 days)."""
    all_items = []
    for term in terms:
        q = quote_plus(f'"{term}"')  # e.g., "Houston ISD" -> %22Houston+ISD%22
        url = f"https://news.google.com/rss/search?q={q}+when:2d&hl=en-US&gl=US&ceid=US:en"

        feed = feedparser.parse(url)
        print(f"[gather] term={term!r} fetched={len(feed.entries)} url={url}")

        for entry in feed.entries:
            item = {
                "title": entry.title,
                "link": entry.link,
                "published": _parse_date(entry.get("published")),
                "summary": entry.get("summary", ""),
                "source": _source_from_link(entry.link),
            }
            print(f"[gather] candidate: {item['title']} — {item['link']}")
            all_items.append(item)

    return all_items


def select_and_enrich(
    items: List[Dict[str, Any]],
    max_articles: int,
    whitelist_domains: List[str] = None,
    max_chars: int = 2000,
) -> List[Dict[str, Any]]:
    """
    Filter, deduplicate, and enrich articles with readable text.
    Order: readability -> soup <p> -> cleaned RSS summary.
    """
    seen_links = set()
    selected = []

    # newest first
    for it in sorted(items, key=lambda x: x["published"] or datetime.utcnow(), reverse=True):
        if it["link"] in seen_links:
            continue

        domain = urlparse(it["link"]).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]

        if whitelist_domains and domain not in whitelist_domains:
            print(f"[gather] SKIP (domain not in whitelist): {it['title']} — {domain}")
            continue

        # 1) readability
        body = extract_readable(it["link"])
        method = "readability"

        # 2) soup fallback
        if not body or len(body) < 280:  # too short? try soup
            soup_body = extract_soup(it["link"])
            if soup_body and len(soup_body) > (len(body) if body else 0):
                body = soup_body
                method = "soup"

        # 3) RSS summary fallback
        if not body or len(body) < 200:
            summary = _clean_html(it.get("summary", "") or "")
            if summary:
                # Prefer summary only if it actually adds content
                if not body or len(summary) > len(body):
                    body = summary
                    method = "rss-summary"

        if not body:
            print(f"[gather] SKIP (no usable body): {it['title']} — {domain}")
            continue

        # Heuristics: trim boilerplate
        body = strip_boilerplate(body)

        # cap to max_chars (config)
        if len(body) > max_chars:
            body = body[: max_chars] + "…"

        it["body"] = body
        it["source"] = domain
        snippet = body[:120].replace("\n", " ")
        print(f"[gather] KEPT via {method}: {it['title']} — {domain} — len={len(body)} — snippet: {snippet}…")

        selected.append(it)
        seen_links.add(it["link"])
        if len(selected) >= max_articles:
            break

    print(f"[gather] selected {len(selected)} articles (limit={max_articles})")
    return selected


# -----------------
# Extractors
# -----------------
def extract_readable(url: str) -> str | None:
    """Extract main content using readability-lxml."""
    try:
        html = _get(url)
        if not html:
            return None
        doc = Document(html)
        article_html = doc.summary(html_partial=True)
        text = _clean_html(article_html)
        return text or None
    except Exception as e:
        print(f"[gather] readability ERROR for {url}: {e}")
        return None


def extract_soup(url: str) -> str | None:
    """Fallback: join <p> tags text with BeautifulSoup."""
    try:
        html = _get(url)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        text = " ".join(paragraphs)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            print(f"[gather] WARNING: soup found no text for {url}")
        return text or None
    except Exception as e:
        print(f"[gather] soup ERROR for {url}: {e}")
        return None


def _get(url: str) -> str | None:
    try:
        resp = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            print(f"[gather] ERROR {resp.status_code} fetching {url}")
            return None
        return resp.text
    except Exception as e:
        print(f"[gather] GET ERROR for {url}: {e}")
        return None


# -----------------
# Utilities
# -----------------
def strip_boilerplate(text: str) -> str:
    """Remove obvious boilerplate / social prompts."""
    # light heuristics — adjust as needed
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
    """Strip tags/whitespace from HTML."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(pub: str):
    try:
        return datetime(*feedparser._parse_date(pub)[:6])
    except Exception:
        return datetime.utcnow()


def _source_from_link(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return "unknown"

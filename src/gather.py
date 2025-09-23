# src/gather.py
import feedparser
from datetime import datetime
from typing import List, Dict, Any
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, quote_plus


def fetch_feeds(terms: List[str]) -> List[Dict[str, Any]]:
    """Fetch Google News RSS feeds for each search term."""
    all_items = []
    for term in terms:
        # URL-encode the quoted term to avoid spaces/control chars in the URL
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
    Filter, deduplicate, and enrich articles.
    Falls back to RSS summary text when full-page scrape fails.
    """
    seen_links = set()
    selected = []

    for it in sorted(items, key=lambda x: x["published"] or datetime.utcnow(), reverse=True):
        if it["link"] in seen_links:
            continue

        if whitelist_domains:
            domain = urlparse(it["link"]).netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
            if domain not in whitelist_domains:
                print(f"[gather] SKIP (domain not in whitelist): {it['title']} — {domain}")
                continue

        # Try to scrape full article text
        body = scrape_article(it["link"])

        # Fallback: clean RSS summary if scraping failed/empty
        if not body:
            fallback = _clean_html(it.get("summary", "") or "")
            if fallback:
                body = fallback
                print(f"[gather] FALLBACK summary used for: {it['title']}")
            else:
                print(f"[gather] SKIP (no body & no summary): {it['title']}")
                continue

        # Trim overly long text
        if len(body) > max_chars:
            body = body[: max_chars] + "…"

        it["body"] = body
        snippet = body[:120].replace("\n", " ")
        print(f"[gather] KEPT: {it['title']} — snippet: {snippet}…")
        selected.append(it)
        seen_links.add(it["link"])

        if len(selected) >= max_articles:
            break

    print(f"[gather] selected {len(selected)} articles (limit={max_articles})")
    return selected


def scrape_article(url: str) -> str:
    """Fetch and extract readable text from a URL."""
    try:
        resp = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            print(f"[gather] ERROR {resp.status_code} fetching {url}")
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        # Simple heuristic: join all paragraph text
        paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        text = " ".join(paragraphs)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            print(f"[gather] WARNING: No text extracted from {url}")
        return text or None
    except Exception as e:
        print(f"[gather] ERROR scraping {url}: {e}")
        return None


def _clean_html(html: str) -> str:
    """Strip tags/whitespace from RSS summary HTML."""
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

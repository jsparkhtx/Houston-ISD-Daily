import feedparser
from datetime import datetime, timedelta
from typing import List, Dict, Any
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse


def fetch_feeds(terms: List[str]) -> List[Dict[str, Any]]:
    """Fetch Google News RSS feeds for each search term."""
    all_items = []
    for term in terms:
        url = f'https://news.google.com/rss/search?q="{term}"+when:2d&hl=en-US&gl=US&ceid=US:en'
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
    """Filter, deduplicate, and trim articles."""
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

        # Fetch full text
        body = scrape_article(it["link"])
        if not body:
            print(f"[gather] SKIP (no body): {it['title']}")
            continue

        # Truncate if too long
        if len(body) > max_chars:
            body = body[: max_chars] + "…"

        it["body"] = body
        selected.append(it)
        seen_links.add(it["link"])

        print(f"[gather] KEPT: {it['title']} ({it['link']})")
        if len(selected) >= max_articles:
            break

    print(f"[gather] selected {len(selected)} articles (limit={max_articles})")
    return selected


def scrape_article(url: str) -> str:
    """Fetch and extract readable text from a URL."""
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        # Heuristic: grab <p> text
        paragraphs = [p.get_text(" ", strip=_]()

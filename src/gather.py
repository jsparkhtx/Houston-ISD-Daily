import time
import feedparser
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from bs4 import BeautifulSoup
from readability import Document
from dateutil import parser as dateparser
from typing import List, Dict, Any, Optional
from utils import clean_whitespace, hostname, domain_from_host

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

def google_news_rss_url(term: str) -> str:
    q = quote(f'"{term}" when:1d')
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

def fetch_feeds(terms: List[str]) -> List[Dict[str, Any]]:
    items = []
    seen = set()
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)

    for term in terms:
        url = google_news_rss_url(term)
        feed = feedparser.parse(url)
        for e in feed.entries:
            title = clean_whitespace(getattr(e, "title", ""))
            link = getattr(e, "link", "")
            published = getattr(e, "published", "") or getattr(e, "updated", "")
            published_parsed = None
            try:
                if hasattr(e, "published_parsed") and e.published_parsed:
                    published_parsed = datetime.fromtimestamp(time.mktime(e.published_parsed), tz=timezone.utc)
                elif published:
                    published_parsed = dateparser.parse(published).astimezone(timezone.utc)
            except Exception:
                published_parsed = None

            if published_parsed and published_parsed < day_ago:
                continue

            key = (title.lower(), link)
            if key in seen:
                continue
            seen.add(key)

            src_host = domain_from_host(hostname(link))
            summary = clean_whitespace(getattr(e, "summary", "") or "")
            items.append({
                "title": title,
                "link": link,
                "published": published_parsed or now,
                "source": src_host,
                "summary": summary,
                "term": term
            })
        time.sleep(0.2)
    return items

def extract_article_text(url: str, timeout: int = 12) -> Optional[str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        doc = Document(r.text)
        html = doc.summary()
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator=" ")
        text = clean_whitespace(text)
        if len(text) < 300:
            soup2 = BeautifulSoup(r.text, "html.parser")
            for tag in soup2(["script", "style", "noscript"]):
                tag.decompose()
            alt = clean_whitespace(soup2.get_text(separator=" "))
            if len(alt) > len(text):
                text = alt
        return text
    except Exception:
        return None

def sanitize_article(text: str, max_chars: int) -> str:
    if not text:
        return ""
    bad_phrases = ["Skip to content", "Advertisement", "Subscribe", "Sign up for our newsletter"]
    for bp in bad_phrases:
        text = text.replace(bp, " ")
    text = clean_whitespace(text)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(". ", 1)[0] + "."
    return text

def select_and_enrich(items: List[Dict[str, Any]], max_articles: int,
                      whitelist_domains: Optional[List[str]], max_chars: int) -> List[Dict[str, Any]]:
    title_seen = set()
    filtered = []
    for it in sorted(items, key=lambda x: x["published"], reverse=True):
        tkey = it["title"].lower()
        if tkey in title_seen:
            continue
        if whitelist_domains:
            if it["source"] not in whitelist_domains and it["source"].replace("www.", "") not in whitelist_domains:
                continue
        title_seen.add(tkey)
        filtered.append(it)
        if len(filtered) >= max_articles * 2:
            break

    enriched = []
    for it in filtered:
        full = extract_article_text(it["link"])
        body = sanitize_article(full, max_chars) if full else sanitize_article(it.get("summary", ""), max_chars)
        if len(body) < 200:
            continue
        enriched.append({**it, "body": body})
        if len(enriched) >= max_articles:
            break
    return enriched
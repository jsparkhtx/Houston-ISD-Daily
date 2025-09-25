from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import List, Dict
import feedparser
from urllib.parse import urlparse
import json
from datetime import datetime, timezone
from pathlib import Path
import logging

from .config import load_settings, STATE_PATH
from .cleaners import strip_html, compact, safe_truncate

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

@dataclass
class Item:
    source: str
    title: str
    link: str
    summary: str
    published: str  # ISO8601

def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()

def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""

def _read_state() -> Dict[str, str]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            return {}
    return {}

def _write_state(state: Dict[str, str]) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))

def fetch_all() -> List[Item]:
    settings = load_settings()
    state = _read_state()
    seen = set(state.keys())

    collected: List[Item] = []

    for feed in settings.feeds:
        name, url = feed.get("name", "Unknown"), feed["url"]
        logger.info("Fetching %s", url)
        parsed = feedparser.parse(url)
        for e in parsed.entries:
            link = compact(getattr(e, "link", ""))
            title = compact(getattr(e, "title", ""))
            summary_html = getattr(e, "summary", "") or getattr(e, "description", "")
            summary = strip_html(summary_html)
            published = getattr(e, "published", None) or getattr(e, "updated", None)
            if not published:
                published_iso = _now_iso()
            else:
                try:
                    # feedparser provides .published_parsed
                    dt = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
                    if dt:
                        published_iso = datetime(*dt[:6], tzinfo=timezone.utc).astimezone().isoformat()
                    else:
                        published_iso = _now_iso()
                except Exception:
                    published_iso = _now_iso()

            if not link or not title:
                continue

            # Blocklist domains (e.g., paywalled)
            if _domain(link) in settings.blocklist_domains:
                logger.info("Skip blocked domain: %s", link)
                continue

            # Soft filters for obvious sports-only posts, etc.
            if any(w.lower() in (title + " " + summary).lower() for w in settings.soft_word_filters):
                logger.info("Soft-filtered: %s", title)
                continue

            key = link
            if key in seen:
                continue
            seen.add(key)

            collected.append(Item(
                source=name,
                title=title,
                link=link,
                summary=safe_truncate(summary, 600),
                published=published_iso,
            ))

    # Update state with a bounded size (keep last 5000)
    state_update = {i.link: i.published for i in collected}
    merged = {**_read_state(), **state_update}
    if len(merged) > 5000:
        # keep newest
        items_sorted = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)[:5000]
        merged = dict(items_sorted)
    _write_state(merged)

    # Sort collected by date desc
    collected.sort(key=lambda i: i.published, reverse=True)
    return collected

def to_dicts(items: List[Item]) -> List[Dict]:
    return [asdict(i) for i in items]

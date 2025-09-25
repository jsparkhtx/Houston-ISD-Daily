# src/gather.py
# Collect RSS items, resolve real publisher URLs, and extract readable text.

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

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_REQ_TIMEOUT = 15

def _http_get(url: str) -> requests.Response:
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
    for k in ("feedburner_origlink", "origLink", "originallink"):
        v = entry.get(k)
        if v:
            return v
    link = entry.get("link") or entry.get("id")
    if link:
        real = _maybe_original_from_google(link)
        return real or link
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

_PARAGRAPH_RE = re.compile(r"\S")

def _readability_extract(html_text: str) -> str:
    doc = Document(html_text)
    cleaned = doc.summary(html_partial=True)
    text = _clean_html(cleaned)
    return text

def _bs4_extract(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "html.parser")
    art = soup.find("article")
    if art:
        text = art.get_text(" ", strip=True)
        if text and len(text) > 100:
            return text
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
    r = _http_get(url)
    try:
        text = _readability_extract(r.text)
        if text and len(text) > 200:
            return text, "readability"
    except Exception:
        pass
    try:
        text = _bs4_extract(r.text)
        if text and len(text) > 200:
            return text, "soup"
    except Exception:
        pass
    og = _fetch_og_description(r.url or url)
    if og:
        return og, "og:description"
    return "", "none"

def _title_or_summary_matches(title_l: str, summary_l: str, terms_l: List[str]) -> bool:
    hay = f"{title_l} {summary_l}"
    return any(t in hay for t in terms_l)

def _google_feed_for_terms(terms: List[str]) -> str:
    if terms:
        query = " OR ".join(f'"{t}"' if " " in t else t for t in terms)
    else:
        query = "Houston ISD OR Fort Bend ISD OR Katy ISD"
    return f"https://news.google.com/rss/search?q={query}+when:24h&hl=en-US&gl=US&ceid=US:en"

def fetch_feeds(terms: List[str], extra_feeds: List[str]) -> List[Dict[str, Any]]:
    feeds: List[str] = []
    if extra_feeds:
        feeds.extend(extra_feeds)
    feeds.append(_google_feed_for_terms(terms))

    out: List[Dict[str, Any]] = []
    for url in feeds:
        try:
            parsed = feedparser.parse(url)
            for e in parsed.entries:
                link = _entry_best_link(e) or ""
                dom = _domain_of(link) or _domain_of(e.get("link", ""))
                title = _clean_html(e.get("title", ""))
                summary = _clean_html(e.get("summary", ""))

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

    out.sort(key=lambda x: x["published"], reverse=True)
    print(f"[gather] fetched {len(out)} raw entries from {len(feeds)} feeds")
    return out

def strip_boilerplate(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
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
    MIN_BODY = 120
    MIN_SUMMARY = 60

    wl = set((d or "").strip().lower().lstrip("www.") for d in (whitelist_domains or []))
    terms_l = [t.strip().lower() for t in (must_match_terms or []) if t and t.strip()]

    selected: List[Dict[str, Any]] = []
    seen = set()

    for it in items:
        url = it.get("link") or ""
        real = _maybe_original_from_google(url)
        if real:
            url = real
        dom = _domain_of(url)

        if not url or _is_blocked(url):
            print(f"[gather] SKIP blocked/empty: {it.get('title')}")
            continue
        if wl and dom not in wl:
            print(f"[gather] SKIP not whitelisted: {dom} | {it.get('title')}")
            continue
        key = (it.get("title"), url)
        if key in seen:
            continue

        title_l = (it.get("title") or "").lower()
        summary_l = (it.get("summary") or "").lower()
        if terms_l and not _title_or_summary_matches(title_l, summary_l, terms_l):
            print(f"[gather] SKIP no-term-match: {dom} | {it.get('title')}")
            continue

        body, method = _extract_body(url)
        if not body or len(body) < MIN_BODY:
            og = _fetch_og_description(url)
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
        it["link"] = url
        selected.append(it)
        seen.add(key)
        print(f"[gather] KEPT via {method}: {dom} | len={len(body)} | {it['title']}")

        if len(selected) >= max_articles:
            break

    if not selected:
        print("[gather] backstop: summary-only")
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
    )

    cleanup_old_audio(int(cfg.get("retain_days", 14)))

    # 4) feed
    site_base_url = cfg["site_base_url"].rstrip("/")
    episodes = load_existing_episodes(site_base_url)

    # attach notes page to today’s ep if present
    for ep in episodes:
        if ep["url"].endswith(f"{daily_mp3_name}.mp3"):
            ep["summary"] = notes.replace("\n", "<br/>")
            ep["page_url"] = f"{site_base_url}/{base_name}.txt"
            break

    feed_path = os.path.join(DOCS, "feed.xml")
    build_podcast_feed(
        site_base_url=site_base_url,
        show_title=cfg.get("show_title","Greater Houston ISD Daily"),
        show_description=cfg.get("show_description","Daily readouts of last-24-hour news about Greater Houston ISDs."),
        show_author=cfg.get("show_author","PlayHereHouston"),
        show_email=cfg.get("show_email","you@example.com"),
        episodes=episodes,
        out_feed_path=feed_path
    )


if __name__ == "__main__":
    main()

def select_and_enrich(
    items: List[Dict[str, Any]],
    max_articles: int,
    whitelist_domains: Optional[List[str]] = None,
    max_chars: int = 2000,
    must_match_terms: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Picks the best candidates, tries to extract full text, and (new)
    accepts shorter bodies; if we still end up with nothing, we
    fall back to summary-only items so the show never comes out empty.
    """
    MIN_BODY = 120          # was ~200; relax to 120 chars
    MIN_SUMMARY = 60        # minimum chars to accept a summary-only fallback

    seen = set()
    selected: List[Dict[str, Any]] = []
    
from typing import Any, Dict, Iterable, List, Optional, Tuple

    wl = set((d or "").strip().lower().lstrip("www.") for d in (whitelist_domains or []))
    mm = [t.strip().lower() for t in (must_match_terms or []) if t and t.strip()]

    # First pass: try to get real bodies
    for it in sorted(items, key=lambda x: x["published"] or datetime.utcnow(), reverse=True):
        url = it.get("link")
        dom = (it.get("source") or _domain_of(url)).lower()
        if not url or _is_blocked(url):
            print(f"[gather] SKIP (blocked url): {it.get('title')}")
            continue
        if wl and dom not in wl:
            print(f"[gather] SKIP (domain not whitelisted): {it.get('title')} — {dom}")
            continue

        key = (it.get("title"), url)
        if key in seen:
            continue

        title_l = (it.get("title") or "").lower()
        summary_l = (it.get("summary") or "").lower()
        if mm and not _title_or_summary_matches(title_l, summary_l, mm):
            print(f"[gather] SKIP (no ISD/district match): {it.get('title')}")
            continue

        body, method = _extract_body(url)

        # Try og:description if body too short
        if not body or len(body) < MIN_BODY:
            og = _fetch_og_description(url)
            if og and len(og) >= MIN_BODY:
                body, method = og, "og:description"

        # Try RSS summary cleaned if still short
        if (not body or len(body) < MIN_BODY) and it.get("summary"):
            fallback = _clean_html(it["summary"])
            if fallback and len(fallback) >= MIN_BODY:
                body, method = fallback, "rss-summary"

        if not body or len(body) < MIN_BODY:
            print(f"[gather] SKIP (no usable body): {it.get('title')} — {dom}")
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

    # Backstop: if nothing survived, allow summary-only items (shorter)
    if not selected:
        print("[gather] backstop: keeping summary-only items so show isn’t empty")
        for it in sorted(items, key=lambda x: x["published"] or datetime.utcnow(), reverse=True):
            url = it.get("link")
            dom = (it.get("source") or _domain_of(url)).lower()
            if not url or _is_blocked(url):
                continue
            if wl and dom not in wl:
                continue
            key = (it.get("title"), url)
            if key in seen:
                continue
            title_l = (it.get("title") or "").lower()
            summary_l = (it.get("summary") or "").lower()
            if mm and not _title_or_summary_matches(title_l, summary_l, mm):
                continue

            # choose the longest text we can find, even if short
            text = None
            og = _fetch_og_description(url)
            cand = [
                _clean_html(it.get("summary") or ""),
                og or "",
            ]
            cand = [c for c in cand if c]
            text = max(cand, key=len) if cand else None
            if text and len(text) >= MIN_SUMMARY:
                body = strip_boilerplate(text)
                if len(body) > max_chars:
                    body = body[:max_chars] + "…"
                it["body"] = body
                it["source"] = dom
                print(f"[gather] KEPT backstop summary: {it['title']} — {dom} — len={len(body)}")
                selected.append(it)
                if len(selected) >= max_articles:
                    break

    print(f"[gather] selected {len(selected)} articles (limit={max_articles})")
    return selected

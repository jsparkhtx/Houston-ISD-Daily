import os, sys, glob, pytz
from datetime import datetime, timedelta
from typing import List, Dict, Any
import yaml

# Allow imports from src directory when run as "python src/main.py"
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

from gather import fetch_feeds, select_and_enrich
from tts import synth_to_mp3
from build_feed import build_podcast_feed
from utils import clean_whitespace

ROOT = os.path.abspath(os.path.join(THIS_DIR, ".."))
DOCS = os.path.join(ROOT, "docs")
AUDIO = os.path.join(DOCS, "audio")

def load_config() -> Dict[str, Any]:
    with open(os.path.join(ROOT, "config.yaml"), "r") as f:
        return yaml.safe_load(f)

def load_terms() -> List[str]:
    with open(os.path.join(ROOT, "isd_terms.txt"), "r") as f:
        return [clean_whitespace(x) for x in f.read().splitlines() if clean_whitespace(x)]

def read_whitelist(cfg: Dict[str, Any]):
    wl = cfg.get("whitelist_domains")
    if isinstance(wl, list) and wl:
        out = []
        for d in wl:
            d = d.strip().lower()
            if d.startswith("www."):
                d = d[4:]
            out.append(d)
        return out
    return None

def format_intro(now_local: datetime) -> str:
    return f"Good morning. Here is your Greater Houston Independent School Districts roundup for {now_local.strftime('%A, %B %d, %Y')}."

def format_outro() -> str:
    return "That’s all for today. Source links are in the show notes. See you tomorrow."

def build_script(items: List[Dict[str, Any]], tzname: str):
    tz = pytz.timezone(tzname)
    now_local = datetime.now(tz)
    intro = format_intro(now_local)

    blocks = [intro]
    notes_lines = ["Sources and links:"]

    for it in items:
        dt_local = it["published"].astimezone(tz)
        # Use %I and strip leading zero to avoid GNU-only %-I
        hour = dt_local.strftime("%I").lstrip("0") or "0"
        head = f"{it['title']} — {it['source'].replace('www.', '')} — posted at {hour}:{dt_local.strftime('%M %p %Z')}."
        body = it["body"]
        blocks.append(head + " " + body)
        notes_lines.append(f"- {it['title']} ({it['source'].replace('www.', '')}): {it['link']}")

    blocks.append(format_outro())
    script_text = "\n\n".join(blocks)
    notes = "\n".join(notes_lines)
    return script_text, notes

def bytes_of(path: str) -> int:
    return os.path.getsize(path) if os.path.exists(path) else 0

def cleanup_old_audio(retain_days: int):
    cutoff = datetime.utcnow() - timedelta(days=retain_days)
    for mp3 in glob.glob(os.path.join(AUDIO, "*.mp3")):
        name = os.path.basename(mp3)
        try:
            date_part = name.split("_")[-1].replace(".mp3", "")
            dt = datetime.strptime(date_part, "%Y-%m-%d")
            if dt < cutoff:
                os.remove(mp3)
        except Exception:
            continue

def load_existing_episodes(site_base_url: str):
    eps = []
    for mp3 in sorted(glob.glob(os.path.join(AUDIO, "*.mp3")), reverse=True):
        name = os.path.basename(mp3)
        date_part = name.split("_")[-1].replace(".mp3", "")
        try:
            dt = datetime.strptime(date_part, "%Y-%m-%d")
        except Exception:
            dt = datetime.utcnow()
        url = f"{site_base_url}/audio/{name}"
        length = bytes_of(mp3)
        eps.append({
            "title": f"Houston ISD Roundup — {date_part}",
            "date": dt,
            "url": url,
            "length": length,
            "page_url": site_base_url,
            "summary": ""
        })
    return eps

def main():
    os.makedirs(DOCS, exist_ok=True)
    os.makedirs(AUDIO, exist_ok=True)

    cfg = load_config()
    terms = load_terms()
    whitelist = read_whitelist(cfg)

    raw_items = fetch_feeds(terms)
    items = select_and_enrich(
        raw_items,
        max_articles=int(cfg.get("max_articles", 12)),
        whitelist_domains=whitelist,
        max_chars=int(cfg.get("max_chars_per_article", 2000)),
    )

    tzname = cfg.get("timezone", "America/Chicago")
    today_str = datetime.now(pytz.timezone(tzname)).strftime("%Y-%m-%d")
    base_name = f"{cfg.get('episode_prefix','houston-isd-roundup')}_{today_str}"

    script_text, notes = build_script(items, tzname)

    final_mp3 = synth_to_mp3(
        chunks=[script_text],
        voice=cfg.get("voice", "en-US-AriaNeural"),
        rate=cfg.get("voice_rate", "+0%"),
        outdir=AUDIO,
        basename=base_name
    )

    notes_path = os.path.join(DOCS, f"{base_name}.txt")
    with open(notes_path, "w") as f:
        f.write(notes)

    cleanup_old_audio(int(cfg.get("retain_days", 14)))

    site_base_url = cfg["site_base_url"].rstrip("/")
    episodes = load_existing_episodes(site_base_url)

    for ep in episodes:
        if ep["url"].endswith(f"{base_name}.mp3"):
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
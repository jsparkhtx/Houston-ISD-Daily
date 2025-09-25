import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import load_env, load_settings, DATA_DIR, EPISODES_DIR, FEED_DIR
from .gather import fetch_all, to_dicts
from .summarize import summarize_items
from .scriptwriter import build_script, build_show_notes
from .tts import synthesize
from .build_feed import build_or_update_feed

def run(target_date: str, tts: bool):
    load_env()
    settings = load_settings()

    # 1) Gather
    items = to_dicts(fetch_all())

    # 2) Slice into top stories and quick hits
    top = items[: settings.limits.top_stories]
    quick = items[settings.limits.top_stories : settings.limits.top_stories + settings.limits.quick_hits]

    # 3) Summarize
    top_s = summarize_items(top, max_chars=600)
    quick_s = summarize_items(quick, max_chars=300)

    # 4) Script + Notes
    if target_date == "today":
        dt = datetime.now(ZoneInfo("America/Chicago")).date()
    else:
        dt = datetime.fromisoformat(target_date).date()

    date_label = dt.strftime("%A, %B %d, %Y")
    day_dir = EPISODES_DIR / dt.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)

    script_text = build_script(date_label, top_s, quick_s)
    (day_dir / "script.md").write_text(script_text, encoding="utf-8")

    notes_text = build_show_notes(date_label, top_s + quick_s)
    (day_dir / "notes.md").write_text(notes_text, encoding="utf-8")

    # 5) TTS (optional)
    audio_path = None
    if tts:
        audio_path = synthesize(script_text, day_dir / "audio.mp3")

    # 6) Feed
    build_or_update_feed(FEED_DIR / "podcast.xml", EPISODES_DIR)

    print(f"Wrote episode: {day_dir}")

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="today", help="ISO date (YYYY-MM-DD) or 'today'")
    p.add_argument("--no-tts", action="store_true", help="Skip audio synthesis")
    return p.parse_args()

if __name__ == "__main__":
    args = parse_args()
    run(args.date, tts=(not args.no_tts))

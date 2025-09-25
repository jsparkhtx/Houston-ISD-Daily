from pathlib import Path
from datetime import datetime, timezone
from feedgen.feed import FeedGenerator
from typing import List
from .config import load_podcast_env

def build_or_update_feed(feed_path: Path, episodes_dir: Path) -> Path:
    pod = load_podcast_env()

    fg = FeedGenerator()
    fg.id(pod.link)
    fg.title(pod.title)
    fg.link(href=pod.link, rel='alternate')
    fg.link(href=pod.link + '/podcast.xml', rel='self')
    fg.author({'name': pod.author, 'email': pod.email})
    fg.logo(pod.art_url)
    fg.subtitle(pod.description)
    fg.language('en')

    # Episodes are folders with script.md and (optional) audio.mp3
    for day_dir in sorted(episodes_dir.glob("*/"), reverse=True):
        date_str = day_dir.name
        script_md = day_dir / "script.md"
        audio_mp3 = day_dir / "audio.mp3"
        notes_md = day_dir / "notes.md"
        if not script_md.exists():
            continue
        fe = fg.add_entry()
        fe.id(date_str)
        fe.title(f"ISD Daily — {date_str}")
        fe.link(href=f"{pod.link}/episodes/{date_str}")
        fe.published(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc))
        summary = (notes_md.read_text(encoding='utf-8')[:900] if notes_md.exists() else script_md.read_text(encoding='utf-8')[:900])
        fe.summary(summary)
        if audio_mp3.exists():
            fe.enclosure(url=f"{pod.link}/episodes/{date_str}/audio.mp3", length=str(audio_mp3.stat().st_size), type='audio/mpeg')

    feed_path.parent.mkdir(parents=True, exist_ok=True)
    fg.rss_file(feed_path)
    return feed_path

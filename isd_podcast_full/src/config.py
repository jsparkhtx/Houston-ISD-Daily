from dataclasses import dataclass
from pathlib import Path
import os
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
EPISODES_DIR = DATA_DIR / "episodes"
FEED_DIR = DATA_DIR / "feed"
STATE_PATH = DATA_DIR / "state.json"

@dataclass
class Limits:
    top_stories: int = 5
    quick_hits: int = 5

@dataclass
class Settings:
    feeds: list
    blocklist_domains: set
    soft_word_filters: set
    limits: Limits

@dataclass
class Podcast:
    title: str
    author: str
    email: str
    description: str
    link: str
    art_url: str


def load_env() -> None:
    load_dotenv()
    os.environ.setdefault("TZ", "America/Chicago")


def load_settings() -> Settings:
    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text(encoding="utf-8"))
    limits = Limits(**cfg.get("limits", {}))
    return Settings(
        feeds=cfg.get("feeds", []),
        blocklist_domains=set(cfg.get("blocklist_domains", [])),
        soft_word_filters=set(cfg.get("soft_word_filters", [])),
        limits=limits,
    )


def load_podcast_env() -> Podcast:
    return Podcast(
        title=os.environ.get("PODCAST_TITLE", "Automated ISD Daily"),
        author=os.environ.get("PODCAST_AUTHOR", "Automator"),
        email=os.environ.get("PODCAST_EMAIL", "noreply@example.com"),
        description=os.environ.get("PODCAST_DESCRIPTION", "Daily K-12 headlines for ISDs"),
        link=os.environ.get("PODCAST_LINK", "https://example.com/isd-podcast"),
        art_url=os.environ.get("PODCAST_ART_URL", "https://example.com/art.jpg"),
    )

# Ensure directories
for p in (DATA_DIR, EPISODES_DIR, FEED_DIR):
    p.mkdir(parents=True, exist_ok=True)

# Automated ISD Podcast

Daily pipeline that gathers school district news, writes a short script, optionally generates TTS audio, and updates a podcast RSS feed.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m src.main --date today --no-tts   # dry run without audio
```

Outputs land in `data/episodes/YYYY-MM-DD/` and an RSS file in `data/feed/podcast.xml`.

### Environment (.env)
- `TZ=America/Chicago`
- `TTS_PROVIDER=none`  # none|openai|elevenlabs|polly
- `OPENAI_API_KEY=...` # if using OpenAI TTS
- `ELEVEN_API_KEY=...` # if using ElevenLabs
- `AWS_ACCESS_KEY_ID=...` and `AWS_SECRET_ACCESS_KEY=...` and `AWS_REGION=us-east-1` # if using Polly
- `PODCAST_TITLE=Automated ISD Daily`
- `PODCAST_AUTHOR=Automator`
- `PODCAST_EMAIL=you@example.com`
- `PODCAST_DESCRIPTION=Daily K-12 headlines for ISDs`
- `PODCAST_LINK=https://example.com/isd-podcast`
- `PODCAST_ART_URL=https://example.com/artwork.jpg`

### Editing sources
Add/remove feeds in `sources.yaml`. Paywalled/blocked domains are listed under `blocklist_domains` and are skipped by design.

### Notes on paywalls (Houston Chronicle, etc.)
To respect site policies and avoid breakage, this pipeline **only reads RSS-provided fields** (title, summary, link, published). If an item is behind a paywall, we will use the summary only or skip per blocklist. Default config skips `houstonchronicle.com` items to prevent empty scripts.

### Local testing
```bash
pytest -q
```

### GitHub Actions
`daily.yml` runs on schedule and on manual dispatch. It caches pip deps for speed.

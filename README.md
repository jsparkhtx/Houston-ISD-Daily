# Automated ISD Podcast

Daily pipeline that gathers school district news, writes a short script, optionally generates TTS audio, and updates a podcast RSS feed.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m src.main --date today --no-tts   # dry run without audio

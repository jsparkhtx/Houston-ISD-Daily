# src/tts.py
import os
import tempfile
import subprocess
from pathlib import Path
from typing import Optional, List
from pydub import AudioSegment  # uses system ffmpeg

MAX_CHARS = 2500  # safety for CLI limits

def _chunk_text(text: str, n: int = MAX_CHARS) -> List[str]:
    text = (text or "").strip()
    if len(text) <= n:
        return [text] if text else []
    parts, s = [], text
    while s:
        chunk = s[:n]
        if " " in chunk and len(s) > n:
            chunk = chunk.rsplit(" ", 1)[0]
        parts.append(chunk)
        s = s[len(chunk):].lstrip()
    return parts

def _run(cmd: list) -> None:
    subprocess.run(cmd, check=True)

def synthesize(script_text: str, out_path: Path) -> Optional[Path]:
    """
    Offline TTS:
      - Linux (GitHub Actions): eSpeak NG -> wav -> mp3
      - macOS: 'say' -> aiff -> mp3
    Requires: ffmpeg in PATH (installed in the workflow).
    """
    provider = os.environ.get("TTS_PROVIDER", "espeak").lower()
    if provider == "none":
        return None

    chunks = _chunk_text(script_text)
    if not chunks:
        return None

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Collect part files then concatenate to MP3
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        part_files = []

        if provider in ("espeak", "espeak-ng", "linux"):
            # Voice and speed (female voice variants: f1..f5). Good default: en-us+f3
            voice = os.environ.get("ESPEAK_VOICE", "en-us+f3")
            wpm = os.environ.get("ESPEAK_WPM", "165")
            for i, chunk in enumerate(chunks, 1):
                wav = tmp_dir / f"part_{i:03d}.wav"
                _run(["espeak", "-v", voice, "-s", wpm, "-w", str(wav), chunk])
                part_files.append(wav)

        elif provider in ("say", "mac", "darwin"):
            # macOS built-in 'say'
            voice = os.environ.get("SAY_VOICE", "Samantha")  # female US voice
            rate = os.environ.get("SAY_WPM", "185")
            for i, chunk in enumerate(chunks, 1):
                aiff = tmp_dir / f"part_{i:03d}.aiff"
                _run(["say", "-v", voice, "-r", rate, "-o", str(aiff), chunk])
                # convert to wav for uniform concat
                wav = tmp_dir / f"part_{i:03d}.wav"
                _run(["ffmpeg", "-y", "-i", str(aiff), str(wav)])
                part_files.append(wav)
        else:
            raise ValueError(f"Unknown TTS_PROVIDER: {provider}")

        # Concatenate with pydub and export MP3
        mixed = AudioSegment.silent(duration=0)
        for f in part_files:
            seg = AudioSegment.from_file(f)
            mixed += seg

        mixed.export(out_path, format="mp3", bitrate="128k")
        return out_path

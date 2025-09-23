# src/tts.py  — gTTS-based TTS (no websockets)
from typing import List
from gtts import gTTS
from pydub import AudioSegment
import os

def synth_to_mp3(chunks: List[str], voice: str, rate: str, outdir: str, basename: str) -> str:
    """
    Generate an MP3 by concatenating TTS for each chunk.
    voice and rate are ignored by gTTS but kept for config compatibility.
    """
    os.makedirs(outdir, exist_ok=True)
    tmp_files = []
    try:
        # Make individual mp3s for each chunk
        for i, chunk in enumerate(chunks):
            tmp = os.path.join(outdir, f"{basename}_part{i}.mp3")
            tts = gTTS(text=chunk, lang="en", slow=False)
            tts.save(tmp)
            tmp_files.append(tmp)

        # Stitch with short pauses
        combined = AudioSegment.silent(duration=500)
        for f in tmp_files:
            combined += AudioSegment.from_file(f, format="mp3")
            combined += AudioSegment.silent(duration=250)

        final_path = os.path.join(outdir, f"{basename}.mp3")
        combined.export(final_path, format="mp3", bitrate="128k")
        return final_path
    finally:
        for f in tmp_files:
            try:
                os.remove(f)
            except Exception:
                pass

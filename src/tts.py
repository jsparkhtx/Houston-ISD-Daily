import asyncio
import os
import edge_tts
from typing import List
from pydub import AudioSegment

async def _speak(text: str, voice: str, rate: str, outfile: str):
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    await communicate.save(outfile)

def synth_to_mp3(chunks: List[str], voice: str, rate: str, outdir: str, basename: str) -> str:
    tmp_files = []
    try:
        for i, chunk in enumerate(chunks):
            outpath = os.path.join(outdir, f"{basename}_part{i}.mp3")
            asyncio.get_event_loop().run_until_complete(_speak(chunk, voice, rate, outpath))
            tmp_files.append(outpath)

        combined = AudioSegment.silent(duration=500)
        for t in tmp_files:
            seg = AudioSegment.from_file(t, format="mp3")
            combined += seg + AudioSegment.silent(duration=250)

        final_path = os.path.join(outdir, f"{basename}.mp3")
        combined.export(final_path, format="mp3", bitrate="128k")
        return final_path
    finally:
        for t in tmp_files:
            if os.path.exists(t):
                os.remove(t)
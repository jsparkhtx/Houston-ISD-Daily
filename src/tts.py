# src/tts.py
import os
from pathlib import Path
from typing import Optional

def synthesize(script_text: str, out_path: Path) -> Optional[Path]:
    provider = os.environ.get("TTS_PROVIDER", "none").lower()
    if provider == "none":
        return None

    if provider == "polly":
        import boto3
        voice = os.environ.get("POLLY_VOICE_ID", "Joanna")   # e.g., Joanna, Matthew, Lupe (es-US), Ruth (en-US NeMo)
        fmt = os.environ.get("POLLY_OUTPUT_FORMAT", "mp3")   # mp3|ogg_vorbis|pcm
        engine = os.environ.get("POLLY_ENGINE", "standard")  # standard|neural (varies by voice/region)

        polly = boto3.client("polly", region_name=os.environ.get("AWS_REGION"))
        # Split very long scripts to be safe (Polly limit ~3k chars)
        chunks, MAX = [], 2800
        text = script_text.strip()
        while text:
            chunks.append(text[:MAX])
            text = text[MAX:]

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "wb") as f:
            for chunk in chunks:
                resp = polly.synthesize_speech(
                    Text=chunk,
                    OutputFormat=fmt,
                    VoiceId=voice,
                    Engine=engine,
                )
                f.write(resp["AudioStream"].read())
        return out_path

    # You can add other providers here later (OpenAI, ElevenLabs).
    raise ValueError(f"Unknown TTS_PROVIDER: {provider}")

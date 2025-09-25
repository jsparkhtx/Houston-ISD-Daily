import os
from pathlib import Path
from typing import Optional
from pydub import AudioSegment

# Pluggable TTS. Defaults to no-op. You can add your preferred provider safely.

def synthesize(script_text: str, out_path: Path) -> Optional[Path]:
    provider = os.environ.get("TTS_PROVIDER", "none").lower()
    if provider == "none":
        return None

    if provider == "openai":
        # Placeholder: requires OPENAI_API_KEY and a model that supports TTS.
        # Implement your call and save to wav/mp3. For now, raise to avoid silent failures.
        raise NotImplementedError("OpenAI TTS not implemented in this template.")

    if provider == "elevenlabs":
        raise NotImplementedError("ElevenLabs TTS not implemented in this template.")

    if provider == "polly":
        # Example skeleton (uncomment & implement if you want Polly):
        # import boto3
        # polly = boto3.client('polly')
        # resp = polly.synthesize_speech(Text=script_text, VoiceId='Joanna', OutputFormat='mp3')
        # with open(out_path, 'wb') as f:
        #     f.write(resp['AudioStream'].read())
        # return out_path
        raise NotImplementedError("AWS Polly TTS not implemented in this template.")

    raise ValueError(f"Unknown TTS_PROVIDER: {provider}")

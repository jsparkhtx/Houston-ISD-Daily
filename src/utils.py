import re
from urllib.parse import urlparse

def clean_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def hostname(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""

def domain_from_host(host: str) -> str:
    if not host:
        return ""
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host
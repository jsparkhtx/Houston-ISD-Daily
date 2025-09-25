import re
from html import unescape
from bs4 import BeautifulSoup

WHITESPACE_RE = re.compile(r"\s+")

def strip_html(text: str) -> str:
    if not text:
        return ""
    # Quick drop of scripts/styles + text extraction
    soup = BeautifulSoup(text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    return WHITESPACE_RE.sub(" ", unescape(text)).strip()

def compact(text: str) -> str:
    return WHITESPACE_RE.sub(" ", (text or "").strip())

def safe_truncate(text: str, n: int = 400) -> str:
    t = compact(text)
    if len(t) <= n:
        return t
    cut = t[:n]
    # avoid mid-word cut
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"

from typing import List, Dict
from .cleaners import safe_truncate

# Lightweight extractive summary: title + shortened summary.
# (Model-based abstractive summaries can be dropped in later.)

def summarize_items(items: List[Dict], max_chars: int = 700) -> List[Dict]:
    out = []
    for it in items:
        text = f"{it['title']}. {it['summary']}"
        out.append({
            **it,
            "blurb": safe_truncate(text, max_chars)
        })
    return out

from datetime import datetime
from typing import List, Dict

INTRO = (
    "Good morning! Here are today’s top K‑12 and ISD headlines for %s. "
    "We’ll start with the big stories, then a few quick hits."
)
OUTRO = (
    "That’s your daily update. Links to all stories are in the show notes. "
    "Have a great day."
)

def build_script(date_label: str, top_stories: List[Dict], quick_hits: List[Dict]) -> str:
    lines = []
    lines.append(INTRO % date_label)
    lines.append("")

    if top_stories:
        lines.append("Top stories:")
        for i, it in enumerate(top_stories, 1):
            lines.append(f"{i}. {it['title']} — {it['blurb']} (Source: {it['source']}).")
        lines.append("")

    if quick_hits:
        lines.append("Quick hits:")
        for it in quick_hits:
            lines.append(f"- {it['title']} — {it['blurb']} (Source: {it['source']}).")
        lines.append("")

    lines.append(OUTRO)
    return "\n".join(lines).strip()

def build_show_notes(date_label: str, items: List[Dict]) -> str:
    md = [f"# ISD Daily — {date_label}", ""]
    for it in items:
        md.append(f"- [{it['title']}]({it['link']}) — {it['source']}")
    return "\n".join(md) + "\n"

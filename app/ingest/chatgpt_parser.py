from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


@dataclass
class ParsedChatSource:
    """Normalized metadata and text sample from a ChatGPT export."""

    source_type: str
    metadata: Dict[str, object]
    text_sample: str
    keywords: List[str]


KEYWORD_PATTERNS = [
    "deterministic accept", "override", "grounding", "batch", "resolver",
    "publish_bootstrap", "reviewer_type", "Blue Mountains", "Dewa Sanzan",
    "Yamagata", "Sendai", "Wikivoyage", "Wikipedia", "source_url",
    "object_registry", "destarea_autofix", "Qwen alias", "cache", "NAS",
]


def parse_chat_source(path: Path) -> ParsedChatSource:
    """Parse supported ChatGPT source shapes into a common review input."""
    if path.is_dir():
        return _parse_multipart_dir(path)
    if path.suffix.lower() == ".json":
        return _parse_json(path)
    return _parse_markdown(path)


def _parse_json(path: Path) -> ParsedChatSource:
    obj = json.loads(path.read_text(encoding="utf-8"))
    messages = obj.get("messages") or []
    seqs = [m.get("seq") for m in messages if isinstance(m.get("seq"), int)]
    roles = Counter(m.get("role") or "unknown" for m in messages)
    missing = []
    if seqs:
        present = set(seqs)
        missing = [n for n in range(min(seqs), max(seqs) + 1) if n not in present]
    text = "\n\n".join(str(m.get("text") or m.get("content") or "") for m in messages[:80])
    metadata = {
        "format": "json",
        "top_level_keys": sorted(obj.keys()),
        "export_meta": obj.get("export_meta") or {},
        "message_count": len(messages),
        "seq_range": [min(seqs), max(seqs)] if seqs else None,
        "missing_seq_count": len(missing),
        "missing_seq": missing[:100],
        "role_counts": dict(roles),
        "code_block_messages": sum(1 for m in messages if m.get("code_blocks")),
    }
    return ParsedChatSource("json", metadata, text[:120000], _keywords(text))


def _parse_multipart_dir(path: Path) -> ParsedChatSource:
    manifest = path / "manifest.json.txt"
    parts = sorted(path.glob("*.md"))
    chunks = []
    if manifest.exists():
        chunks.append(manifest.read_text(encoding="utf-8", errors="replace"))
    for part in parts[:10]:
        chunks.append(part.read_text(encoding="utf-8", errors="replace")[:20000])
    text = "\n\n".join(chunks)
    seq_ranges = [p.name for p in parts]
    metadata = {
        "format": "multipart_md_dir",
        "part_count": len(parts),
        "has_manifest": manifest.exists(),
        "part_files": seq_ranges,
    }
    return ParsedChatSource("multipart_md_dir", metadata, text[:120000], _keywords(text))


def _parse_markdown(path: Path) -> ParsedChatSource:
    text = path.read_text(encoding="utf-8", errors="replace")
    headings = re.findall(r"^#+\s+(.+)$", text, flags=re.MULTILINE)
    metadata = {
        "format": "md",
        "line_count": len(text.splitlines()),
        "headings": headings[:20],
    }
    return ParsedChatSource("md", metadata, text[:120000], _keywords(text))


def _keywords(text: str) -> List[str]:
    lowered = text.lower()
    found = []
    for pattern in KEYWORD_PATTERNS:
        if pattern.lower() in lowered:
            found.append(pattern)
    return found

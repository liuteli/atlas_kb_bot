from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from app.common.committed_cache import CommittedCodeCache
from app.common.config import Settings
from app.common.json_store import JsonStore
from app.common.logging_utils import log_event, setup_logger
from app.common.subprocess_utils import run_cmd
from app.ingest.chatgpt_detector import ChatGPTSourceDetector, SourceRecord
from app.ingest.chatgpt_parser import ParsedChatSource, parse_chat_source


@dataclass
class ReviewResult:
    """Paths and status from a review-only audit run."""

    source_id: str
    output_dir: Path
    summary_path: Path
    fallback_used: bool
    fallback_reason: Optional[str]


def refresh_reference_code_cache(settings: Settings, dry_run: bool = False):
    """Refresh the shared Atlas committed-code cache before any source review."""
    return CommittedCodeCache(settings.github_cache_root).refresh(
        "atlas",
        settings.atlas_repo_path,
        repo_url=settings.github_repo_url,
        branch=settings.github_default_branch,
        keep_commit_snapshot=settings.keep_commit_snapshots,
        dry_run=dry_run,
    )


class ChatHistoryReviewRunner:
    """Run review-only audits for ChatGPT exports.

    Phase 1 intentionally writes only audit artifacts. It does not apply
    wiki patches, move source files, or mutate Atlas runtime.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = setup_logger("knowledge_bot_review", settings.log_root)
        self.detector = ChatGPTSourceDetector(
            settings.chatgpt_export_root,
            settings.state_root / "chatgpt_sources.json",
        )
        self.status_store = JsonStore(settings.state_root / "review_runs.json")

    def review(self, source_id_or_path: str) -> ReviewResult:
        """Refresh code cache, parse source, compare wiki/code, and write six files."""
        log_event(self.logger, event="review_received", source_id=source_id_or_path, status="received")
        try:
            result = self._review_impl(source_id_or_path)
            log_event(
                self.logger,
                event="review_completed",
                source_id=result.source_id,
                review_id=result.output_dir.name,
                status="success",
                summary=f"fallback_used={result.fallback_used}",
            )
            return result
        except Exception as exc:
            log_event(
                self.logger,
                event="review_failed",
                source_id=source_id_or_path,
                status="failure",
                level="error",
                summary=str(exc),
            )
            raise

    def _review_impl(self, source_id_or_path: str) -> ReviewResult:
        self.settings.ensure_runtime_dirs()
        record = self._resolve_source(source_id_or_path)
        source_path = Path(record.path)
        parsed = parse_chat_source(source_path)
        cache_result = refresh_reference_code_cache(self.settings, dry_run=False)
        log_event(
            self.logger,
            event="cache_refreshed",
            source_id=record.source_id,
            status="success",
            summary=f"commit={cache_result.commit} source_mode={cache_result.source_mode} updated={cache_result.updated}",
        )
        fallback_used = False
        fallback_reason = None
        code_hits = self._search_code_cache(cache_result.latest_path, parsed.keywords)
        if not code_hits and self.settings.allow_atlas_readonly_fallback:
            fallback_used = True
            fallback_reason = "committed cache had no relevant hits for parsed source keywords"
            code_hits = self._search_atlas_readonly(parsed.keywords)
            log_event(
                self.logger,
                event="atlas_readonly_fallback",
                source_id=record.source_id,
                status="used",
                summary=fallback_reason,
            )

        wiki_hits = self._search_wiki(parsed.keywords)
        output_dir = self._make_output_dir(record.source_id)
        files = self._render_files(record, parsed, cache_result.commit, cache_result.latest_path, wiki_hits, code_hits, fallback_used, fallback_reason)
        for name, content in files.items():
            self._write(output_dir / name, content)
        self._record_status(record, output_dir, fallback_used, fallback_reason)
        return ReviewResult(record.source_id, output_dir, output_dir / "summary.md", fallback_used, fallback_reason)

    def _resolve_source(self, source_id_or_path: str) -> SourceRecord:
        candidates = self.detector.scan_and_update()
        for record in candidates:
            if record.source_id == source_id_or_path:
                return record
        prefix_matches = [record for record in candidates if record.source_id.startswith(source_id_or_path)]
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        if len(prefix_matches) > 1:
            raise KeyError(f"ambiguous source id prefix: {source_id_or_path}")
        path = Path(source_id_or_path)
        if path.exists():
            source_type = "multipart_md_dir" if path.is_dir() else path.suffix.lower().lstrip(".")
            return SourceRecord("adhoc_" + re.sub(r"[^a-zA-Z0-9]+", "_", path.name)[:40], source_type, str(path), path.name, "adhoc source", path.stat().st_size if path.is_file() else 0)
        raise KeyError(f"source not found: {source_id_or_path}")

    def _make_output_dir(self, source_id: str) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = self.settings.review_output_root / f"{source_id}_{stamp}"
        out.mkdir(parents=True, exist_ok=True)
        return out

    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)

    def _search_wiki(self, keywords: List[str]) -> Dict[str, List[str]]:
        roots = [
            self.settings.knowledge_vault_root / "00_HOME",
            self.settings.knowledge_vault_root / "01_BOOK",
            self.settings.knowledge_vault_root / "02_WIKI",
            self.settings.knowledge_vault_root / "03_INDEX",
        ]
        return self._python_text_search(roots, keywords, suffixes={".md"}, limit=80)

    def _search_code_cache(self, cache_latest: Path, keywords: List[str]) -> Dict[str, List[str]]:
        if not cache_latest.exists():
            return {}
        return self._python_text_search([cache_latest], keywords, suffixes={".py", ".sql", ".md", ".toml", ".yaml", ".yml"}, limit=120)

    def _search_atlas_readonly(self, keywords: List[str]) -> Dict[str, List[str]]:
        # Read-only fallback: rg only, no writes and no git mutation.
        hits: Dict[str, List[str]] = {}
        for keyword in keywords[:12]:
            result = run_cmd(["rg", "-n", keyword, str(self.settings.atlas_repo_path)])
            if result.returncode in {0, 1}:
                lines = result.stdout.splitlines()[:20]
                if lines:
                    hits[keyword] = lines
        return hits

    def _python_text_search(self, roots: List[Path], keywords: List[str], suffixes: set, limit: int) -> Dict[str, List[str]]:
        hits: Dict[str, List[str]] = {}
        if not keywords:
            return hits
        lowered_keywords = [(k, k.lower()) for k in keywords]
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in suffixes:
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                lower = text.lower()
                for original, lowered in lowered_keywords:
                    if lowered in lower:
                        hits.setdefault(original, []).append(str(path))
                        if sum(len(v) for v in hits.values()) >= limit:
                            return hits
        return hits

    def _knowledge_rows(self, parsed: ParsedChatSource) -> List[Dict[str, str]]:
        rows = []
        for keyword in parsed.keywords:
            if keyword in {"publish_bootstrap", "reviewer_type"}:
                coverage = "needs_followup_before_promotion"
            elif keyword in {"deterministic accept", "grounding", "batch", "resolver"}:
                coverage = "candidate_for_resolver_or_troubleshooting_page"
            elif keyword in {"Wikivoyage", "Wikipedia", "source_url"}:
                coverage = "candidate_for_source_ingest_or_script_map_page"
            else:
                coverage = "raw_or_contextual_candidate"
            rows.append({
                "keyword": keyword,
                "candidate_type": coverage,
                "promotion_rule": "review-only; no automatic wiki apply in Phase 1",
            })
        if not rows:
            rows.append({
                "keyword": "general_chat_history",
                "candidate_type": "raw_only_until_manual_review",
                "promotion_rule": "insufficient high-signal keywords for automatic shortlist",
            })
        return rows

    def _render_files(
        self,
        record: SourceRecord,
        parsed: ParsedChatSource,
        commit: str,
        cache_latest: Path,
        wiki_hits: Dict[str, List[str]],
        code_hits: Dict[str, List[str]],
        fallback_used: bool,
        fallback_reason: Optional[str],
    ) -> Dict[str, str]:
        rows = self._knowledge_rows(parsed)
        table = "\n".join(
            f"| {r['keyword']} | {r['candidate_type']} | {r['promotion_rule']} |" for r in rows
        )
        metadata_json = json.dumps(parsed.metadata, ensure_ascii=False, indent=2)
        summary = f"""# Review Summary

Source: `{record.display_name}`

Source id: `{record.source_id}`

Phase: review-only

Current committed code cache: `{cache_latest}` at commit `{commit}`

Fallback to Atlas runtime read-only: `{fallback_used}`

Fallback reason: `{fallback_reason or 'none'}`

## Parsed Metadata

```json
{metadata_json}
```

## High-Signal Keywords

{', '.join(parsed.keywords) if parsed.keywords else 'none'}

## Output Contract

This review generated six core files and did not apply wiki changes.
"""
        inventory = f"""# Extracted Knowledge Inventory

| Signal | Candidate Type | Promotion Rule |
|---|---|---|
{table}
"""
        wiki_lines = self._render_hit_lines(wiki_hits)
        code_lines = self._render_hit_lines(code_hits)
        return {
            "summary.md": summary,
            "extracted_knowledge_inventory.md": inventory,
            "wiki_coverage_matrix.md": f"# Wiki Coverage Matrix\n\n{wiki_lines}\n",
            "code_consistency_audit.md": f"# Code Consistency Audit\n\nCommitted cache commit: `{commit}`\n\nFallback used: `{fallback_used}`\n\nFallback reason: `{fallback_reason or 'none'}`\n\n{code_lines}\n",
            "backfill_shortlist.md": "# Backfill Shortlist\n\nPhase 1 is review-only. Items marked as candidates require human review before apply.\n",
            "applied_mapping_or_apply_plan.md": "# Apply Plan\n\nNo apply was executed. Future actions are reserved for explicit approve/apply flow.\n",
        }

    def _render_hit_lines(self, hits: Dict[str, List[str]]) -> str:
        if not hits:
            return "No direct hits found."
        lines = []
        for keyword, paths in hits.items():
            lines.append(f"## {keyword}")
            for path in paths[:20]:
                lines.append(f"- `{path}`")
        return "\n".join(lines)

    def _record_status(self, record: SourceRecord, output_dir: Path, fallback_used: bool, fallback_reason: Optional[str]) -> None:
        state = self.status_store.read()
        runs = state.setdefault("runs", [])
        runs.append({
            "source_id": record.source_id,
            "source_path": record.path,
            "output_dir": str(output_dir),
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        state["runs"] = runs[-100:]
        self.status_store.write(state)

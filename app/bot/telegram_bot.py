from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.common.config import Settings
from app.common.json_store import JsonStore
from app.common.logging_utils import SG_TZ, log_event, setup_logger, singapore_now
from app.ingest.audit_runner import ChatHistoryReviewRunner
from app.ingest.chatgpt_detector import ChatGPTSourceDetector, short_source_id
from app.reports.daily_backup_report import DailyBackupReport


class TelegramReviewBot:
    """Minimal Telegram long-polling bot using the Bot API directly."""

    COMMANDS = ("/sources", "/review", "/status", "/backup_report", "/whoami")

    def __init__(self, settings: Settings) -> None:
        if not settings.telegram_bot_token:
            raise RuntimeError("KB_TELEGRAM_BOT_TOKEN is required to start bot")
        self.settings = settings
        self.token = settings.telegram_bot_token
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.logger = setup_logger("knowledge_bot_telegram", settings.log_root)
        self.detector = ChatGPTSourceDetector(
            settings.chatgpt_export_root,
            settings.state_root / "chatgpt_sources.json",
        )
        self.runner = ChatHistoryReviewRunner(settings)
        self.daily_report = DailyBackupReport(settings)
        self.daily_report_state = JsonStore(settings.state_root / "daily_backup_report_state.json")
        self.last_scheduler_check_monotonic = 0.0

    @classmethod
    def registered_commands(cls) -> List[str]:
        """Return supported command names for smoke tests and documentation."""
        return list(cls.COMMANDS)

    @staticmethod
    def whoami_text(message: Dict[str, object]) -> str:
        """Render chat/user identity without requiring allowlist access."""
        chat = message.get("chat") or {}
        user = message.get("from") or {}
        chat_id = chat.get("id")
        user_id = user.get("id")
        chat_type = chat.get("type")
        username = user.get("username") or chat.get("username") or ""
        title = chat.get("title") or ""
        lines = [
            f"chat_id: {chat_id}",
            f"user_id: {user_id}",
            f"chat_type: {chat_type}",
        ]
        if username:
            lines.append(f"username: {username}")
        if title:
            lines.append(f"title: {title}")
        return "\n".join(lines)

    def run_forever(self) -> None:
        """Poll Telegram for commands until interrupted."""
        self.settings.ensure_runtime_dirs()
        log_event(
            self.logger,
            event="bot_startup",
            status="starting",
            summary=f"registered_commands={','.join(self.COMMANDS)}",
        )
        offset: Optional[int] = None
        while True:
            self._run_daily_backup_report_scheduler()
            try:
                updates = self._get_updates(offset)
            except Exception as exc:
                log_event(
                    self.logger,
                    event="telegram_poll",
                    level="error",
                    status="failure",
                    summary=f"getUpdates failed: {exc}",
                )
                time.sleep(5)
                continue
            for update in updates:
                offset = update.get("update_id", 0) + 1
                try:
                    self._handle_update(update)
                except Exception as exc:
                    log_event(
                        self.logger,
                        event="telegram_update",
                        level="error",
                        status="failure",
                        summary=f"update handling failed: {exc}",
                    )
            time.sleep(1)

    def _get_updates(self, offset: Optional[int]) -> List[Dict[str, object]]:
        params = {"timeout": 25}
        if offset is not None:
            params["offset"] = offset
        data = self._api("getUpdates", params)
        return data.get("result") or []

    def _message_context(self, message: Dict[str, object]) -> Dict[str, str]:
        chat = message.get("chat") or {}
        user = message.get("from") or {}
        username = user.get("username") or chat.get("username") or chat.get("title") or ""
        return {
            "chat_id": str(chat.get("id", "")),
            "user_id": str(user.get("id", "")),
            "username": str(username),
        }

    def _command_from_text(self, text: str) -> str:
        if not text:
            return ""
        first = text.split(maxsplit=1)[0]
        return first.split("@", 1)[0]

    def _handle_update(self, update: Dict[str, object]) -> None:
        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        command = self._command_from_text(text)
        ctx = self._message_context(message)
        chat_id = ctx["chat_id"]
        if command:
            log_event(self.logger, event="command_received", command=command, status="received", **ctx)

        if command == "/whoami":
            self._send(chat_id, self.whoami_text(message))
            log_event(self.logger, event="command_completed", command="/whoami", status="success", **ctx)
            return

        if self.settings.allowed_chat_ids and chat_id not in self.settings.allowed_chat_ids:
            self._send(chat_id, "This chat is not allowed for knowledge-bot. Send /whoami to get the chat_id.")
            log_event(
                self.logger,
                event="command_rejected",
                command=command or "unknown",
                status="failure",
                summary="chat_id not in KB_ALLOWED_CHAT_IDS",
                **ctx,
            )
            return

        if command == "/sources":
            self._handle_sources(chat_id, ctx)
        elif command == "/review":
            self._handle_review(chat_id, text, ctx)
        elif command == "/status":
            self._send_text(chat_id, self._status_text())
            log_event(self.logger, event="command_completed", command="/status", status="success", **ctx)
        elif command == "/backup_report":
            self._handle_backup_report(chat_id, ctx)
        else:
            self._send_text(chat_id, "Commands: /sources, /review <source_id>, /status, /backup_report, /whoami")
            log_event(
                self.logger,
                event="command_rejected",
                command=command or "unknown",
                status="failure",
                summary="unknown command",
                **ctx,
            )

    def _handle_sources(self, chat_id: str, ctx: Dict[str, str]) -> None:
        records = self.detector.scan_and_update()
        self._send_text(chat_id, self._sources_text(records))
        log_event(
            self.logger,
            event="command_completed",
            command="/sources",
            status="success",
            summary=f"source_count={len(records)}",
            **ctx,
        )

    def _handle_review(self, chat_id: str, text: str, ctx: Dict[str, str]) -> None:
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            self._send_text(chat_id, "Usage: /review <source_id>")
            log_event(
                self.logger,
                event="command_rejected",
                command="/review",
                status="failure",
                summary="missing source_id",
                **ctx,
            )
            return
        source_id = parts[1].strip()
        try:
            body, review_id = self._review_text(source_id)
            self._send_text(chat_id, body)
            log_event(
                self.logger,
                event="command_completed",
                command="/review",
                source_id=source_id,
                review_id=review_id,
                status="success",
                summary="review complete",
                **ctx,
            )
        except Exception as exc:
            self._send_text(chat_id, f"Review failed for {source_id}: {exc}")
            log_event(
                self.logger,
                event="command_failed",
                command="/review",
                source_id=source_id,
                status="failure",
                level="error",
                summary=f"review failed: {exc}",
                **ctx,
            )

    def _handle_backup_report(self, chat_id: str, ctx: Dict[str, str]) -> None:
        try:
            self._send_backup_report_to_chat(chat_id)
            log_event(
                self.logger,
                event="backup_report_command_completed",
                command="/backup_report",
                status="success",
                **ctx,
            )
        except Exception as exc:
            self._send_text(chat_id, f"Backup report failed: {exc}")
            log_event(
                self.logger,
                event="backup_report_command_failed",
                command="/backup_report",
                level="error",
                status="failure",
                summary=f"backup report failed: {exc}",
                **ctx,
            )

    def _sources_text(self, records) -> str:
        if not records:
            return "No pending ChatGPT sources."
        lines = ["Pending ChatGPT sources:"]
        for record in records[:20]:
            short_id = short_source_id(record.source_id)
            summary = record.rough_summary[:100]
            source_time = self._source_time_hint(record.display_name)
            lines.append(
                f"- {short_id}\n"
                f"  file: {record.display_name}\n"
                f"  time: {source_time}\n"
                f"  summary: {summary}\n"
                f"  review: /review {short_id}"
            )
        return "\n".join(lines)

    def _source_time_hint(self, display_name: str) -> str:
        # Most ChatGPT exports start with YYYY-MM-DD or include the date in the filename.
        return display_name[:10] if len(display_name) >= 10 and display_name[4:5] == "-" else "unknown"

    def _review_text(self, source_id: str) -> Tuple[str, str]:
        result = self.runner.review(source_id)
        summary = result.summary_path.read_text(encoding="utf-8")[:2500]
        return f"Review complete: {result.output_dir}\n\n{summary}", result.output_dir.name

    def _status_text(self) -> str:
        store = JsonStore(self.settings.state_root / "review_runs.json")
        runs = (store.read().get("runs") or [])[-10:]
        if not runs:
            return "No review runs recorded."
        lines = ["Recent review runs:"]
        for run in runs:
            lines.append(f"- {run.get('source_id')} -> {run.get('output_dir')}")
        return "\n".join(lines)

    def _send_text(self, chat_id: str, text: str) -> None:
        for chunk in self._split_message(text):
            self._send(chat_id, chunk)

    def _send(self, chat_id: str, text: str) -> None:
        self._api("sendMessage", {"chat_id": chat_id, "text": text[:3900]})

    def _send_backup_report_to_chat(self, chat_id: str) -> None:
        self._send_text(chat_id, self.daily_report.render())

    def _run_daily_backup_report_scheduler(self) -> None:
        now_monotonic = time.monotonic()
        if now_monotonic - self.last_scheduler_check_monotonic < 60:
            return
        self.last_scheduler_check_monotonic = now_monotonic
        try:
            now = singapore_now()
            state = self.daily_report_state.read()
            if not self.settings.daily_backup_report_enabled:
                log_event(
                    self.logger,
                    event="daily_backup_report_skipped",
                    status="disabled",
                    summary="KB_DAILY_BACKUP_REPORT_ENABLED=0",
                )
                return
            if not self.settings.allowed_chat_ids:
                log_event(
                    self.logger,
                    event="daily_backup_report_skipped",
                    level="warning",
                    status="failure",
                    summary="no allowed chat ids configured for daily backup report",
                )
                return
            if state.get("last_sent_date") == now.date().isoformat():
                return
            send_at = self._today_at(self.settings.daily_backup_report_time, now)
            cutoff_at = self._today_at(self.settings.daily_backup_report_cutoff, now)
            if now < send_at:
                log_event(
                    self.logger,
                    event="daily_backup_report_scheduler_check",
                    status="waiting",
                    summary=f"before send window {self.settings.daily_backup_report_time}",
                )
                return
            if now > cutoff_at:
                log_event(
                    self.logger,
                    event="daily_backup_report_skipped",
                    status="cutoff",
                    summary=f"missed cutoff {self.settings.daily_backup_report_cutoff} for {now.date().isoformat()}",
                )
                return
            log_event(
                self.logger,
                event="daily_backup_report_scheduler_check",
                status="ready",
                summary=f"attempting daily report for {now.date().isoformat()}",
            )
            for chat_id in self.settings.allowed_chat_ids:
                self._send_backup_report_to_chat(chat_id)
            sent_at = singapore_now().isoformat(timespec="seconds")
            self.daily_report_state.write({
                "last_sent_date": now.date().isoformat(),
                "last_sent_at": sent_at,
                "last_status": "success",
            })
            log_event(
                self.logger,
                event="daily_backup_report_sent",
                status="success",
                summary=f"chat_count={len(self.settings.allowed_chat_ids)} sent_at={sent_at}",
            )
        except Exception as exc:
            self.daily_report_state.write({
                "last_sent_date": state.get("last_sent_date", ""),
                "last_sent_at": state.get("last_sent_at", ""),
                "last_status": "failure",
            })
            log_event(
                self.logger,
                event="daily_backup_report_failed",
                level="error",
                status="failure",
                summary=f"daily report send failed: {exc}",
            )

    @staticmethod
    def _today_at(hhmm: str, now: datetime) -> datetime:
        hour, minute = TelegramReviewBot._parse_hhmm(hhmm)
        return datetime(now.year, now.month, now.day, hour, minute, tzinfo=SG_TZ)

    @staticmethod
    def _parse_hhmm(value: str) -> Tuple[int, int]:
        hour_text, minute_text = value.strip().split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError(f"invalid HH:MM value: {value}")
        return hour, minute

    @staticmethod
    def _split_message(text: str, limit: int = 3900) -> List[str]:
        if len(text) <= limit:
            return [text]
        chunks: List[str] = []
        remaining = text
        while len(remaining) > limit:
            split_at = remaining.rfind("\n\n", 0, limit)
            if split_at <= 0:
                split_at = remaining.rfind("\n", 0, limit)
            if split_at <= 0:
                split_at = limit
            chunk = remaining[:split_at].strip()
            if chunk:
                chunks.append(chunk)
            remaining = remaining[split_at:].strip()
        if remaining:
            chunks.append(remaining)
        return chunks

    def _api(self, method: str, params: Dict[str, object]) -> Dict[str, object]:
        payload = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(f"{self.base_url}/{method}", data=payload)
        with urllib.request.urlopen(req, timeout=35) as resp:
            return json.loads(resp.read().decode("utf-8"))

#!/usr/bin/env python3
"""
Windowed Telegram admin tool.

Features:
- Search target user's messages and show message IDs in a table.
- Archive text-only chat to HTML/CSV.
- Download media to a folder.
- Preview and delete a message ID with sender verification.
"""

from __future__ import annotations

import asyncio
import csv
import html
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

from tkinter import filedialog, messagebox, simpledialog, ttk

from telethon import TelegramClient, errors
from telethon.tl.functions.messages import GetHistoryRequest
from telethon.tl.types import Channel, Chat, Message
from telethon.utils import get_peer_id


SCRIPT_VERSION = "2026-07-01-admin-gui-v2-parallel"
APP_CREDIT = "Built by Syn3xar"
DEFAULT_API_ID = ""
DEFAULT_API_HASH = ""
DEFAULT_ADMIN_ID = ""
DEFAULT_GROUP_ID = ""
DEFAULT_TARGET_ID = ""
DEFAULT_PHONE = ""
DEFAULT_PASSWORD = ""
DEFAULT_OUTPUT_DIR = "telegram_admin_exports"
DEFAULT_PARALLEL_DOWNLOADS = 4
DEFAULT_PARALLEL_SMALL = 4
DEFAULT_PARALLEL_LARGE = 2
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
TRANSIENT_CONNECTION_ERRORS = (
    ConnectionError,
    OSError,
    TimeoutError,
    asyncio.IncompleteReadError,
)
TRANSIENT_CONNECTION_TEXT = (
    "server closed the connection",
    "connection reset",
    "connection aborted",
    "connection lost",
    "not connected",
    "timed out",
    "timeout",
    "transport closed",
    "disconnected",
    "request was unsuccessful",
    "filemigrate",
    "stored in dc",
)


@dataclass
class AppConfig:
    api_id: int
    api_hash: str
    admin_user_id: int
    group_id: int
    target_user_id: int
    phone: str
    password: str
    output_dir: Path
    batch_size: int
    parallel_downloads: int
    parallel_small: int
    parallel_large: int
    download_target_only: bool


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def clean_name(value: str, fallback: str = "untitled") -> str:
    value = INVALID_FILENAME_CHARS.sub("_", value).strip(" ._")
    value = re.sub(r"\s+", " ", value)
    return value[:120] or fallback


def format_bytes(value: int | float | None) -> str:
    if value is None:
        return "unknown"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def is_transient_error(exc: BaseException) -> bool:
    if isinstance(exc, TRANSIENT_CONNECTION_ERRORS):
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(pattern in text for pattern in TRANSIENT_CONNECTION_TEXT)


def normalized_chat_ids(chat_id: int) -> set[int]:
    ids = {int(chat_id), abs(int(chat_id))}
    text = str(chat_id)
    if text.startswith("-100"):
        ids.add(int(text[4:]))
    return ids


def entity_ids(entity: object) -> set[int]:
    ids: set[int] = set()
    entity_id = getattr(entity, "id", None)
    if entity_id is not None:
        ids.add(int(entity_id))
    try:
        ids.add(int(get_peer_id(entity)))
    except TypeError:
        pass
    return ids


def dialog_kind(entity: object) -> str:
    if isinstance(entity, Channel):
        if getattr(entity, "broadcast", False):
            return "channel"
        if getattr(entity, "megagroup", False):
            return "supergroup"
        return "channel_or_group"
    if isinstance(entity, Chat):
        return "group"
    return type(entity).__name__


def entity_display_label(entity: object) -> str:
    title = getattr(entity, "title", None)
    if title:
        return title
    parts = [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]
    display = " ".join(part for part in parts if part)
    username = getattr(entity, "username", None)
    if display and username:
        return f"{display} (@{username})"
    if display:
        return display
    if username:
        return f"@{username}"
    entity_id = getattr(entity, "id", None)
    return f"user_{entity_id}" if entity_id else "unknown"


def sender_label(sender_id: object, labels: dict[str, str]) -> str:
    if sender_id is None:
        return "unknown"
    return labels.get(str(sender_id), f"sender_{sender_id}")


def message_media_type(message: Message) -> str:
    if message.photo:
        return "photos"
    if message.video:
        return "videos"
    if message.voice:
        return "voice"
    if message.audio:
        return "audio"
    if message.sticker:
        return "stickers"
    if message.gif:
        return "gifs"
    if message.document:
        return "documents"
    if message.media:
        return "other"
    return ""


def message_excerpt(message: Message, limit: int = 900) -> str:
    text = (message.message or "").replace("\r", " ").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text and message.media:
        text = f"({message_media_type(message) or type(message.media).__name__})"
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def expected_media_size(message: Message) -> int:
    file_info = getattr(message, "file", None)
    size = getattr(file_info, "size", None)
    return int(size) if size else 0


def file_is_complete(path: Path, expected_size: int) -> bool:
    if not path.exists():
        return False
    actual = path.stat().st_size
    if expected_size > 0:
        return actual >= expected_size
    return actual > 0


def remove_incomplete_file(path: Path, expected_size: int, emit, worker_label: str) -> None:
    if path.exists() and not file_is_complete(path, expected_size):
        emit(
            "log",
            f"[{worker_label}] Removing incomplete file {path.name} "
            f"({format_bytes(path.stat().st_size)} / {format_bytes(expected_size)}).",
        )
        path.unlink()


class DownloadProgress:
    def __init__(self, label: str, emit, interval: float = 2.0) -> None:
        self.label = label
        self.emit = emit
        self.interval = interval
        self.last_print = 0.0
        self.last_progress = time.monotonic()
        self.last_bucket = -1
        self.current = 0

    def __call__(self, current: int, total: int) -> None:
        now = time.monotonic()
        if current != self.current:
            self.last_progress = now
        self.current = current

        if total:
            percent = min(100, int((current / total) * 100))
            bucket = percent // 5
            if percent != 100 and bucket == self.last_bucket and now - self.last_print < self.interval:
                return
            self.emit(
                "log",
                f"progress {percent:3d}% | {format_bytes(current)} / {format_bytes(total)} | {self.label}",
            )
            self.last_bucket = bucket
            self.last_print = now
        elif now - self.last_print >= self.interval:
            self.emit("log", f"progress {format_bytes(current)} | {self.label}")
            self.last_print = now


class MediaTransferCoordinator:
    def __init__(self, total_limit: int, small_limit: int, large_limit: int, emit) -> None:
        self.max_total = max(1, total_limit)
        self.current_total = self.max_total
        self.small_limit = max(1, min(small_limit, self.max_total))
        self.large_limit = max(1, min(large_limit, self.max_total))
        self.emit = emit
        self.active_total = 0
        self.active_small = 0
        self.active_large = 0
        self.condition = asyncio.Condition()
        self.connection_lock = asyncio.Lock()
        self.pause_until = 0.0
        self.error_times: list[float] = []
        self.success_streak = 0

    async def wait_if_paused(self) -> None:
        while True:
            remaining = self.pause_until - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 1.0))

    async def acquire(self, size_class: str) -> None:
        while True:
            await self.wait_if_paused()
            async with self.condition:
                class_active = self.active_large if size_class == "large" else self.active_small
                class_limit = self.large_limit if size_class == "large" else self.small_limit
                if self.active_total < self.current_total and class_active < class_limit:
                    self.active_total += 1
                    if size_class == "large":
                        self.active_large += 1
                    else:
                        self.active_small += 1
                    return
                await self.condition.wait()

    async def release(self, size_class: str) -> None:
        async with self.condition:
            self.active_total = max(0, self.active_total - 1)
            if size_class == "large":
                self.active_large = max(0, self.active_large - 1)
            else:
                self.active_small = max(0, self.active_small - 1)
            self.condition.notify_all()

    async def pause_for_flood_wait(self, seconds: int, worker_label: str) -> None:
        seconds = max(1, int(seconds))
        self.pause_until = max(self.pause_until, time.monotonic() + seconds)
        async with self.condition:
            if self.current_total > 1:
                self.current_total -= 1
                self.emit(
                    "log",
                    f"[{worker_label}] Flood wait: pausing new work for {seconds}s and "
                    f"reducing concurrency to {self.current_total}.",
                )
            else:
                self.emit("log", f"[{worker_label}] Flood wait: pausing new work for {seconds}s.")
            self.success_streak = 0
            self.condition.notify_all()
        await self.wait_if_paused()

    async def note_error(self, worker_label: str) -> None:
        now = time.monotonic()
        async with self.condition:
            self.error_times = [stamp for stamp in self.error_times if now - stamp <= 60]
            self.error_times.append(now)
            self.success_streak = 0
            if len(self.error_times) >= 3 and self.current_total > 1:
                self.current_total -= 1
                self.error_times.clear()
                self.emit(
                    "log",
                    f"[{worker_label}] Repeated transfer errors; concurrency reduced to {self.current_total}.",
                )
            self.condition.notify_all()

    async def note_success(self) -> None:
        async with self.condition:
            self.success_streak += 1
            recovery_target = max(20, self.current_total * 10)
            if self.current_total < self.max_total and self.success_streak >= recovery_target:
                self.current_total += 1
                self.success_streak = 0
                self.emit("log", f"Connection stable; concurrency restored to {self.current_total}.")
                self.condition.notify_all()

    async def ensure_connected(self, client: TelegramClient, worker_label: str) -> None:
        if client.is_connected():
            return
        async with self.connection_lock:
            if not client.is_connected():
                self.emit("log", f"[{worker_label}] Reconnecting Telegram client...")
                await client.connect()


async def download_media_with_retries(
    client: TelegramClient,
    message: Message,
    target_path: Path,
    expected_size: int,
    stop_event: threading.Event,
    coordinator: MediaTransferCoordinator,
    worker_label: str,
    emit,
    retry_attempts: int = 8,
    retry_delay: int = 10,
    stall_timeout: int = 60,
) -> str:
    attempt = 0
    while not stop_event.is_set():
        await coordinator.wait_if_paused()
        try:
            remove_incomplete_file(target_path, expected_size, emit, worker_label)
            emit("log", f"[{worker_label}] Downloading #{message.id}: {target_path.name}")
            progress = DownloadProgress(f"{worker_label} | {target_path.name}", emit)
            download_task = asyncio.create_task(
                client.download_media(message, file=str(target_path), progress_callback=progress)
            )
            while not download_task.done():
                await asyncio.sleep(0.5)
                if stop_event.is_set():
                    download_task.cancel()
                    try:
                        await download_task
                    except asyncio.CancelledError:
                        pass
                    remove_incomplete_file(target_path, expected_size, emit, worker_label)
                    return ""
                stalled_for = time.monotonic() - progress.last_progress
                if stalled_for >= stall_timeout:
                    download_task.cancel()
                    try:
                        await download_task
                    except asyncio.CancelledError:
                        pass
                    raise TimeoutError(f"download stalled for {int(stalled_for)}s")

            saved_path = await download_task
            if saved_path:
                emit("log", f"[{worker_label}] Saved: {saved_path}")
            return saved_path or ""
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                raise
            attempt += 1
            await coordinator.note_error(worker_label)
            if attempt > retry_attempts:
                remove_incomplete_file(target_path, expected_size, emit, worker_label)
                return ""
            wait_seconds = min(60, max(1, retry_delay) * attempt)
            emit(
                "log",
                f"[{worker_label}] Telegram cancelled this transfer during connection recovery. "
                f"Retrying {attempt}/{retry_attempts} after {wait_seconds}s.",
            )
            remove_incomplete_file(target_path, expected_size, emit, worker_label)
            await asyncio.sleep(wait_seconds)
        except errors.FloodWaitError as exc:
            await coordinator.pause_for_flood_wait(exc.seconds, worker_label)
        except errors.ServerError as exc:
            attempt += 1
            await coordinator.note_error(worker_label)
            if attempt > retry_attempts:
                emit("log", f"[{worker_label}] Telegram server error persisted: {exc}. Item remains pending.")
                remove_incomplete_file(target_path, expected_size, emit, worker_label)
                return ""
            wait_seconds = min(60, max(1, retry_delay) * attempt)
            emit(
                "log",
                f"[{worker_label}] Temporary Telegram server error: {exc}. "
                f"Retrying {attempt}/{retry_attempts} after {wait_seconds}s without resetting other workers.",
            )
            remove_incomplete_file(target_path, expected_size, emit, worker_label)
            await asyncio.sleep(wait_seconds)
        except Exception as exc:
            if not is_transient_error(exc):
                raise
            attempt += 1
            await coordinator.note_error(worker_label)
            if attempt > retry_attempts:
                emit("log", f"[{worker_label}] Retry limit reached: {exc}. Item remains pending.")
                remove_incomplete_file(target_path, expected_size, emit, worker_label)
                return ""
            wait_seconds = min(60, max(1, retry_delay) * attempt)
            emit(
                "log",
                f"[{worker_label}] Connection error: {exc}. "
                f"Retrying {attempt}/{retry_attempts} after {wait_seconds}s.",
            )
            await asyncio.sleep(wait_seconds)
            await coordinator.ensure_connected(client, worker_label)

    return ""


async def get_message_with_retries(
    client: TelegramClient,
    entity: object,
    message_id: int,
    stop_event: threading.Event,
    coordinator: MediaTransferCoordinator,
    worker_label: str,
    emit,
    retry_attempts: int = 8,
    retry_delay: int = 10,
) -> Message | None:
    attempt = 0
    while not stop_event.is_set():
        await coordinator.wait_if_paused()
        try:
            return await client.get_messages(entity, ids=message_id)
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                raise
            attempt += 1
            await coordinator.note_error(worker_label)
        except errors.FloodWaitError as exc:
            await coordinator.pause_for_flood_wait(exc.seconds, worker_label)
            continue
        except errors.ServerError as exc:
            attempt += 1
            await coordinator.note_error(worker_label)
            emit("log", f"[{worker_label}] Temporary Telegram error while refreshing #{message_id}: {exc}")
        except Exception as exc:
            if not is_transient_error(exc):
                raise
            attempt += 1
            await coordinator.note_error(worker_label)
            emit("log", f"[{worker_label}] Connection error while refreshing #{message_id}: {exc}")
            await coordinator.ensure_connected(client, worker_label)

        if attempt > retry_attempts:
            raise RuntimeError(f"Could not refresh message {message_id} after {retry_attempts} retries.")
        wait_seconds = min(60, max(1, retry_delay) * attempt)
        emit(
            "log",
            f"[{worker_label}] Retrying message refresh {attempt}/{retry_attempts} after {wait_seconds}s.",
        )
        await asyncio.sleep(wait_seconds)
    return None


def message_download_path(folder: Path, message: Message, labels: dict[str, str]) -> Path:
    sender = clean_name(sender_label(message.sender_id, labels), f"sender_{message.sender_id or 'unknown'}")
    date_part = message.date.strftime("%Y%m%d") if message.date else "unknown_date"
    media_type = message_media_type(message) or "media"
    original = clean_name(getattr(getattr(message, "file", None), "name", "") or "")
    ext = getattr(getattr(message, "file", None), "ext", None) or ""
    if original:
        filename = f"{date_part}_{message.id}_{sender}_{original}"
    else:
        filename = f"{date_part}_{message.id}_{sender}_{media_type}_{message.id}{ext}"
    return folder / clean_name(filename, f"{message.id}_{media_type}")


async def find_dialog_by_chat_id(client: TelegramClient, group_id: int):
    wanted = normalized_chat_ids(group_id)
    async for dialog in client.iter_dialogs():
        if entity_ids(dialog.entity) & wanted:
            return dialog
    entity = await client.get_entity(group_id)
    return SimpleNamespace(entity=entity, name=getattr(entity, "title", str(group_id)))


async def load_participant_labels(client: TelegramClient, entity: object, emit) -> dict[str, str]:
    labels: dict[str, str] = {}
    try:
        emit("log", "Loading participant names...")
        async for participant in client.iter_participants(entity):
            participant_id = str(getattr(participant, "id", ""))
            if participant_id:
                labels[participant_id] = entity_display_label(participant)
        emit("log", f"Loaded {len(labels)} participant names.")
    except errors.RPCError as exc:
        emit("log", f"Could not load participants: {exc}. Sender IDs will still work.")
    return labels


async def fetch_history_page(client: TelegramClient, entity: object, offset_id: int, limit: int):
    return await client(
        GetHistoryRequest(
            peer=entity,
            offset_id=offset_id,
            offset_date=None,
            add_offset=0,
            limit=limit,
            max_id=0,
            min_id=0,
            hash=0,
        )
    )


def prompt_from_gui(emit, title: str, message: str, hidden: bool = False, default: str = "") -> str:
    response_queue: queue.Queue[str | None] = queue.Queue(maxsize=1)
    emit(
        "prompt",
        {
            "title": title,
            "message": message,
            "hidden": hidden,
            "default": default,
            "response_queue": response_queue,
        },
    )
    response = response_queue.get()
    if response is None:
        raise RuntimeError(f"{title} cancelled.")
    return response.strip()


async def ensure_authorized(client: TelegramClient, config: AppConfig, emit) -> None:
    await client.connect()
    if await client.is_user_authorized():
        return

    if not config.phone:
        raise RuntimeError("Phone number is required for first Telegram login.")

    emit("log", f"Telegram session is not authorized. Sending login code to {config.phone}...")
    await client.send_code_request(config.phone)
    for attempt in range(1, 4):
        code = prompt_from_gui(
            emit,
            "Telegram Login Code",
            f"Enter the Telegram login code sent to {config.phone}:",
        )
        if not code:
            raise RuntimeError("Telegram login code is required.")
        try:
            await client.sign_in(config.phone, code)
            return
        except errors.SessionPasswordNeededError:
            password = config.password.strip()
            if not password:
                password = prompt_from_gui(
                    emit,
                    "Telegram 2FA Password",
                    "Enter your Telegram two-step verification password:",
                    hidden=True,
                )
            await client.sign_in(password=password)
            return
        except errors.PhoneCodeInvalidError:
            emit("log", f"Telegram login code was invalid. Attempt {attempt}/3.")
            if attempt == 3:
                raise
        except errors.PhoneCodeExpiredError:
            emit("log", "Telegram login code expired. Sending a new code...")
            await client.send_code_request(config.phone)


async def connect_and_prepare(config: AppConfig, emit):
    session_path = app_base_dir() / "telegram_admin_gui"
    client = TelegramClient(
        str(session_path),
        config.api_id,
        config.api_hash,
        connection_retries=8,
        retry_delay=10,
        auto_reconnect=True,
    )
    await ensure_authorized(client, config, emit)
    me = await client.get_me()
    if int(me.id) != int(config.admin_user_id):
        await client.disconnect()
        raise RuntimeError(
            f"Logged in as Telegram user ID {me.id}, but admin ID field is {config.admin_user_id}."
        )
    emit("log", f"Logged in as {entity_display_label(me)} ({me.id}).")
    dialog = await find_dialog_by_chat_id(client, config.group_id)
    entity = dialog.entity
    title = getattr(entity, "title", getattr(dialog, "name", str(config.group_id)))
    emit("log", f"Matched group: {title} ({dialog_kind(entity)}, id={getattr(entity, 'id', '')}).")
    labels = await load_participant_labels(client, entity, emit)
    return client, entity, title, labels


async def iter_history(client: TelegramClient, entity: object, batch_size: int, stop_event: threading.Event, emit):
    offset_id = 0
    page = 0
    while not stop_event.is_set():
        page += 1
        history = await fetch_history_page(client, entity, offset_id, batch_size)
        messages = [message for message in history.messages if getattr(message, "id", None)]
        if not messages:
            emit("log", "Telegram returned no older messages.")
            break
        newest = messages[0]
        oldest = messages[-1]
        emit("log", f"Page {page}: {len(messages)} messages, {newest.id} down to {oldest.id}.")
        for message in messages:
            offset_id = int(message.id)
            yield message
            if stop_event.is_set():
                break


def html_header(title: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
body{{font-family:Segoe UI,Tahoma,Arial,sans-serif;background:#eef3f7;color:#0f172a}}
main{{max-width:1120px;margin:0 auto;padding:18px}}
.msg{{background:#fff;border:1px solid #d6e1eb;border-radius:8px;margin:8px 0;padding:10px}}
.meta{{color:#64748b;font-size:13px}}
.text{{white-space:pre-wrap;line-height:1.45}}
.credit{{margin-top:24px;color:#64748b;font-size:13px;text-align:right}}
</style></head><body><main><h1>{html.escape(title)}</h1>
"""


def html_footer() -> str:
    return f'<div class="credit">{html.escape(APP_CREDIT)}</div></main></body></html>\n'


async def search_target_messages(config: AppConfig, stop_event: threading.Event, emit) -> None:
    client = None
    try:
        client, entity, _title, labels = await connect_and_prepare(config, emit)
        report_dir = config.output_dir / clean_name(
            f"search_user_{config.target_user_id}_{datetime.now():%Y%m%d_%H%M%S}"
        )
        report_dir.mkdir(parents=True, exist_ok=True)
        csv_path = report_dir / "target_messages.csv"
        html_path = report_dir / "target_messages.html"
        count = 0
        emit("clear_rows", None)
        emit("log", "Searching target user's messages from newest to oldest.")
        with csv_path.open("w", newline="", encoding="utf-8") as csv_file, html_path.open("w", encoding="utf-8") as html_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=["message_id", "message_date", "sender_id", "sender_label", "text", "media_type", "reply_to_msg_id"],
            )
            writer.writeheader()
            html_file.write(html_header(f"Messages by user {config.target_user_id}"))
            async for message in iter_history(client, entity, config.batch_size, stop_event, emit):
                if int(message.sender_id or 0) != int(config.target_user_id):
                    continue
                count += 1
                reply_id = getattr(getattr(message, "reply_to", None), "reply_to_msg_id", "")
                label = sender_label(message.sender_id, labels)
                text = message_excerpt(message, 2000)
                media_type = message_media_type(message)
                row = {
                    "message_id": message.id,
                    "message_date": message.date.isoformat() if message.date else "",
                    "sender_id": message.sender_id or "",
                    "sender_label": label,
                    "text": text,
                    "media_type": media_type,
                    "reply_to_msg_id": reply_id or "",
                }
                writer.writerow(row)
                emit("row", row)
                emit("log", f"Found #{message.id}: {message_excerpt(message, 160)}")
                html_file.write(
                    f"<div class=\"msg\"><div class=\"meta\">#{message.id} | "
                    f"{html.escape(message.date.isoformat() if message.date else '')} | "
                    f"{html.escape(label)} | {html.escape(media_type or 'text')}</div>"
                    f"<div class=\"text\">{html.escape(text)}</div></div>"
                )
            html_file.write(html_footer())
        emit("log", f"Search complete. Found {count} messages.")
        emit("log", f"CSV: {csv_path}")
        emit("log", f"HTML: {html_path}")
    finally:
        if client:
            await client.disconnect()


async def archive_text_chat(config: AppConfig, stop_event: threading.Event, emit) -> None:
    client = None
    try:
        client, entity, _title, labels = await connect_and_prepare(config, emit)
        report_dir = config.output_dir / clean_name(f"text_archive_{datetime.now():%Y%m%d_%H%M%S}")
        report_dir.mkdir(parents=True, exist_ok=True)
        csv_path = report_dir / "text_chat.csv"
        html_path = report_dir / "text_chat.html"
        count = 0
        emit("log", "Archiving text-only chat. Media will not be downloaded.")
        with csv_path.open("w", newline="", encoding="utf-8") as csv_file, html_path.open("w", encoding="utf-8") as html_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=["message_id", "message_date", "sender_id", "sender_label", "text", "reply_to_msg_id"],
            )
            writer.writeheader()
            html_file.write(html_header("Text Chat Archive"))
            async for message in iter_history(client, entity, config.batch_size, stop_event, emit):
                if not message.message:
                    continue
                count += 1
                reply_id = getattr(getattr(message, "reply_to", None), "reply_to_msg_id", "")
                label = sender_label(message.sender_id, labels)
                writer.writerow(
                    {
                        "message_id": message.id,
                        "message_date": message.date.isoformat() if message.date else "",
                        "sender_id": message.sender_id or "",
                        "sender_label": label,
                        "text": message.message or "",
                        "reply_to_msg_id": reply_id or "",
                    }
                )
                html_file.write(
                    f"<div class=\"msg\"><div class=\"meta\">#{message.id} | "
                    f"{html.escape(message.date.isoformat() if message.date else '')} | "
                    f"{html.escape(label)}</div><div class=\"text\">{html.escape(message.message or '')}</div></div>"
                )
            html_file.write(html_footer())
        emit("log", f"Text archive complete. Archived {count} text messages.")
        emit("log", f"CSV: {csv_path}")
        emit("log", f"HTML: {html_path}")
    finally:
        if client:
            await client.disconnect()


async def download_media(config: AppConfig, stop_event: threading.Event, emit) -> None:
    client = None
    try:
        client, entity, _title, labels = await connect_and_prepare(config, emit)
        scope_name = f"target_{config.target_user_id}" if config.download_target_only else "all"
        media_dir = config.output_dir / clean_name(f"media_{scope_name}")
        media_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = media_dir / "media_manifest.csv"
        fieldnames = [
            "message_id",
            "message_date",
            "sender_id",
            "sender_label",
            "media_type",
            "saved_path",
            "text",
        ]
        manifest_paths: dict[int, str] = {}
        if manifest_path.exists():
            try:
                with manifest_path.open("r", newline="", encoding="utf-8") as existing_manifest:
                    for row in csv.DictReader(existing_manifest):
                        message_id = int(row.get("message_id") or 0)
                        saved_path = row.get("saved_path", "")
                        if message_id and saved_path:
                            manifest_paths[message_id] = saved_path
            except (OSError, ValueError, csv.Error) as exc:
                emit("log", f"Could not read existing manifest; file checks will still be used: {exc}")

        scanned = 0
        queued: list[tuple[Message, Path, int, str]] = []
        known_remaining_size = 0
        unknown_remaining_size = 0
        emit("log", f"Scanning complete group history before media download. Resume folder: {media_dir}")
        async for message in iter_history(client, entity, config.batch_size, stop_event, emit):
            scanned += 1
            if config.download_target_only and int(message.sender_id or 0) != int(config.target_user_id):
                continue
            if not message.media:
                continue
            expected = expected_media_size(message)
            target_path = message_download_path(media_dir, message, labels)
            previous_path = manifest_paths.get(int(message.id), "")
            if previous_path and file_is_complete(Path(previous_path), expected):
                target_path = Path(previous_path)
            queued.append((message, target_path, expected, message_media_type(message)))
            if not file_is_complete(target_path, expected):
                if expected:
                    known_remaining_size += expected
                else:
                    unknown_remaining_size += 1

        if stop_event.is_set():
            emit("log", f"Stopped after scanning {scanned} messages. No new media work was started.")
            return

        emit(
            "log",
            f"Scan complete: {scanned} messages checked; {len(queued)} media items queued; "
            f"{format_bytes(known_remaining_size)} known remaining; "
            f"{unknown_remaining_size} items with unknown size.",
        )
        if not queued:
            emit("log", "No matching media was found.")
            return

        total_limit = config.parallel_downloads
        small_limit = min(config.parallel_small, total_limit)
        large_limit = min(config.parallel_large, total_limit)
        coordinator = MediaTransferCoordinator(total_limit, small_limit, large_limit, emit)
        manifest_lock = asyncio.Lock()
        small_queue: asyncio.Queue[tuple[Message, Path, int, str]] = asyncio.Queue()
        large_queue: asyncio.Queue[tuple[Message, Path, int, str]] = asyncio.Queue()
        large_threshold = 20 * 1024 * 1024
        for item in queued:
            expected = item[2]
            if expected <= 0 or expected > large_threshold:
                large_queue.put_nowait(item)
            else:
                small_queue.put_nowait(item)

        stats = {"downloaded": 0, "skipped": 0, "failed": 0}
        manifest_exists = manifest_path.exists() and manifest_path.stat().st_size > 0
        emit(
            "log",
            f"Parallel capacity: {total_limit} total, {small_limit} small-file slots, "
            f"{large_limit} large/unknown-file slots. Adaptive backoff enabled.",
        )

        with manifest_path.open("a", newline="", encoding="utf-8") as manifest_file:
            writer = csv.DictWriter(
                manifest_file,
                fieldnames=fieldnames,
            )
            if not manifest_exists:
                writer.writeheader()
                manifest_file.flush()

            async def media_worker(
                worker_label: str,
                work_queue: asyncio.Queue[tuple[Message, Path, int, str]],
                size_class: str,
            ) -> None:
                while not stop_event.is_set():
                    try:
                        scanned_message, target_path, expected, media_type = work_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return

                    await coordinator.acquire(size_class)
                    succeeded = False
                    try:
                        message = await get_message_with_retries(
                            client,
                            entity,
                            int(scanned_message.id),
                            stop_event,
                            coordinator,
                            worker_label,
                            emit,
                        )
                        if not message:
                            stats["failed"] += 1
                            emit("log", f"[{worker_label}] Message #{scanned_message.id} is unavailable.")
                            continue

                        expected = expected_media_size(message) or expected
                        saved_path = ""
                        already_manifested = int(message.id) in manifest_paths
                        if file_is_complete(target_path, expected):
                            saved_path = str(target_path)
                            stats["skipped"] += 1
                            emit("log", f"[{worker_label}] Skipped existing #{message.id}: {target_path.name}")
                        else:
                            saved_path = await download_media_with_retries(
                                client,
                                message,
                                target_path,
                                expected,
                                stop_event,
                                coordinator,
                                worker_label,
                                emit,
                            )
                            if saved_path:
                                stats["downloaded"] += 1

                        if not saved_path:
                            stats["failed"] += 1
                            continue

                        async with manifest_lock:
                            if not already_manifested or manifest_paths.get(int(message.id)) != saved_path:
                                writer.writerow(
                                    {
                                        "message_id": message.id,
                                        "message_date": message.date.isoformat() if message.date else "",
                                        "sender_id": message.sender_id or "",
                                        "sender_label": sender_label(message.sender_id, labels),
                                        "media_type": media_type,
                                        "saved_path": saved_path,
                                        "text": message_excerpt(message, 500),
                                    }
                                )
                                manifest_file.flush()
                                manifest_paths[int(message.id)] = saved_path
                        succeeded = True
                    except Exception as exc:
                        stats["failed"] += 1
                        emit(
                            "log",
                            f"[{worker_label}] Media #{scanned_message.id} failed with "
                            f"{type(exc).__name__}: {exc}. It remains pending; worker continues.",
                        )
                    finally:
                        await coordinator.release(size_class)
                        work_queue.task_done()
                        if succeeded:
                            await coordinator.note_success()

            small_workers = min(small_limit, small_queue.qsize())
            large_workers = min(large_limit, large_queue.qsize())
            workers = [
                *(
                    media_worker(f"S{number:02d}", small_queue, "small")
                    for number in range(1, small_workers + 1)
                ),
                *(
                    media_worker(f"L{number:02d}", large_queue, "large")
                    for number in range(1, large_workers + 1)
                )
            ]
            await asyncio.gather(*workers)

        emit(
            "log",
            f"Media run complete. Scanned {scanned}, downloaded {stats['downloaded']}, "
            f"skipped {stats['skipped']}, pending/failed {stats['failed']}.",
        )
        emit("log", f"Media folder: {media_dir}")
        emit("log", f"Manifest: {manifest_path}")
    finally:
        if client:
            await client.disconnect()


async def preview_message(config: AppConfig, message_id: int, emit) -> None:
    client = None
    try:
        client, entity, _title, labels = await connect_and_prepare(config, emit)
        message = await client.get_messages(entity, ids=message_id)
        if not message:
            emit("log", f"Message {message_id} was not found or is not accessible.")
            return
        emit("log", "")
        emit("log", f"Message ID: {message.id}")
        emit("log", f"Date: {message.date.isoformat() if message.date else 'unknown'}")
        emit("log", f"Sender ID: {message.sender_id or 'unknown'}")
        emit("log", f"Sender: {sender_label(message.sender_id, labels)}")
        emit("log", f"Media: {message_media_type(message) or 'none'}")
        emit("log", f"Text/media preview: {message_excerpt(message)}")
    finally:
        if client:
            await client.disconnect()


async def delete_message(config: AppConfig, message_id: int, allow_any_sender: bool, emit) -> None:
    client = None
    try:
        client, entity, _title, labels = await connect_and_prepare(config, emit)
        message = await client.get_messages(entity, ids=message_id)
        if not message:
            emit("log", f"Message {message_id} was not found or is not accessible.")
            return
        emit("log", f"Delete preview for #{message.id}: sender {message.sender_id}, {message_excerpt(message)}")
        if int(message.sender_id or 0) != int(config.target_user_id) and not allow_any_sender:
            emit(
                "log",
                f"Refusing to delete: sender is {message.sender_id}, target user is {config.target_user_id}.",
            )
            return
        await client.delete_messages(entity, [message_id], revoke=True)
        emit("log", f"Delete request sent for message {message_id}. Verifying...")
        check = await client.get_messages(entity, ids=message_id)
        if not check or type(check).__name__ == "MessageEmpty":
            emit("log", f"Message {message_id}: CONFIRMED DELETED.")
        else:
            emit("log", f"Message {message_id}: still visible or Telegram returned a non-empty response.")
    finally:
        if client:
            await client.disconnect()


class TelegramAdminGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"Telegram Admin Tool - {APP_CREDIT}")
        self.root.geometry("1180x760")
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()

        self.api_id = tk.StringVar(value=DEFAULT_API_ID)
        self.api_hash = tk.StringVar(value=DEFAULT_API_HASH)
        self.admin_id = tk.StringVar(value=DEFAULT_ADMIN_ID)
        self.group_id = tk.StringVar(value=DEFAULT_GROUP_ID)
        self.target_id = tk.StringVar(value=DEFAULT_TARGET_ID)
        self.phone = tk.StringVar(value=DEFAULT_PHONE)
        self.password = tk.StringVar(value=DEFAULT_PASSWORD)
        self.output_dir = tk.StringVar(value=DEFAULT_OUTPUT_DIR)
        self.batch_size = tk.StringVar(value="100")
        self.parallel_downloads = tk.StringVar(value=str(DEFAULT_PARALLEL_DOWNLOADS))
        self.parallel_small = tk.StringVar(value=str(DEFAULT_PARALLEL_SMALL))
        self.parallel_large = tk.StringVar(value=str(DEFAULT_PARALLEL_LARGE))
        self.message_id = tk.StringVar(value="")
        self.download_target_only = tk.BooleanVar(value=False)
        self.allow_any_sender = tk.BooleanVar(value=False)
        self.status_text = tk.StringVar(value=f"Ready - {APP_CREDIT}")

        self.build_ui()
        self.log_line(APP_CREDIT)
        self.root.after(100, self.poll_events)

    def build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        form = ttk.LabelFrame(outer, text="Connection and Search Fields", padding=10)
        form.pack(fill=tk.X)
        for col in range(8):
            form.columnconfigure(col, weight=1)

        self.add_field(form, "Admin user ID", self.admin_id, 0, 0)
        self.add_field(form, "Group ID", self.group_id, 0, 2)
        self.add_field(form, "Target user ID", self.target_id, 0, 4)
        self.add_field(form, "Phone", self.phone, 0, 6)
        self.add_field(form, "API ID", self.api_id, 1, 0)
        self.add_field(form, "API hash", self.api_hash, 1, 2)
        self.add_field(form, "2FA password", self.password, 1, 4, show="*")
        self.add_field(form, "Batch size", self.batch_size, 1, 6)
        self.add_field(form, "Total streams", self.parallel_downloads, 2, 0)
        self.add_field(form, "Small streams", self.parallel_small, 2, 2)
        self.add_field(form, "Large streams", self.parallel_large, 2, 4)
        ttk.Label(form, text="Recommended: 4 / 4 / 2").grid(row=2, column=6, columnspan=2, sticky="w", pady=4)

        ttk.Label(form, text="Output folder").grid(row=3, column=0, sticky="w", padx=(0, 4), pady=4)
        ttk.Entry(form, textvariable=self.output_dir).grid(row=3, column=1, columnspan=6, sticky="ew", padx=(0, 8), pady=4)
        ttk.Button(form, text="Browse", command=self.browse_output).grid(row=3, column=7, sticky="ew", pady=4)

        actions = ttk.LabelFrame(outer, text="Actions", padding=10)
        actions.pack(fill=tk.X, pady=(10, 0))
        for col in range(8):
            actions.columnconfigure(col, weight=1)
        ttk.Button(actions, text="Search Target Messages", command=self.search_messages).grid(row=0, column=0, sticky="ew", padx=4, pady=3)
        ttk.Button(actions, text="Archive Text Chat", command=self.archive_text).grid(row=0, column=1, sticky="ew", padx=4, pady=3)
        ttk.Button(actions, text="Download Media", command=self.download_media).grid(row=0, column=2, sticky="ew", padx=4, pady=3)
        ttk.Checkbutton(actions, text="Only target media", variable=self.download_target_only).grid(row=0, column=3, sticky="w", padx=4, pady=3)

        ttk.Label(actions, text="Message ID").grid(row=1, column=0, sticky="w", padx=4, pady=3)
        ttk.Entry(actions, textvariable=self.message_id).grid(row=1, column=1, sticky="ew", padx=4, pady=3)
        ttk.Button(actions, text="Preview Message", command=self.preview_selected_message).grid(row=1, column=2, sticky="ew", padx=4, pady=3)
        ttk.Button(actions, text="Delete Message", command=self.delete_selected_message).grid(row=1, column=3, sticky="ew", padx=4, pady=3)
        ttk.Checkbutton(actions, text="Allow any sender", variable=self.allow_any_sender).grid(row=1, column=4, sticky="w", padx=4, pady=3)
        ttk.Button(actions, text="Stop Current Task", command=self.stop_current_task).grid(row=1, column=5, sticky="ew", padx=4, pady=3)

        body = ttk.PanedWindow(outer, orient=tk.VERTICAL)
        body.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        table_frame = ttk.LabelFrame(body, text="Detected Target Messages", padding=8)
        columns = ("message_id", "message_date", "sender_label", "media_type", "reply_to_msg_id", "text")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=12)
        widths = {
            "message_id": 90,
            "message_date": 190,
            "sender_label": 170,
            "media_type": 90,
            "reply_to_msg_id": 110,
            "text": 520,
        }
        for column in columns:
            self.tree.heading(column, text=column)
            self.tree.column(column, width=widths[column], stretch=column == "text")
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        table_scroll_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        table_scroll_x = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=table_scroll_y.set, xscrollcommand=table_scroll_x.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        table_scroll_y.grid(row=0, column=1, sticky="ns")
        table_scroll_x.grid(row=1, column=0, sticky="ew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        log_frame = ttk.LabelFrame(body, text="Status and Text Screen", padding=8)
        self.log = tk.Text(log_frame, height=12, wrap=tk.WORD)
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        log_scroll.grid(row=0, column=1, sticky="ns")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        body.add(table_frame, weight=3)
        body.add(log_frame, weight=2)

        footer = ttk.Frame(outer)
        footer.pack(fill=tk.X, pady=(6, 0))
        status = ttk.Label(footer, textvariable=self.status_text, anchor="w")
        status.pack(side=tk.LEFT, fill=tk.X, expand=True)
        credit = ttk.Label(footer, text=APP_CREDIT, anchor="e")
        credit.pack(side=tk.RIGHT)

    def add_field(self, parent: ttk.Frame, label: str, variable: tk.StringVar, row: int, column: int, show: str | None = None) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=(0, 4), pady=4)
        ttk.Entry(parent, textvariable=variable, show=show).grid(row=row, column=column + 1, sticky="ew", padx=(0, 8), pady=4)

    def browse_output(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.output_dir.get() or DEFAULT_OUTPUT_DIR)
        if folder:
            self.output_dir.set(folder)

    def collect_config(self) -> AppConfig:
        try:
            config = AppConfig(
                api_id=int(self.api_id.get().strip()),
                api_hash=self.api_hash.get().strip(),
                admin_user_id=int(self.admin_id.get().strip()),
                group_id=int(self.group_id.get().strip()),
                target_user_id=int(self.target_id.get().strip()),
                phone=self.phone.get().strip(),
                password=self.password.get(),
                output_dir=Path(self.output_dir.get().strip()).expanduser().resolve(),
                batch_size=max(1, int(self.batch_size.get().strip())),
                parallel_downloads=int(self.parallel_downloads.get().strip()),
                parallel_small=int(self.parallel_small.get().strip()),
                parallel_large=int(self.parallel_large.get().strip()),
                download_target_only=bool(self.download_target_only.get()),
            )
        except ValueError as exc:
            raise ValueError(
                "IDs, API ID, batch size, and stream settings must be numeric."
            ) from exc
        if not config.api_hash:
            raise ValueError("API hash is required.")
        if not 1 <= config.parallel_downloads <= 20:
            raise ValueError("Total streams must be between 1 and 20.")
        if not 1 <= config.parallel_small <= config.parallel_downloads:
            raise ValueError("Small streams must be between 1 and Total streams.")
        if not 1 <= config.parallel_large <= config.parallel_downloads:
            raise ValueError("Large streams must be between 1 and Total streams.")
        config.output_dir.mkdir(parents=True, exist_ok=True)
        return config

    def start_task(self, title: str, coro_factory) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("Task running", "A task is already running. Stop it or wait until it finishes.")
            return
        try:
            config = self.collect_config()
        except Exception as exc:
            messagebox.showerror("Invalid fields", str(exc))
            return
        self.stop_event.clear()
        self.status_text.set(f"Running: {title}")
        self.log_line(f"Starting: {title}")

        def emit(kind: str, payload: Any) -> None:
            self.events.put((kind, payload))

        def runner() -> None:
            try:
                asyncio.run(coro_factory(config, self.stop_event, emit))
                emit("status", f"Finished: {title}")
            except Exception as exc:
                emit("error", f"{type(exc).__name__}: {exc}")
                emit("status", f"Failed: {title}")

        self.worker = threading.Thread(target=runner, daemon=True)
        self.worker.start()

    def search_messages(self) -> None:
        self.start_task("Search target messages", search_target_messages)

    def archive_text(self) -> None:
        self.start_task("Archive text chat", archive_text_chat)

    def download_media(self) -> None:
        self.start_task("Download media", download_media)

    def selected_message_id(self) -> int | None:
        value = self.message_id.get().strip()
        if value:
            try:
                return int(value)
            except ValueError:
                messagebox.showerror("Invalid message ID", "Message ID must be numeric.")
                return None
        selection = self.tree.selection()
        if selection:
            values = self.tree.item(selection[0], "values")
            if values:
                self.message_id.set(str(values[0]))
                return int(values[0])
        messagebox.showwarning("No message selected", "Select a row or enter a message ID.")
        return None

    def preview_selected_message(self) -> None:
        message_id = self.selected_message_id()
        if message_id is None:
            return

        async def task(config: AppConfig, _stop_event: threading.Event, emit) -> None:
            await preview_message(config, message_id, emit)

        self.start_task(f"Preview message {message_id}", task)

    def delete_selected_message(self) -> None:
        message_id = self.selected_message_id()
        if message_id is None:
            return
        allow_any = bool(self.allow_any_sender.get())
        target_id = self.target_id.get().strip()
        warning = (
            f"Delete message {message_id} from the group?\n\n"
            f"The app will fetch it again and verify the sender matches target user {target_id}."
        )
        if allow_any:
            warning += "\n\nAllow any sender is ON."
        if not messagebox.askyesno("Confirm deletion", warning):
            return
        typed = simpledialog.askstring("Type DELETE", "Type DELETE to confirm permanent deletion:")
        if typed != "DELETE":
            self.log_line("Deletion cancelled.")
            return

        async def task(config: AppConfig, _stop_event: threading.Event, emit) -> None:
            await delete_message(config, message_id, allow_any, emit)

        self.start_task(f"Delete message {message_id}", task)

    def stop_current_task(self) -> None:
        self.stop_event.set()
        self.status_text.set("Stop requested. Waiting for current page/file to finish.")
        self.log_line("Stop requested.")

    def on_tree_select(self, _event: object) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        values = self.tree.item(selection[0], "values")
        if values:
            self.message_id.set(str(values[0]))

    def log_line(self, text: str) -> None:
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)

    def poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.log_line(str(payload))
                elif kind == "clear_rows":
                    for item in self.tree.get_children():
                        self.tree.delete(item)
                elif kind == "row":
                    row = payload
                    self.tree.insert(
                        "",
                        tk.END,
                        values=(
                            row.get("message_id", ""),
                            row.get("message_date", ""),
                            row.get("sender_label", ""),
                            row.get("media_type", ""),
                            row.get("reply_to_msg_id", ""),
                            row.get("text", ""),
                        ),
                    )
                elif kind == "status":
                    self.status_text.set(str(payload))
                    self.log_line(str(payload))
                elif kind == "error":
                    self.log_line("ERROR: " + str(payload))
                    messagebox.showerror("Task failed", str(payload))
                elif kind == "prompt":
                    response_queue = payload["response_queue"]
                    try:
                        answer = simpledialog.askstring(
                            payload["title"],
                            payload["message"],
                            initialvalue=payload.get("default", ""),
                            show="*" if payload.get("hidden") else None,
                            parent=self.root,
                        )
                        response_queue.put(answer)
                    except Exception:
                        response_queue.put(None)
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)


def main() -> None:
    if "--smoke-test" in sys.argv:
        print(f"Telegram Admin GUI {SCRIPT_VERSION} OK - {APP_CREDIT}")
        return

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except tk.TclError:
        pass
    TelegramAdminGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

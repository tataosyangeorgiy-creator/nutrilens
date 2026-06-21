import asyncio
import html
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest
from yt_dlp import YoutubeDL


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
logger = logging.getLogger(__name__)


TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "downloads"))
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "45"))
SEARCH_RESULTS = int(os.getenv("SEARCH_RESULTS", "5"))
TELEGRAM_TIMEOUT = float(os.getenv("TELEGRAM_TIMEOUT", "30"))
TELEGRAM_PROXY_URL = os.getenv("TELEGRAM_PROXY_URL")
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
SPOTIFY_RE = re.compile(r"https?://open\.spotify\.com/\S+", re.IGNORECASE)
YANDEX_RE = re.compile(r"https?://music\.yandex\.[a-z.]+/\S+", re.IGNORECASE)


@dataclass(frozen=True)
class Track:
    title: str
    webpage_url: str
    duration: int | None
    extractor: str


def _ydl(options: dict[str, Any] | None = None) -> YoutubeDL:
    base_options: dict[str, Any] = {
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "noplaylist": True,
        "default_search": "ytsearch",
        "source_address": "0.0.0.0",
    }
    if options:
        base_options.update(options)
    return YoutubeDL(base_options)


def _first_url(text: str) -> str | None:
    match = URL_RE.search(text)
    return match.group(0) if match else None


def _clean_title(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _fetch_page_title(url: str) -> str | None:
    try:
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=8) as response:
            body = response.read(256_000).decode("utf-8", errors="ignore")
    except Exception:
        return None

    title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
    if not title_match:
        return None

    title = _clean_title(title_match.group(1))
    title = re.sub(r"\s+[|—-]\s+(Spotify|Яндекс Музыка|Yandex Music).*$", "", title, flags=re.IGNORECASE)
    return title or None


def _spotify_oembed_title(url: str) -> str | None:
    try:
        request = Request(
            f"https://open.spotify.com/oembed?url={quote(url, safe='')}",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urlopen(request, timeout=8) as response:
            body = response.read(64_000).decode("utf-8", errors="ignore")
    except Exception:
        return None

    match = re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', body)
    if not match:
        return None
    return _clean_title(match.group(1).encode("utf-8").decode("unicode_escape"))


def _query_from_text(text: str) -> tuple[str, bool]:
    url = _first_url(text)
    if not url:
        return text.strip(), False

    if SPOTIFY_RE.search(url):
        return (_spotify_oembed_title(url) or _fetch_page_title(url) or text).strip(), False

    if YANDEX_RE.search(url):
        return (_fetch_page_title(url) or text).strip(), False

    return url, True


def _entry_to_track(entry: dict[str, Any]) -> Track | None:
    title = entry.get("title") or entry.get("fulltitle")
    url = entry.get("webpage_url") or entry.get("url")
    if not title or not url:
        return None
    if entry.get("is_live"):
        return None
    duration = entry.get("duration")
    if duration and duration > 20 * 60:
        return None
    return Track(
        title=str(title),
        webpage_url=str(url),
        duration=int(duration) if duration else None,
        extractor=str(entry.get("extractor_key") or entry.get("extractor") or "unknown"),
    )


def search_track(text: str) -> Track:
    query, direct_url = _query_from_text(text)
    if not query:
        raise ValueError("Пустой запрос.")

    searches = [query] if direct_url else [
        f"ytsearch{SEARCH_RESULTS}:{query} audio",
        f"scsearch{SEARCH_RESULTS}:{query}",
    ]

    last_error: Exception | None = None
    for search in searches:
        try:
            with _ydl({"extract_flat": False}) as ydl:
                info = ydl.extract_info(search, download=False)
        except Exception as exc:
            last_error = exc
            continue

        entries = info.get("entries") if isinstance(info, dict) else None
        candidates = entries or [info]
        for entry in candidates:
            if not entry:
                continue
            track = _entry_to_track(entry)
            if track:
                return track

    if last_error:
        raise RuntimeError(f"Не удалось найти трек: {last_error}") from last_error
    raise LookupError("Не нашёл подходящий аудио-результат.")


def download_track(track: Track, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    before = set(destination.iterdir())
    options = {
        "format": "bestaudio/best",
        "outtmpl": str(destination / "%(title).160B [%(id)s].%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            },
            {"key": "FFmpegMetadata"},
        ],
    }

    with _ydl(options) as ydl:
        ydl.download([track.webpage_url])

    after = set(destination.iterdir())
    files = sorted(after - before, key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        files = sorted(destination.glob("*.mp3"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError("Файл не был создан.")

    audio = files[0]
    if audio.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"Файл больше лимита {MAX_FILE_MB} МБ для отправки ботом.")
    return audio


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Пришли название песни или ссылку на YouTube/SoundCloud/Bandcamp. "
        "Ссылки Spotify/Yandex Music использую как источник названия и ищу доступный аудио-результат."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.text:
        return

    query = message.text.strip()
    if len(query) < 2:
        await message.reply_text("Напиши название трека подлиннее.")
        return

    await message.chat.send_action(ChatAction.TYPING)
    status = await message.reply_text("Ищу трек…")

    workdir = Path(tempfile.mkdtemp(prefix="track-", dir=DOWNLOAD_DIR))
    try:
        track = await asyncio.to_thread(search_track, query)
        await status.edit_text(f"Нашёл: {track.title}\nСкачиваю аудио…")
        await message.chat.send_action(ChatAction.UPLOAD_AUDIO)
        audio_path = await asyncio.to_thread(download_track, track, workdir)

        with audio_path.open("rb") as audio:
            await message.reply_audio(
                audio=audio,
                title=track.title[:64],
                caption=f"{track.title}\nИсточник: {track.webpage_url}",
            )
        await status.delete()
    except Exception as exc:
        logger.exception("Failed to process query %r", query)
        await status.edit_text(
            "Не получилось скачать трек. Попробуй другое название или ссылку на YouTube/SoundCloud.\n"
            f"Причина: {exc}"
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main() -> None:
    if not TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN environment variable.")

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    request = HTTPXRequest(
        connect_timeout=TELEGRAM_TIMEOUT,
        read_timeout=TELEGRAM_TIMEOUT,
        write_timeout=TELEGRAM_TIMEOUT,
        pool_timeout=TELEGRAM_TIMEOUT,
        media_write_timeout=TELEGRAM_TIMEOUT,
        proxy_url=TELEGRAM_PROXY_URL,
    )
    application = Application.builder().token(TOKEN).request(request).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

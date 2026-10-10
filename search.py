from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlparse

import yt_dlp

from player import Song

logger = logging.getLogger("MusicS3YD.search")


class MusicSearch:
    """YouTube and SoundCloud search/stream resolver using yt-dlp."""

    def __init__(self, cache_seconds: int = 300) -> None:
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, list[Song]]] = {}

    @staticmethod
    def _is_url(query: str) -> bool:
        parsed = urlparse(query.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def _search_source(prefix: str, query: str, limit: int) -> list[Song]:
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "noplaylist": True,
            "ignoreerrors": True,
            "default_search": "ytsearch",
        }
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(f"{prefix}{limit}:{query}", download=False)
        except Exception as exc:
            logger.info("Search failed for %s: %s", prefix, exc)
            return []

        entries = info.get("entries") if isinstance(info, dict) else None
        if not entries and isinstance(info, dict):
            entries = [info]

        results: list[Song] = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            title = entry.get("title")
            webpage = entry.get("webpage_url") or entry.get("original_url")
            raw_url = entry.get("url")
            if not webpage and raw_url and str(raw_url).startswith("http"):
                webpage = raw_url
            if not webpage and entry.get("id"):
                extractor = str(entry.get("ie_key") or entry.get("extractor_key") or "").lower()
                if "soundcloud" in extractor:
                    webpage = f"https://soundcloud.com/{entry.get('uploader_id', '')}/{entry['id']}"
                elif "youtube" in extractor:
                    webpage = f"https://www.youtube.com/watch?v={entry['id']}"
            if not title or not webpage:
                continue
            results.append(
                Song(
                    title=str(title),
                    url=str(webpage),
                    webpage=str(webpage),
                    duration=int(entry.get("duration") or 0),
                    thumbnail=entry.get("thumbnail"),
                    source=str(entry.get("extractor") or entry.get("ie_key") or ""),
                )
            )
        return results[:limit]

    @staticmethod
    def _resolve_sync(url: str) -> Song:
        options: dict[str, Any] = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
        }
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as first_error:
            # Some extractors expose a playable default format but reject the
            # preferred audio selector. Retry with yt-dlp's normal selection.
            fallback = dict(options)
            fallback.pop("format", None)
            try:
                with yt_dlp.YoutubeDL(fallback) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception:
                raise first_error

        if not isinstance(info, dict):
            raise ValueError("لم أستطع قراءة بيانات الرابط.")
        if info.get("entries"):
            info = next((entry for entry in info["entries"] if entry), None)
        if not isinstance(info, dict) or not info.get("url"):
            raise ValueError("لم أجد صيغة تشغيل صوتية لهذا الرابط.")
        webpage = info.get("webpage_url") or info.get("original_url") or url
        return Song(
            title=str(info.get("title") or "مقطع بدون عنوان"),
            url=str(webpage),
            webpage=str(webpage),
            duration=int(info.get("duration") or 0),
            thumbnail=info.get("thumbnail"),
            source=str(info.get("extractor") or ""),
        )

    async def resolve(self, url: str) -> Song:
        return await asyncio.to_thread(self._resolve_sync, url.strip())

    async def search(self, query: str, limit: int = 5) -> list[Song]:
        query = query.strip()
        if not query:
            return []
        if self._is_url(query):
            return [await self.resolve(query)]

        key = query.casefold()
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and now - cached[0] < self.cache_seconds:
            return cached[1][:limit]

        youtube_limit = min(limit, 3)
        soundcloud_limit = max(0, limit - youtube_limit)
        youtube_task = asyncio.to_thread(self._search_source, "ytsearch", query, youtube_limit)
        soundcloud_task = asyncio.to_thread(self._search_source, "scsearch", query, soundcloud_limit)
        youtube_results, soundcloud_results = await asyncio.gather(youtube_task, soundcloud_task)

        results = (youtube_results + soundcloud_results)[:limit]
        # Fill any missing slots from the source that returned more matches.
        if len(results) < limit:
            extras = await asyncio.to_thread(
                self._search_source, "ytsearch", query, limit
            )
            known = {song.webpage for song in results}
            results.extend(song for song in extras if song.webpage not in known)
            results = results[:limit]

        self._cache[key] = (now, results)
        return results


music_search = MusicSearch()

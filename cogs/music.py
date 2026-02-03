# cogs/music.py
# Discord.py 2.x music cog with:
# - Queue (enqueue, skip, clear, remove, shuffle, show queue)
# - Accurate play/pause timing
# - True interrupt + resume helpers (for TTS/speak)
# - Robust seek command (absolute/relative/percentage)
# - yt_dlp re-extraction to avoid expired stream URLs

from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord.ext import commands
from discord import FFmpegOpusAudio
import yt_dlp as youtube_dl


@dataclass
class Track:
    """Lightweight, queue-friendly metadata about a track."""
    query: str                 # original user input (url or search)
    webpage_url: str           # stable page URL for re-extraction
    title: str                 # nice title for display
    duration: Optional[float]  # seconds (None for unknown/live)
    is_live: bool
    extractor: Optional[str]
    id: Optional[str]


class Music(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client

        # Per-guild queue and playback state
        self._queues: Dict[int, List[Track]] = {}         # upcoming tracks
        self._locks: Dict[int, asyncio.Lock] = {}         # serialization lock per guild for play/advance
        self._pb: Dict[int, Dict[str, Any]] = {}          # current playback metadata (now playing)

    # ---------------------------
    # Internal helpers
    # ---------------------------
    def _ensure_connected(self, ctx: commands.Context) -> bool:
        return ctx.voice_client is not None and ctx.voice_client.is_connected()

    def _lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self._locks:
            self._locks[guild_id] = asyncio.Lock()
        return self._locks[guild_id]

    def _queue(self, guild_id: int) -> List[Track]:
        if guild_id not in self._queues:
            self._queues[guild_id] = []
        return self._queues[guild_id]

    def _is_resumable_protocol(self, protocol: Optional[str]) -> bool:
        if not protocol:
            return True
        p = str(protocol).lower()
        return not any(k in p for k in ("m3u8", "hls", "rtmp"))

    def _fmt_time(self, seconds: Optional[float]) -> str:
        if seconds is None:
            return "LIVE/Unknown"
        t = int(max(0, seconds))
        m, s = divmod(t, 60)
        h, m = divmod(m, 60)
        return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"

    # ---------------------------
    # yt_dlp extraction
    # ---------------------------
    def _ydl(self):
        return youtube_dl.YoutubeDL({
            "format": "bestaudio/best",
            "noplaylist": False,            # we’ll handle playlists by expanding
            "default_search": "ytsearch",   # allow search terms
            "quiet": True,
            "nocheckcertificate": True,
            "geo_bypass": True,
            "extract_flat": False,
        })

    def _extract_entries(self, info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Normalize yt_dlp output into a list of 'entry' dicts to enqueue."""
        if "entries" in info and isinstance(info["entries"], list):
            return [e for e in info["entries"] if e]
        return [info]

    def _entry_to_track(self, entry: Dict[str, Any], fallback_query: str) -> Track:
        return Track(
            query=fallback_query,
            webpage_url=entry.get("webpage_url") or entry.get("url") or fallback_query,
            title=entry.get("title") or "Unknown title",
            duration=entry.get("duration"),
            is_live=bool(entry.get("is_live")),
            extractor=entry.get("extractor_key"),
            id=entry.get("id"),
        )

    async def _enqueue_extracted(self, ctx: commands.Context, query: str) -> List[Track]:
        """Extract info for query/URL and return enqueued Track(s)."""
        added: List[Track] = []
        try:
            with self._ydl() as ydl:
                info = ydl.extract_info(query, download=False)
            entries = self._extract_entries(info)
            for i, e in enumerate(entries):
                t = self._entry_to_track(e, query)
                self._queue(ctx.guild.id).append(t)
                added.append(t)
        except youtube_dl.utils.DownloadError as e:
            await ctx.send(f"Failed to retrieve audio: `{e}`")
        return added

    async def _choose_stream_url(self, url_or_info: Dict[str, Any] | str) -> Tuple[str, Optional[str]]:
        """From a yt_dlp info dict or a URL string, choose an http(s) stream URL and protocol."""
        if isinstance(url_or_info, str):
            with self._ydl() as ydl:
                info = ydl.extract_info(url_or_info, download=False)
        else:
            info = url_or_info

        # If playlist-like, pick first entry
        if "entries" in info:
            for e in info["entries"]:
                if e:
                    info = e
                    break

        if "url" in info and str(info.get("protocol", "")).startswith(("http", "https")):
            return info["url"], info.get("protocol")

        fmts = info.get("formats", []) or []
        cands = [
            f for f in fmts
            if f.get("acodec") != "none" and str(f.get("protocol", "")).startswith(("http", "https"))
        ]
        if not cands:
            raise RuntimeError("No playable audio stream was found.")
        cands.sort(key=lambda f: f.get("abr", 0) or 0, reverse=True)
        return cands[0]["url"], cands[0].get("protocol")

    # ---------------------------
    # Public helpers (for other cogs / UI)
    # ---------------------------
    def build_resume_snapshot(self, ctx: commands.Context) -> Optional[Dict[str, Any]]:
        meta = self._pb.get(ctx.guild.id)
        if not meta or ctx.voice_client is None:
            return None

        started_at: Optional[float] = meta.get("started_at")
        if started_at is None:
            return None

        seek_base = float(meta.get("seek_base", 0.0))
        duration: Optional[float] = meta.get("duration")
        protocol: Optional[str] = meta.get("protocol")
        is_live = bool(meta.get("is_live"))
        paused_at: Optional[float] = meta.get("paused_at")

        if paused_at is not None:
            elapsed = max(0.0, paused_at - started_at)
        else:
            elapsed = max(0.0, time.monotonic() - started_at)

        offset = seek_base + elapsed
        if duration is not None:
            offset = max(0.0, min(offset, float(duration) - 0.25))

        resumable = (not is_live) and self._is_resumable_protocol(protocol)
        return {
            "webpage_url": meta.get("webpage_url"),
            "title": meta.get("title"),
            "duration": duration,
            "offset": offset,
            "resumable": resumable,
            "extractor": meta.get("extractor"),
            "id": meta.get("id"),
        }

    async def resume_from_snapshot(self, ctx: commands.Context, snapshot: Dict[str, Any]):
        if ctx.voice_client is None or not ctx.voice_client.is_connected():
            if ctx.author.voice and ctx.author.voice.channel:
                await ctx.author.voice.channel.connect()
            else:
                await ctx.send("Cannot resume: not connected to a voice channel.")
                return

        url = snapshot.get("webpage_url")
        if not url:
            await ctx.send("Nothing to resume.")
            return

        offset = float(snapshot.get("offset", 0.0))
        try:
            with self._ydl() as ydl:
                info = ydl.extract_info(url, download=False)
            title = info.get("title") or "Unknown title"
            duration = info.get("duration")
            is_live = bool(info.get("is_live"))
            stream_url, protocol = await self._choose_stream_url(info)

            if not ((not is_live) and self._is_resumable_protocol(protocol)):
                await ctx.send("⚠️ Source is not seekable; restarting from the beginning.")
                offset = 0.0

            ffm_opts = {
                "before_options": f"-ss {offset:.3f} -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                "options": "-vn"
            }
            source = await FFmpegOpusAudio.from_probe(stream_url, **ffm_opts)

            def after_playing(err):
                if err:
                    print(f"Playback error (resumed): {err}")
                # Auto-advance handled by queue (if any) via after on normal tracks; snapshot resumes are explicit.

            ctx.voice_client.play(source, after=after_playing)

            self._pb[ctx.guild.id] = {
                "webpage_url": info.get("webpage_url") or url,
                "title": title,
                "duration": duration,
                "started_at": time.monotonic(),
                "seek_base": float(offset),
                "paused_at": None,
                "is_live": is_live,
                "protocol": protocol,
                "extractor": info.get("extractor_key"),
                "id": info.get("id"),
                "channel_id": getattr(ctx.channel, "id", None),
            }

            await ctx.send(f"⏮️ Resumed **{title}** at {int(offset)}s.")
        except youtube_dl.utils.DownloadError as e:
            await ctx.send(f"Failed to resume (yt-dlp): `{e}`")
        except discord.ClientException as e:
            await ctx.send(f"Voice client error during resume: `{e}`")
        except Exception as e:
            await ctx.send(f"Unexpected error during resume: `{e}`")

    def get_queue_copy(self, guild_id: int) -> List[Track]:
        return list(self._queue(guild_id))

    # ---------------------------
    # Core playback engine (queue-aware)
    # ---------------------------
    async def _play_track(self, ctx: commands.Context, track: Track):
        """Play a specific track immediately (replacing current playback)."""
        with self._ydl() as ydl:
            info = ydl.extract_info(track.webpage_url, download=False)

        stream_url, protocol = await self._choose_stream_url(info)
        ffm_opts = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn"
        }
        source = await FFmpegOpusAudio.from_probe(stream_url, **ffm_opts)

        def after_playing(err, guild_id=ctx.guild.id):
            # Advance queue on end/skip; schedule in bot loop
            fut = asyncio.run_coroutine_threadsafe(self._advance_queue(guild_id), self.client.loop)
            try:
                fut.result()
            except Exception as ex:
                print(f"[after_playing] advance_queue error: {ex}")
            if err:
                print(f"Playback error: {err}")

        ctx.voice_client.play(source, after=after_playing)

        title = info.get("title") or track.title or "Unknown title"
        duration = info.get("duration", track.duration)
        is_live = bool(info.get("is_live", track.is_live))

        # Update "now playing" metadata for resume/seek/UI
        self._pb[ctx.guild.id] = {
            "webpage_url": info.get("webpage_url") or track.webpage_url,
            "title": title,
            "duration": duration,
            "started_at": time.monotonic(),
            "seek_base": 0.0,
            "paused_at": None,
            "is_live": is_live,
            "protocol": protocol,
            "extractor": info.get("extractor_key"),
            "id": info.get("id"),
            "channel_id": getattr(ctx.channel, "id", None),
        }

        await ctx.send(f"▶️ Now playing: **{title}**")

    async def _advance_queue(self, guild_id: int):
        """Auto-advance to next item when current track ends."""
        lock = self._lock(guild_id)
        if lock.locked():
            # Prevent re-entrancy if after callback fires multiple times
            return
        async with lock:
            guild = self.client.get_guild(guild_id)
            if guild is None:
                return
            vc = guild.voice_client
            if vc is None or not vc.is_connected():
                return

            # If something is playing or paused, do nothing
            if vc.is_playing() or vc.is_paused():
                return

            q = self._queue(guild_id)
            if not q:
                return  # nothing to play

            # Pop next track and play
            next_track = q.pop(0)
            # Build a fake ctx-like object for sending "Now playing" to the last channel
            meta = self._pb.get(guild_id, {})
            channel_id = meta.get("channel_id")
            channel = self.client.get_channel(channel_id) if channel_id else None

            class _CtxLike:
                def __init__(self, bot, guild, channel):
                    self.bot = bot
                    self.guild = guild
                    self.channel = channel if channel else guild.text_channels[0]
                    self.voice_client = guild.voice_client
                    self.author = guild.me  # not used by play code

                async def send(self, *args, **kwargs):
                    return await self.channel.send(*args, **kwargs)

            ctx_like = _CtxLike(self.client, guild, channel)
            await self._play_track(ctx_like, next_track)

    # ---------------------------
    # Commands: join/leave/play/skip/queue
    # ---------------------------
    @commands.command()
    async def join(self, ctx: commands.Context):
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.send("You're not in a voice channel. Join one first, then try again.")
            return

        chan = ctx.author.voice.channel
        vc = ctx.voice_client
        if vc is None:
            await chan.connect()
            await ctx.send(f"Joined **{chan.name}** ✅")
        else:
            if vc.channel != chan:
                await vc.move_to(chan)
                await ctx.send(f"Moved to **{chan.name}** 🔁")
            else:
                await ctx.send("I'm already in your voice channel 🙂")

    @commands.command()
    async def leave(self, ctx: commands.Context):
        if self._ensure_connected(ctx):
            name = ctx.voice_client.channel.name
            await ctx.voice_client.disconnect()
            await ctx.send(f"Left **{name}** 👋")
            # Clear queue and now-playing when leaving
            self._queues.pop(ctx.guild.id, None)
            self._pb.pop(ctx.guild.id, None)
        else:
            await ctx.send("I'm not connected to any voice channel.")

    @commands.command()
    async def play(self, ctx: commands.Context, *, query: str):
        """
        Enqueue a URL/search. If idle, play immediately; otherwise queue it.
        Playlists will enqueue multiple items.
        """
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.send("Join a voice channel first, then use `?play <url or search>`.")
            return

        if not self._ensure_connected(ctx):
            await ctx.author.voice.channel.connect()

        added = await self._enqueue_extracted(ctx, query)
        if not added:
            return

        # If nothing is playing/paused, start immediately with the first new track
        vc = ctx.voice_client
        if not (vc.is_playing() or vc.is_paused()):
            # pop first item and start it
            first = self._queue(ctx.guild.id).pop(0)
            async with self._lock(ctx.guild.id):
                await self._play_track(ctx, first)
            # if more items were added, announce count
            if len(added) > 1:
                await ctx.send(f"➕ Enqueued {len(added) - 1} more from playlist.")
        else:
            # Already playing → everything was enqueued
            if len(added) == 1:
                await ctx.send(f"➕ Queued: **{added[0].title}**")
            else:
                await ctx.send(f"➕ Enqueued {len(added)} tracks.")

    @commands.command(aliases=["q"])
    async def queue(self, ctx: commands.Context):
        """Show the current queue (upcoming tracks)."""
        q = self.get_queue_copy(ctx.guild.id)
        if not q:
            await ctx.send("📭 Queue is empty.")
            return

        lines = []
        max_show = 10
        for i, t in enumerate(q[:max_show], start=1):
            dur = self._fmt_time(t.duration)
            lines.append(f"`{i:>2}.` **{t.title}** — `{dur}`")

        more = len(q) - max_show
        desc = "\n".join(lines)
        if more > 0:
            desc += f"\n… and `{more}` more."

        embed = discord.Embed(title="Upcoming Queue", description=desc, color=discord.Color.blurple())
        await ctx.send(embed=embed)

    @commands.command()
    async def skip(self, ctx: commands.Context):
        """Skip the current track and play the next in queue, if any."""
        if not self._ensure_connected(ctx):
            await ctx.send("I'm not connected to a voice channel.")
            return
        vc = ctx.voice_client
        if vc.is_playing() or vc.is_paused():
            vc.stop()  # after callback will advance the queue
            await ctx.send("⏭️ Skipped.")
        else:
            await ctx.send("Nothing is playing right now.")

    @commands.command()
    async def clear(self, ctx: commands.Context):
        """Clear the entire queue (does not stop current track)."""
        q = self._queue(ctx.guild.id)
        n = len(q)
        q.clear()
        await ctx.send(f"🗑️ Cleared {n} queued item(s).")

    @commands.command()
    async def remove(self, ctx: commands.Context, index: int):
        """Remove a track at 1-based index from the upcoming queue."""
        q = self._queue(ctx.guild.id)
        if 1 <= index <= len(q):
            t = q.pop(index - 1)
            await ctx.send(f"❌ Removed: **{t.title}**")
        else:
            await ctx.send("Index out of range.")

    @commands.command()
    async def shuffle(self, ctx: commands.Context):
        """Shuffle the upcoming queue."""
        q = self._queue(ctx.guild.id)
        if len(q) < 2:
            await ctx.send("Not enough items to shuffle.")
            return
        random.shuffle(q)
        await ctx.send("🔀 Shuffled the queue.")

    # ---------------------------
    # Pause/Resume/Stop
    # ---------------------------
    @commands.command()
    async def pause(self, ctx: commands.Context):
        """Pause the current audio and record pause time."""
        if not self._ensure_connected(ctx):
            await ctx.send("I'm not connected to a voice channel.")
            return
        vc = ctx.voice_client
        if vc.is_playing():
            vc.pause()
            meta = self._pb.get(ctx.guild.id)
            if meta is not None:
                meta["paused_at"] = time.monotonic()
            await ctx.send("⏸️ Paused.")
        else:
            await ctx.send("Nothing is playing right now.")

    @commands.command()
    async def resume(self, ctx: commands.Context):
        """Resume paused audio and fix timing anchors."""
        if not self._ensure_connected(ctx):
            await ctx.send("I'm not connected to a voice channel.")
            return
        vc = ctx.voice_client
        if vc.is_paused():
            meta = self._pb.get(ctx.guild.id)
            now = time.monotonic()
            if meta and meta.get("paused_at") is not None and meta.get("started_at") is not None:
                paused_at = float(meta["paused_at"])
                started_at = float(meta["started_at"])
                already = max(0.0, paused_at - started_at)
                meta["seek_base"] = float(meta.get("seek_base", 0.0)) + already
                meta["started_at"] = now
                meta["paused_at"] = None
            else:
                if meta:
                    meta["started_at"] = now
                    meta["paused_at"] = None
            vc.resume()
            await ctx.send("▶️ Resumed.")
        else:
            await ctx.send("Audio is not paused.")

    @commands.command()
    async def stop(self, ctx: commands.Context):
        """Stop playback and clear current source (queue remains)."""
        if not self._ensure_connected(ctx):
            await ctx.send("I'm not connected to a voice channel.")
            return
        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            ctx.voice_client.stop()
            await ctx.send("⏹️ Stopped.")
        else:
            await ctx.send("Nothing to stop.")

    # ---------------------------
    # SEEK Command
    # ---------------------------
    def _parse_time_to_seconds(self, text: str) -> Optional[float]:
        """
        Parse time strings into seconds.
        Supports:
          - "75"       (seconds)
          - "1:15"     (mm:ss)
          - "0:01:15"  (hh:mm:ss)
        """
        text = text.strip()
        if re.fullmatch(r"\d+", text):
            return float(int(text))
        if re.fullmatch(r"\d+:\d{1,2}", text) or re.fullmatch(r"\d+:\d{2}:\d{2}", text):
            parts = [int(p) for p in text.split(":")]
            if len(parts) == 2:
                m, s = parts
                return float(m * 60 + s)
            if len(parts) == 3:
                h, m, s = parts
                return float(h * 3600 + m * 60 + s)
        return None

    @commands.command(name="seek", aliases=["scrub", "jump"])
    async def seek(self, ctx: commands.Context, *, position: str):
        """
        Seek to a new position in the current track.

        Examples:
          ?seek +10       (forward 10s)
          ?seek -15       (back 15s)
          ?seek 90        (absolute 90s)
          ?seek 1:23      (absolute 1m23s)
          ?seek 50%       (50% of the track; requires known duration)

        Notes:
          - Works while playing or paused.
          - Live/HLS streams are not seekable.
        """
        vc = ctx.voice_client
        meta = self._pb.get(ctx.guild.id)
        if vc is None or not vc.is_connected() or not meta:
            await ctx.send("Nothing to seek—no track is loaded.")
            return

        snapshot = self.build_resume_snapshot(ctx)
        if not snapshot:
            await ctx.send("Cannot capture current playback position.")
            return
        if not snapshot.get("resumable", False):
            await ctx.send("⚠️ Current source is not seekable (live/HLS).")
            return

        cur_offset = float(snapshot.get("offset", 0.0))
        duration = snapshot.get("duration")
        arg = position.strip().replace(" ", "")
        new_offset: Optional[float] = None

        try:
            if arg.endswith("%"):
                if duration is None:
                    await ctx.send("Cannot use % seek—track duration is unknown.")
                    return
                pct = float(arg[:-1])
                if not (0.0 <= pct <= 100.0):
                    await ctx.send("Percentage must be between 0 and 100.")
                    return
                new_offset = float(duration) * (pct / 100.0)

            elif arg.startswith(("+", "-")):
                rel_str = arg[1:]
                secs = self._parse_time_to_seconds(rel_str)
                if secs is None:
                    await ctx.send("Invalid time. Use seconds, mm:ss, or hh:mm:ss (e.g., +10, -1:30).")
                    return
                new_offset = cur_offset + secs if arg.startswith("+") else cur_offset - secs

            else:
                secs = self._parse_time_to_seconds(arg)
                if secs is None:
                    await ctx.send("Invalid time. Use seconds, mm:ss, or hh:mm:ss (e.g., 90, 1:30).")
                    return
                new_offset = secs

        except ValueError:
            await ctx.send("Invalid seek value.")
            return

        # Bounds
        if duration is not None:
            new_offset = max(0.0, min(float(duration) - 0.25, float(new_offset)))
        else:
            new_offset = max(0.0, float(new_offset))

        if abs(new_offset - cur_offset) < 0.25:
            await ctx.send(f"⏩ Already near {int(new_offset)}s.")
            return

        try:
            if vc.is_playing() or vc.is_paused():
                vc.stop()
            snapshot["offset"] = new_offset
            await self.resume_from_snapshot(ctx, snapshot)
        except Exception as e:
            await ctx.send(f"Seek failed: `{e}`")

    @commands.command()
    async def re(self, ctx: commands.Context):
        await ctx.send("If you see this, Bonzi Buddy is working")


async def setup(client: commands.Bot):
    await client.add_cog(Music(client))
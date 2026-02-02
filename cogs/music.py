# cogs/music.py
import time
from typing import Any, Dict, Optional

import discord
from discord.ext import commands
from discord import FFmpegOpusAudio
import yt_dlp as youtube_dl


class Music(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client
        # per-guild playback metadata
        self._pb: Dict[int, Dict[str, Any]] = {}

    # --- helpers ---
    def _ensure_connected(self, ctx: commands.Context) -> bool:
        return ctx.voice_client is not None and ctx.voice_client.is_connected()

    def _is_resumable_protocol(self, protocol: Optional[str]) -> bool:
        if not protocol:
            return True
        p = str(protocol).lower()
        # Treat HLS/m3u8 and RTMP as non-seekable for our resume flow
        return not any(k in p for k in ("m3u8", "hls", "rtmp"))

    def build_resume_snapshot(self, ctx: commands.Context) -> Optional[Dict[str, Any]]:
        """Compute current offset for the track and return a snapshot for resume."""
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
        """Re-extract stream URL and resume playback from snapshot['offset']."""
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

        YDL_OPTIONS = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "nocheckcertificate": True,
            "geo_bypass": True,
            "extract_flat": False,
        }

        try:
            with youtube_dl.YoutubeDL(YDL_OPTIONS) as ydl:
                info = ydl.extract_info(url, download=False)

            if "entries" in info:
                for entry in info["entries"]:
                    if entry:
                        info = entry
                        break

            title = info.get("title") or "Unknown title"
            duration = info.get("duration")
            is_live = bool(info.get("is_live"))

            # choose stream
            protocol = None
            if "url" in info and str(info.get("protocol", "")).startswith(("http", "https")):
                stream_url = info["url"]
                protocol = info.get("protocol")
            else:
                fmts = info.get("formats", []) or []
                cands = [f for f in fmts if f.get("acodec") != "none" and str(f.get("protocol", "")).startswith(("http", "https"))]
                if not cands:
                    await ctx.send("Failed to resume: no playable stream.")
                    return
                cands.sort(key=lambda f: f.get("abr", 0) or 0, reverse=True)
                stream_url = cands[0].get("url")
                protocol = cands[0].get("protocol")

            resumable_now = (not is_live) and self._is_resumable_protocol(protocol)
            if not resumable_now:
                await ctx.send("⚠️ Source is not seekable; restarting from beginning.")
                offset = 0.0

            FFMPEG_OPTIONS = {
                "before_options": f"-ss {offset:.3f} -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                "options": "-vn"
            }
            source = await FFmpegOpusAudio.from_probe(stream_url, **FFMPEG_OPTIONS)

            def after_playing(err):
                if err:
                    print(f"Playback error (resumed): {err}")

            ctx.voice_client.play(source, after=after_playing)

            # update playback meta
            self._pb[ctx.guild.id] = {
                "webpage_url": info.get("webpage_url") or url,
                "title": title,
                "duration": duration,
                "started_at": time.monotonic(),
                "seek_base": float(offset),
                "is_live": is_live,
                "protocol": protocol,
                "extractor": info.get("extractor_key"),
                "id": info.get("id"),
            }

            await ctx.send(f"⏮️ Resumed **{title}** at {int(offset)}s.")
        except youtube_dl.utils.DownloadError as e:
            await ctx.send(f"Failed to resume (yt-dlp): `{e}`")
        except discord.ClientException as e:
            await ctx.send(f"Voice client error during resume: `{e}`")
        except Exception as e:
            await ctx.send(f"Unexpected error during resume: `{e}`")

    # --- commands ---
    @commands.command()
    async def join(self, ctx: commands.Context):
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.send("You're not in a voice channel. Join one first, then try again.")
            return
        vc = ctx.voice_client
        chan = ctx.author.voice.channel
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
        else:
            await ctx.send("I'm not connected to any voice channel.")

    @commands.command()
    async def play(self, ctx: commands.Context, *, url: str):
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.send("Join a voice channel first, then use `?play <url>`.")
            return
        if not self._ensure_connected(ctx):
            await ctx.author.voice.channel.connect()

        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            ctx.voice_client.stop()

        FFMPEG_OPTIONS = {"before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5", "options": "-vn"}
        YDL_OPTIONS = {
            "format": "bestaudio/best",
            "noplaylist": False,
            "default_search": "ytsearch",
            "quiet": True,
            "nocheckcertificate": True,
            "geo_bypass": True,
            "extract_flat": False,
        }

        try:
            with youtube_dl.YoutubeDL(YDL_OPTIONS) as ydl:
                info = ydl.extract_info(url, download=False)
            if "entries" in info:
                for entry in info["entries"]:
                    if entry:
                        info = entry
                        break

            protocol = None
            if "url" in info and str(info.get("protocol", "")).startswith(("http", "https")):
                stream_url = info["url"]
                protocol = info.get("protocol")
            else:
                fmts = info.get("formats", []) or []
                audio = [f for f in fmts if f.get("acodec") != "none" and str(f.get("protocol", "")).startswith(("http", "https"))]
                if not audio:
                    await ctx.send("Couldn't find an audio stream for that URL. Try another link.")
                    return
                audio.sort(key=lambda f: f.get("abr", 0) or 0, reverse=True)
                stream_url = audio[0].get("url")
                protocol = audio[0].get("protocol")

            source = await FFmpegOpusAudio.from_probe(stream_url, **FFMPEG_OPTIONS)
            ctx.voice_client.play(source, after=lambda e: e and print(f"Playback error: {e}"))

            title = info.get("title") or "Unknown title"
            webpage_url = info.get("webpage_url") or url
            is_live = bool(info.get("is_live"))
            duration = info.get("duration")

            self._pb[ctx.guild.id] = {
                "webpage_url": webpage_url,
                "title": title,
                "duration": duration,
                "started_at": time.monotonic(),
                "seek_base": 0.0,
                "is_live": is_live,
                "protocol": protocol,
                "extractor": info.get("extractor_key"),
                "id": info.get("id"),
            }

            await ctx.send(f"▶️ Now playing: **{title}**")
        except youtube_dl.utils.DownloadError as e:
            await ctx.send(f"Failed to retrieve audio: `{e}`")
        except discord.ClientException as e:
            await ctx.send(f"Voice client error: `{e}`")
        except Exception as e:
            await ctx.send(f"Unexpected error: `{e}`")

    @commands.command()
    async def pause(self, ctx: commands.Context):
        if not self._ensure_connected(ctx):
            await ctx.send("I'm not connected to a voice channel.")
            return
        if ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await ctx.send("⏸️ Paused.")
        else:
            await ctx.send("Nothing is playing right now.")

    @commands.command()
    async def resume(self, ctx: commands.Context):
        if not self._ensure_connected(ctx):
            await ctx.send("I'm not connected to a voice channel.")
            return
        if ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await ctx.send("▶️ Resumed.")
        else:
            await ctx.send("Audio is not paused.")

    @commands.command()
    async def stop(self, ctx: commands.Context):
        if not self._ensure_connected(ctx):
            await ctx.send("I'm not connected to a voice channel.")
            return
        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            ctx.voice_client.stop()
            await ctx.send("⏹️ Stopped.")
        else:
            await ctx.send("Nothing to stop.")

    @commands.command()
    async def re(self, ctx: commands.Context):
        await ctx.send("If you see this, Bonzi Buddy is working")


async def setup(client: commands.Bot):
    await client.add_cog(Music(client))
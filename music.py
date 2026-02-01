# cogs/music.py
import discord
from discord.ext import commands
from discord import FFmpegOpusAudio

# Use yt_dlp instead of youtube_dl (active fork)
import yt_dlp as youtube_dl

class Music(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client

    # --- Helpers ---
    def _ensure_connected(self, ctx: commands.Context) -> bool:
        """Return True if bot is connected to a voice channel in this guild."""
        return ctx.voice_client is not None and ctx.voice_client.is_connected()

    # --- Commands ---
    @commands.command()
    async def join(self, ctx: commands.Context):
        """Join the voice channel the author is in (or move to it)."""
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.send("You're not in a voice channel. Join one first, then try again.")
            return

        voice_channel = ctx.author.voice.channel
        if ctx.voice_client is None:
            await voice_channel.connect()
            await ctx.send(f"Joined **{voice_channel.name}** ✅")
        else:
            # Already connected somewhere; move to author's channel
            if ctx.voice_client.channel != voice_channel:
                await ctx.voice_client.move_to(voice_channel)
                await ctx.send(f"Moved to **{voice_channel.name}** 🔁")
            else:
                await ctx.send("I'm already in your voice channel 🙂")

    @commands.command()
    async def leave(self, ctx: commands.Context):
        """Disconnect from voice channel."""
        if self._ensure_connected(ctx):
            channel_name = ctx.voice_client.channel.name
            await ctx.voice_client.disconnect()
            await ctx.send(f"Left **{channel_name}** 👋")
        else:
            await ctx.send("I'm not connected to any voice channel.")

    @commands.command()
    async def play(self, ctx: commands.Context, *, url: str):
        """
        Play audio from a URL (YouTube supported). If something is playing, replace it.
        Usage: !play <url or search terms>
        """
        # Ensure user is in a VC and bot is connected
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.send("Join a voice channel first, then use `!play <url>`.")
            return

        # Connect if needed
        if not self._ensure_connected(ctx):
            await ctx.author.voice.channel.connect()

        # Stop current audio if any
        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            ctx.voice_client.stop()

        FFMPEG_OPTIONS = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn"
        }

        # yt_dlp config: bestaudio, no download, handle playlists by picking the first entry
        YDL_OPTIONS = {
            "format": "bestaudio/best",
            "noplaylist": False,     # If a playlist URL is passed, we’ll play the first entry
            "default_search": "ytsearch",  # allow search terms (not just URLs)
            "quiet": True,
            "nocheckcertificate": True,
            "geo_bypass": True,
            "extract_flat": False,
        }

        try:
            with youtube_dl.YoutubeDL(YDL_OPTIONS) as ydl:
                info = ydl.extract_info(url, download=False)

            # Handle playlists/search results vs single videos
            if "entries" in info:
                # Use the first valid entry
                for entry in info["entries"]:
                    if entry:
                        info = entry
                        break

            # Find a suitable audio stream URL
            # Prefer audio-only formats with an HTTP(S) URL
            stream_url = None
            if "url" in info and info.get("protocol", "").startswith(("http", "https")):
                stream_url = info["url"]
            else:
                formats = info.get("formats", []) or []
                # Sort to prefer audio-only formats with codecs present
                # Fallback: first HTTP(S) playable format
                audio_candidates = [
                    f for f in formats
                    if f.get("acodec") != "none" and str(f.get("protocol", "")).startswith(("http", "https"))
                ]
                if audio_candidates:
                    # Pick highest abr if available
                    audio_candidates.sort(key=lambda f: f.get("abr", 0), reverse=True)
                    stream_url = audio_candidates[0].get("url")

            if not stream_url:
                await ctx.send("Couldn't find an audio stream for that URL. Try another link.")
                return

            # Create source via FFmpeg’s probe helper (async)
            source = await FFmpegOpusAudio.from_probe(stream_url, **FFMPEG_OPTIONS)

            def after_playing(err):
                if err:
                    # You can log this to your logger instead of using send in a callback
                    # asyncio.run_coroutine_threadsafe(ctx.send(f"Playback error: {err}"), self.client.loop)
                    print(f"Playback error: {err}")

            ctx.voice_client.play(source, after=after_playing)
            title = info.get("title") or "Unknown title"
            webpage_url = info.get("webpage_url") or url
            await ctx.send(f"▶️ Now playing: **{title}**\n{webpage_url}")

        except youtube_dl.utils.DownloadError as e:
            await ctx.send(f"Failed to retrieve audio: `{e}`")
        except discord.ClientException as e:
            await ctx.send(f"Voice client error: `{e}`")
        except Exception as e:
            await ctx.send(f"Unexpected error: `{e}`")

    @commands.command()
    async def pause(self, ctx: commands.Context):
        """Pause the current audio."""
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
        """Resume paused audio."""
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
        """Stop playback and clear the current source."""
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
        """Generic Testing Command"""
        await ctx.send("If you see this, Bonzi Buddy is working")


async def setup(client: commands.Bot):
    await client.add_cog(Music(client))
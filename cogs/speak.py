# cogs/speak.py
import asyncio
import urllib.parse

import discord
from discord.ext import commands


class Speak(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client

    def _ensure_connected(self, ctx: commands.Context) -> bool:
        return ctx.voice_client is not None and ctx.voice_client.is_connected()

    @commands.command(name="say")
    async def say(self, ctx: commands.Context, *, text: str):
        """
        Interrupt current music, speak TTS, then resume at the correct timestamp.
        Will refuse to interrupt non-seekable sources (live/HLS).
        """
        # URL-encode the text for the TTS service
        qs_text = urllib.parse.quote_plus(text)
        tts_url = (
            "https://tetyys.com/SAPI4/SAPI4?"
            f"text={qs_text}"
            "&voice=Adult%20Male%20%232,%20American%20English%20(TruVoice)"
            "&pitch=140&speed=157"
        )

        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.send("Join a voice channel first, then try again.")
            return

        if not self._ensure_connected(ctx):
            await ctx.author.voice.channel.connect()

        vc = ctx.voice_client

        music_cog = ctx.bot.get_cog("Music")
        if music_cog is None:
            await ctx.send("Music cog not available; cannot resume after speaking.")
            return

        snapshot = None
        if vc.is_playing():
            snapshot = music_cog.build_resume_snapshot(ctx)
            if not snapshot:
                await ctx.send("Could not capture current track info; speaking without resume.")
            elif not snapshot.get("resumable", False):
                await ctx.send("⚠️ Current source is not seekable (live/HLS). Will not interrupt.")
                return
            vc.stop()

        FFMPEG_OPTIONS = {"before_options": "", "options": "-vn"}
        try:
            source = await discord.FFmpegOpusAudio.from_probe(tts_url, **FFMPEG_OPTIONS)
        except Exception as e:
            await ctx.send(f"Could not generate speech: `{e}`")
            if snapshot:
                await music_cog.resume_from_snapshot(ctx, snapshot)
            return

        done_evt = asyncio.Event()

        def after_tts(err: Exception | None):
            if err:
                asyncio.run_coroutine_threadsafe(
                    ctx.send(f"TTS error: `{err}`"),
                    self.client.loop
                )
            if snapshot:
                asyncio.run_coroutine_threadsafe(
                    music_cog.resume_from_snapshot(ctx, snapshot),
                    self.client.loop
                )
            self.client.loop.call_soon_threadsafe(done_evt.set)

        vc.play(source, after=after_tts)
        await ctx.send("🗣️ Speaking…")
        await done_evt.wait()


async def setup(client: commands.Bot):
    await client.add_cog(Speak(client))
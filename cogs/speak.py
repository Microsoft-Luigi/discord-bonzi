# cogs/speak.py
import asyncio
import urllib.parse
import random

import discord
from discord.ext import commands

facts_list = ['The situation you are in is very dangerous. The likelihood of you dying and not surviving within the next 5 minutes is 87.61%',
            'You could stand to lose a few pounds',
            'Cellular phones will not give you cancer... only hepatitis.',
            'Pants were invented by Sanders in the 16th century to avoid poseidon\'s wrath.',
            'The atomic weight of germanium 72.64',
            'The schrodinger\'s cat paradox outlines a situation in which a cat in a box must be considered, for all intensive purposes, simultaneously alive and dead. Schrodinger created this paradox as a justification for killing cats.',
            'Abraham Lincoln signed the emancipation proclamation. Freeing the slaves. Like everything he did lincoln freed the slaves while sleepwalking and later had no memory of the event.', 
            'In 1948, at the request of a dying boy, baseball legend Babe Ruth ate 75 hot dogs then died of hot dog poisoning.', 
            'William Shakespeare did not exist. His plays were masterminded in 1589 by Fracis Bacon who used a Ouija board to enslave playwriting ghosts.', 
            'Haley\'s Comet can be viewed over Earth every 76 years. For the other 75 it retreats to the heart of the sun where it hibernates undisturbed.', 
            'In greek myth, prometheus stole fire from the gods and gave it to humankind. The jewelry, he kept for himself.', 
            'The first person to prove that cow\'s milk is drinkable was very, very, thirsty.',
            'According to the most advanced algorithms, the world\'s best name is Craig.',
            'To make a photocopier, simply photocopy a mirror'
            'Dreams are the subconcious mind\'s way of remind people to go to school naked and have their teeth fall out.'
            ]

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
        await ctx.send(text)
        await done_evt.wait()

    @commands.command(name="fact")
    async def fact(self, ctx: commands.Context):
        random_str = random.choice(facts_list)
        await ctx.send(random_str)

async def setup(client: commands.Bot):
    await client.add_cog(Speak(client))
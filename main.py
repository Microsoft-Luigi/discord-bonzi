# main.py
import os
import asyncio
import logging
from dotenv import load_dotenv

import discord
from discord.ext import commands

load_dotenv()
TOKEN = os.getenv("TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s: %(message)s"
)
log = logging.getLogger("bot")

intents = discord.Intents.default()
intents.message_content = True  # required for prefix commands

bot = commands.Bot(command_prefix="?", intents=intents)

@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Game(name="type ?help"))

@bot.command(name="debugcogs")
async def debug_cogs(ctx: commands.Context):
    loaded = ", ".join(sorted(bot.cogs.keys()))
    await ctx.send(f"Loaded cogs: {loaded or '(none)'}")

async def main():
    if not TOKEN:
        raise SystemExit("Missing TOKEN env var (.env)")

    async with bot:
        # IMPORTANT: load Music first (Speak depends on it for resume)
        extensions = ["cogs.music", "cogs.speak"]
        for ext in extensions:
            try:
                await bot.load_extension(ext)
                log.info(f"Loaded extension: {ext}")
            except Exception as e:
                log.exception(f"Failed to load extension {ext}", exc_info=e)
                raise

        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
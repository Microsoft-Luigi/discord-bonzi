# main.py
import os
import asyncio
import logging
from dotenv import load_dotenv

import discord
from discord.ext import commands

# ---------- Env & Logging ----------
load_dotenv()  # reads .env
TOKEN = os.getenv("TOKEN")

# Basic logging (helpful for FFmpeg/voice diagnostics too)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s: %(message)s"
)
logger = logging.getLogger("bot")

# ---------- Intents ----------
intents = discord.Intents.default()
# Needed for prefix commands to read user messages
intents.message_content = True
# Enable other intents as needed (you were using Intents.all())
# If you truly need all, uncomment the next line:
# intents = discord.Intents.all()

# ---------- Bot ----------
bot = commands.Bot(command_prefix="?", intents=intents)

# Optional: show when the bot is up
@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    # Presence is optional
    await bot.change_presence(activity=discord.Game(name="type ?help"))

# Optional: basic error handler for common issues
@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("Unknown command. Try `?help`.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument: `{error.param.name}`.")
    else:
        # Log and surface brief info
        logger.exception("Unhandled command error", exc_info=error)
        await ctx.send(f"Error: `{type(error).__name__}`")

async def main():
    # Load extensions (cogs). If your file is ./music.py, use "music".
    # If it's ./cogs/music.py, use "cogs.music".
    extensions = ["music"]

    async with bot:
        for ext in extensions:
            try:
                await bot.load_extension(ext)
                logger.info(f"Loaded extension: {ext}")
            except Exception as e:
                logger.exception(f"Failed to load extension {ext}", exc_info=e)

        if not TOKEN:
            logger.error("TOKEN is not set in environment or .env file.")
            raise SystemExit("Missing TOKEN environment variable.")

        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
"""Discord bot with two commands: !hello and !dice.

The bot token goes in the .env file (copy .env.example): the steps are in MYDEVAGENT.md.
Run: python bot.py  (first: python -m pip install -r requirements.txt)
"""

import os
import random

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
intents = discord.Intents.default()
intents.message_content = True  # needed to read commands that start with !
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready() -> None:
    print(f"Logged in as {bot.user}")


@bot.command()
async def hello(ctx: commands.Context) -> None:
    """Greets whoever types the command."""
    await ctx.send(f"Hi {ctx.author.display_name}! 👋")


@bot.command()
async def dice(ctx: commands.Context, sides: int = 6) -> None:
    """Rolls a die: !dice or !dice 20."""
    await ctx.send(f"🎲 You rolled {random.randint(1, max(2, sides))}")


if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is missing: copy .env.example to .env and paste the bot token")
    bot.run(token)

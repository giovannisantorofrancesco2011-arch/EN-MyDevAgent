# Discord bot

Python bot with discord.py. Commands are functions decorated with `@bot.command()` in bot.py.

## Commands
- install: `python -m pip install -r requirements.txt`
- run: `python bot.py`

## First time
1. On https://discord.com/developers/applications create an application, then in the Bot tab press
   "Reset Token" and copy the token.
2. Still in the Bot tab, turn on "Message Content Intent".
3. Copy .env.example to .env and paste the token after `DISCORD_TOKEN=` (the .env file must never go on GitHub).
4. In OAuth2 → URL Generator pick `bot` and the "Send Messages" permission, open the link and invite the bot to
   your server.

## Conventions
- a new command = an async function with `@bot.command()` and a docstring
- the token is only read from .env, never written in the code

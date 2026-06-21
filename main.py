import os
import asyncio
import discord

from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", 0))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


async def carregar_cogs():
    extensoes = ["cogs.aniversarios"]

    for extensao in extensoes:
        try:
            await bot.load_extension(extensao)
            print(f"✅ {extensao} carregado.")
        except Exception as e:
            print(f"❌ Erro ao carregar {extensao}: {e}")


@bot.event
async def on_ready():
    print(f"🌙 {bot.user} conectado.")


async def main():
    async with bot:
        await carregar_cogs()

        await bot.start(TOKEN)


@bot.event
async def setup_hook():
    guild = discord.Object(id=GUILD_ID)

    synced = await bot.tree.sync(guild=guild)

    print(f"✅ {len(synced)} comandos sincronizados no servidor {GUILD_ID}.")

    for cmd in synced:
        print(f"➡️ /{cmd.name}")


asyncio.run(main())
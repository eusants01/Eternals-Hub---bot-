import os
import discord

from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))


intents = discord.Intents.default()

intents.message_content = True
intents.members = True
intents.voice_states = True


bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


@bot.event
async def on_ready():

    print(f"🌙 {bot.user} conectado.")

    try:

        guild = discord.Object(
            id=GUILD_ID
        )

        synced = await bot.tree.sync(
            guild=guild
        )

        print(
            f"✅ {len(synced)} comandos sincronizados."
        )

    except Exception as e:

        print(
            f"❌ Erro ao sincronizar: {e}"
        )


async def load_extensions():

    extensoes = [

        "cogs.painel",
        "cogs.zoeira",
        "cogs.sorte",
        "cogs.livro",
        "cogs.aniversarios"

    ]

    for extensao in extensoes:

        try:

            await bot.load_extension(
                extensao
            )

            print(
                f"📦 {extensao} carregado."
            )

        except Exception as e:

            print(
                f"❌ {extensao}: {e}"
            )


async def main():

    async with bot:

        await load_extensions()

        await bot.start(
            TOKEN
        )


import asyncio

asyncio.run(main())
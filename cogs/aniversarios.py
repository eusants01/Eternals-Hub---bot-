import os
import asyncio
import asyncpg
import discord

from datetime import datetime, time
from zoneinfo import ZoneInfo

from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
CANAL_ANIVERSARIOS_ID = int(os.getenv("CANAL_ANIVERSARIOS_ID", 0))
BANNER_ANIVERSARIO_URL = os.getenv("BANNER_ANIVERSARIO_URL")

BRASILIA = ZoneInfo("America/Sao_Paulo")


class ModalAniversario(discord.ui.Modal, title="🎂 Registrar Aniversário"):

    nome = discord.ui.TextInput(
        label="Como devemos te chamar?",
        placeholder="Ex: Daniel, Sant's, Dani...",
        max_length=30
    )

    data = discord.ui.TextInput(
        label="Data do aniversário",
        placeholder="Ex: 21/07",
        max_length=5
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):

        try:
            dia, mes = self.data.value.strip().split("/")
            dia = int(dia)
            mes = int(mes)

            datetime(
                year=2000,
                month=mes,
                day=dia
            )

        except Exception:
            await interaction.response.send_message(
                "❌ Data inválida. Use o formato `DD/MM`, exemplo: `21/07`.",
                ephemeral=True
            )
            return

        async with self.cog.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO aniversarios (
                    user_id,
                    nome,
                    dia,
                    mes,
                    criado_em
                )
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (user_id)
                DO UPDATE SET
                    nome = EXCLUDED.nome,
                    dia = EXCLUDED.dia,
                    mes = EXCLUDED.mes
                """,
                interaction.user.id,
                self.nome.value,
                dia,
                mes,
                datetime.now(BRASILIA)
            )

        embed = discord.Embed(
            title="🎂 Aniversário registrado!",
            description=(
                f"Seu aniversário foi salvo com sucesso.\n\n"
                f"👤 **Nome:** {self.nome.value}\n"
                f"📅 **Data:** `{dia:02d}/{mes:02d}`\n\n"
                f"Quando chegar seu dia, o Eternals Hub vai comemorar com você. ✨"
            ),
            color=discord.Color.purple()
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )


class PainelAniversarioView(discord.ui.View):

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Registrar aniversário",
        emoji="🎂",
        style=discord.ButtonStyle.primary,
        custom_id="eternals_registrar_aniversario"
    )
    async def registrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            ModalAniversario(self.cog)
        )

    @discord.ui.button(
        label="Ver meu aniversário",
        emoji="📅",
        style=discord.ButtonStyle.secondary,
        custom_id="eternals_ver_aniversario"
    )
    async def ver_aniversario(self, interaction: discord.Interaction, button: discord.ui.Button):

        async with self.cog.pool.acquire() as conn:
            dados = await conn.fetchrow(
                """
                SELECT nome, dia, mes
                FROM aniversarios
                WHERE user_id = $1
                """,
                interaction.user.id
            )

        if not dados:
            await interaction.response.send_message(
                "❌ Você ainda não registrou seu aniversário.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"🎂 **{dados['nome']}**, seu aniversário está registrado como `{dados['dia']:02d}/{dados['mes']:02d}`.",
            ephemeral=True
        )


class Aniversarios(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.pool = None

    async def cog_load(self):
        self.pool = await asyncpg.create_pool(
            DATABASE_URL
        )

        await self.criar_tabelas()

        self.verificar_aniversarios.start()

    async def cog_unload(self):
        self.verificar_aniversarios.cancel()

        if self.pool:
            await self.pool.close()

    async def criar_tabelas(self):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS aniversarios (
                    user_id BIGINT PRIMARY KEY,
                    nome TEXT NOT NULL,
                    dia INTEGER NOT NULL,
                    mes INTEGER NOT NULL,
                    criado_em TIMESTAMP NOT NULL,
                    ultimo_anuncio INTEGER
                )
                """
            )

    @app_commands.command(
        name="painel_aniversarios",
        description="Cria o painel de aniversários do servidor."
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def painel_aniversarios(self, interaction: discord.Interaction):

        embed = discord.Embed(
            title="🎂 Painel de Aniversários",
            description=(
                "Registre seu aniversário para o Eternals Hub comemorar com você.\n\n"
                "Clique no botão abaixo, coloque seu nome e sua data.\n\n"
                "No dia do seu aniversário, o bot enviará uma mensagem especial "
                "com marcação, homenagem e banner no canal de aniversários."
            ),
            color=discord.Color.purple()
        )

        embed.set_footer(
            text="Eternals Hub • Seu dia também vira memória"
        )

        await interaction.response.send_message(
            embed=embed,
            view=PainelAniversarioView(self)
        )

    @app_commands.command(
        name="aniversariantes",
        description="Mostra os aniversários registrados."
    )
    async def aniversariantes(self, interaction: discord.Interaction):

        async with self.pool.acquire() as conn:
            dados = await conn.fetch(
                """
                SELECT nome, dia, mes
                FROM aniversarios
                ORDER BY mes, dia
                """
            )

        if not dados:
            await interaction.response.send_message(
                "🎂 Nenhum aniversário registrado ainda.",
                ephemeral=True
            )
            return

        texto = ""

        for item in dados:
            texto += f"🎈 **{item['nome']}** — `{item['dia']:02d}/{item['mes']:02d}`\n"

        embed = discord.Embed(
            title="📅 Aniversários Registrados",
            description=texto,
            color=discord.Color.purple()
        )

        await interaction.response.send_message(
            embed=embed
        )

    @tasks.loop(minutes=1)
    async def verificar_aniversarios(self):

        agora = datetime.now(BRASILIA)

        if agora.hour != 0 or agora.minute != 1:
            return

        dia_atual = agora.day
        mes_atual = agora.month
        ano_atual = agora.year

        canal = self.bot.get_channel(
            CANAL_ANIVERSARIOS_ID
        )

        if not canal:
            return

        async with self.pool.acquire() as conn:
            aniversariantes = await conn.fetch(
                """
                SELECT user_id, nome, dia, mes, ultimo_anuncio
                FROM aniversarios
                WHERE dia = $1 AND mes = $2
                """,
                dia_atual,
                mes_atual
            )

            for pessoa in aniversariantes:

                if pessoa["ultimo_anuncio"] == ano_atual:
                    continue

                membro = canal.guild.get_member(
                    pessoa["user_id"]
                )

                mencao = membro.mention if membro else f"<@{pessoa['user_id']}>"

                embed = discord.Embed(
                    title="🎉 Hoje é dia de comemorar!",
                    description=(
                        f"Hoje é aniversário de {mencao}! 🎂\n\n"
                        f"Que seu dia seja cheio de risadas, amizade, "
                        f"momentos bons e memórias eternas.\n\n"
                        f"✨ **Feliz aniversário, {pessoa['nome']}!**"
                    ),
                    color=discord.Color.purple()
                )

                if BANNER_ANIVERSARIO_URL:
                    embed.set_image(
                        url=BANNER_ANIVERSARIO_URL
                    )

                embed.set_footer(
                    text="Eternals Hub • Esta mensagem ficará até 23:59"
                )

                mensagem = await canal.send(
                    content=f"🎂 {mencao}",
                    embed=embed
                )

                await conn.execute(
                    """
                    UPDATE aniversarios
                    SET ultimo_anuncio = $1
                    WHERE user_id = $2
                    """,
                    ano_atual,
                    pessoa["user_id"]
                )

                self.bot.loop.create_task(
                    self.apagar_as_2359(mensagem)
                )

    async def apagar_as_2359(self, mensagem: discord.Message):

        agora = datetime.now(BRASILIA)

        alvo = datetime.combine(
            agora.date(),
            time(23, 59),
            tzinfo=BRASILIA
        )

        segundos = (alvo - agora).total_seconds()

        if segundos > 0:
            await asyncio.sleep(segundos)

        try:
            await mensagem.delete()
        except discord.NotFound:
            pass
        except discord.Forbidden:
            pass

    @verificar_aniversarios.before_loop
    async def before_verificar_aniversarios(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(
        Aniversarios(bot)
    )
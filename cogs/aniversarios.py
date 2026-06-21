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

BANNER_PAINEL_ANIVERSARIO_URL = os.getenv("BANNER_PAINEL_ANIVERSARIO_URL")
BANNER_ANIVERSARIO_URL = os.getenv("BANNER_ANIVERSARIO_URL")

BRASILIA = ZoneInfo("America/Sao_Paulo")


class ModalAniversario(discord.ui.Modal, title="🎂 Registro de Aniversário"):

    nome = discord.ui.TextInput(
        label="Como devemos te chamar?",
        placeholder="Ex: Sant's, Daniel, Dani...",
        max_length=30
    )

    data = discord.ui.TextInput(
        label="Sua data de aniversário",
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

            datetime(year=2000, month=mes, day=dia)

        except Exception:
            await interaction.response.send_message(
                "❌ Data inválida. Use o formato `DD/MM`, exemplo: `21/07`.",
                ephemeral=True
            )
            return

        try:
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
                    datetime.now(BRASILIA).replace(tzinfo=None)
                )

        except Exception as e:
            print(f"❌ Erro ao salvar aniversário: {e}")

            await interaction.response.send_message(
                "❌ Não consegui salvar seu aniversário agora. Tente novamente em alguns instantes.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🎉 Sua data foi registrada!",
            description=(
                f"Pronto, **{self.nome.value}**! ✨\n\n"
                f"Seu aniversário ficou salvo como **{dia:02d}/{mes:02d}**.\n"
                f"Quando esse dia chegar, o **Eternals Hub** vai preparar uma homenagem especial para você."
            ),
            color=0x8B5CF6
        )

        embed.set_footer(
            text="Eternals Hub • Seu dia também vira memória"
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
        label="Participar da celebração",
        emoji="🎉",
        style=discord.ButtonStyle.primary,
        custom_id="eternals_registrar_aniversario"
    )
    async def registrar(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        await interaction.response.send_modal(
            ModalAniversario(self.cog)
        )

    @discord.ui.button(
        label="Meu registro",
        emoji="📖",
        style=discord.ButtonStyle.secondary,
        custom_id="eternals_ver_aniversario"
    )
    async def ver_aniversario(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

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
        self.pool = await asyncpg.create_pool(DATABASE_URL)

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

    async def proximo_aniversariante_texto(self):
        hoje = datetime.now(BRASILIA).date()

        async with self.pool.acquire() as conn:
            dados = await conn.fetch(
                """
                SELECT nome, dia, mes
                FROM aniversarios
                ORDER BY mes, dia
                """
            )

        if not dados:
            return "🌙 Ainda ninguém registrou uma data."

        proximos = []

        for item in dados:
            data_aniversario = datetime(
                hoje.year,
                item["mes"],
                item["dia"],
                tzinfo=BRASILIA
            ).date()

            if data_aniversario < hoje:
                data_aniversario = datetime(
                    hoje.year + 1,
                    item["mes"],
                    item["dia"],
                    tzinfo=BRASILIA
                ).date()

            dias = (data_aniversario - hoje).days

            proximos.append(
                (dias, item["nome"], item["dia"], item["mes"])
            )

        proximos.sort(key=lambda x: x[0])

        dias, nome, dia, mes = proximos[0]

        if dias == 0:
            return f"🎉 **{nome}** faz aniversário hoje!"

        if dias == 1:
            return f"👤 **{nome}**\n📅 `{dia:02d}/{mes:02d}`\n⏳ Falta **1 dia**."

        return f"👤 **{nome}**\n📅 `{dia:02d}/{mes:02d}`\n⏳ Faltam **{dias} dias**."

    @app_commands.command(
        name="instalar_aniversarios",
        description="Instala o painel de aniversários no canal atual."
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def instalar_aniversarios(
        self,
        interaction: discord.Interaction
    ):

        proximo = await self.proximo_aniversariante_texto()

        embed = discord.Embed(
            title="🎂 • Celebre seu grande dia!",
            description=(
                "Todos nós temos uma data especial, e ela merece ser lembrada. ✨\n\n"
                "Ao registrar seu aniversário, o **Eternals Hub** irá preparar "
                "uma homenagem exclusiva para você.\n\n"
                "🎈 **O que acontece no seu aniversário?**\n\n"
                "・📢 Anúncio especial no servidor.\n"
                "・🎉 Marcação personalizada.\n"
                "・🖼️ Banner comemorativo.\n"
                "・💜 Mensagem exclusiva.\n"
                "・⏰ Destaque disponível até às **23:59 (BRT)**.\n\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "🎂 **Próximo aniversariante**\n\n"
                f"{proximo}\n\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "Clique em um dos botões abaixo para começar. 🌙"
            ),
            color=0x8B5CF6
        )

        if interaction.guild and interaction.guild.icon:
            embed.set_thumbnail(
                url=interaction.guild.icon.url
            )

        if BANNER_PAINEL_ANIVERSARIO_URL:
            embed.set_image(
                url=BANNER_PAINEL_ANIVERSARIO_URL
            )

        embed.set_footer(
            text="Eternals Hub • Criando memórias desde o primeiro dia"
        )

        await interaction.channel.send(
            embed=embed,
            view=PainelAniversarioView(self)
        )

        await interaction.response.send_message(
            "✅ Painel de aniversários instalado com sucesso.",
            ephemeral=True
        )

    @app_commands.command(
        name="aniversariantes",
        description="Mostra os aniversários registrados."
    )
    async def aniversariantes(
        self,
        interaction: discord.Interaction
    ):

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
            color=0x8B5CF6
        )

        embed.set_footer(
            text="Eternals Hub • Lista de celebrações"
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

    @tasks.loop(minutes=1)
    async def verificar_aniversarios(self):

        agora = datetime.now(BRASILIA)

        if agora.hour != 0 or agora.minute != 1:
            return

        dia_atual = agora.day
        mes_atual = agora.month
        ano_atual = agora.year

        canal = self.bot.get_channel(CANAL_ANIVERSARIOS_ID)

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
                    title="🎉 Hoje o Eternals Hub está em festa!",
                    description=(
                        f"Hoje é aniversário de {mencao}! 🎂\n\n"
                        f"Que seu dia seja leve, divertido e cheio de momentos bons.\n"
                        f"Que nunca faltem risadas, amizade verdadeira e histórias para lembrar.\n\n"
                        f"✨ **Feliz aniversário, {pessoa['nome']}!**\n\n"
                        f"Essa homenagem ficará aqui até às **23:59 (BRT)**."
                    ),
                    color=0x8B5CF6
                )

                if BANNER_ANIVERSARIO_URL:
                    embed.set_image(
                        url=BANNER_ANIVERSARIO_URL
                    )

                embed.set_footer(
                    text="Eternals Hub • Memórias que ficam"
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
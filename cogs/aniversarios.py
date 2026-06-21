import os
import math
import asyncio
import asyncpg
import discord

from datetime import datetime, date, time
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

COR_PADRAO = 0x8B5CF6


# ───────────────────────────── Helpers ─────────────────────────────

def calcular_proxima_data(dia: int, mes: int, hoje: date) -> date:
    """Retorna a próxima ocorrência de uma data de aniversário a partir de hoje.

    Trata o caso de 29/02 em anos não bissextos, comemorando no dia 28/02.
    """
    for ano in (hoje.year, hoje.year + 1):
        try:
            data = date(ano, mes, dia)
        except ValueError:
            data = date(ano, 2, 28)

        if data >= hoje:
            return data

    return date(hoje.year + 1, 2, 28) if (mes, dia) == (2, 29) else date(hoje.year + 1, mes, dia)


def timestamp_unix(data: date) -> int:
    dt = datetime.combine(data, time(0, 0), tzinfo=BRASILIA)
    return int(dt.timestamp())


# ───────────────────────────── Modal de registro ─────────────────────────────

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
            dia_str, mes_str = self.data.value.strip().split("/")
            dia = int(dia_str)
            mes = int(mes_str)

            date(2000, mes, dia) if (mes, dia) != (2, 29) else date(2000, 2, 29)

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
                        mes = EXCLUDED.mes,
                        ultimo_anuncio = NULL
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

        hoje = datetime.now(BRASILIA).date()
        proxima = calcular_proxima_data(dia, mes, hoje)
        ts = timestamp_unix(proxima)

        embed = discord.Embed(
            title="🎉 Prontinho, seu dia está guardado!",
            description=(
                f"Anotei aqui, **{self.nome.value}**. ✨\n\n"
                f"Sua próxima comemoração cai em <t:{ts}:D> (<t:{ts}:R>).\n"
                f"Quando esse dia chegar, o **Eternals Hub** vai preparar uma homenagem especial pra você."
            ),
            color=COR_PADRAO
        )

        embed.set_footer(text="Eternals Hub • Seu dia também vira memória")

        await interaction.response.send_message(embed=embed, ephemeral=True)

        # Atualiza o painel automaticamente, sem precisar reenviar manualmente.
        if interaction.guild_id:
            try:
                await self.cog.atualizar_painel(interaction.guild_id)
            except Exception as e:
                print(f"⚠️ Não consegui atualizar o painel automaticamente: {e}")


# ───────────────────────────── Confirmação de remoção ─────────────────────────────

class ConfirmarRemocaoView(discord.ui.View):

    def __init__(self, cog, user_id: int, nome: str):
        super().__init__(timeout=30)
        self.cog = cog
        self.user_id = user_id
        self.nome = nome

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Essa confirmação não é sua. 😅", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="Sim, remover", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.cog.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM aniversarios WHERE user_id = $1",
                self.user_id
            )

        await interaction.response.edit_message(
            content=f"✅ Removi o registro de **{self.nome}**. Quando quiser, é só registrar de novo.",
            view=None
        )

        if interaction.guild_id:
            try:
                await self.cog.atualizar_painel(interaction.guild_id)
            except Exception as e:
                print(f"⚠️ Não consegui atualizar o painel automaticamente: {e}")

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Ok, mantive seu registro como estava. 🙂",
            view=None
        )


# ───────────────────────────── Lista paginada ─────────────────────────────

class ListaAniversariantesView(discord.ui.View):

    TAMANHO_PAGINA = 8

    def __init__(self, lista: list, autor_id: int):
        super().__init__(timeout=120)
        self.lista = lista
        self.autor_id = autor_id
        self.pagina = 0
        self.total_paginas = max(1, math.ceil(len(lista) / self.TAMANHO_PAGINA))
        self._atualizar_botoes()

    def _atualizar_botoes(self):
        self.anterior.disabled = self.pagina == 0
        self.proximo.disabled = self.pagina >= self.total_paginas - 1

    def montar_embed(self) -> discord.Embed:
        inicio = self.pagina * self.TAMANHO_PAGINA
        fim = inicio + self.TAMANHO_PAGINA
        pedaco = self.lista[inicio:fim]

        if not pedaco:
            descricao = "🌙 Ninguém registrou um aniversário ainda. Seja a primeira pessoa!"
        else:
            linhas = []
            for item in pedaco:
                ts = timestamp_unix(item["data"])
                if item["dias"] == 0:
                    linhas.append(f"🎉 **{item['nome']}** — é hoje!")
                else:
                    linhas.append(
                        f"👤 **{item['nome']}** — `{item['dia']:02d}/{item['mes']:02d}` (<t:{ts}:R>)"
                    )
            descricao = "\n".join(linhas)

        embed = discord.Embed(
            title="📅 Todos os aniversários",
            description=descricao,
            color=COR_PADRAO
        )
        embed.set_footer(text=f"Página {self.pagina + 1}/{self.total_paginas} • Eternals Hub")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "Essa lista não é sua, mas você pode abrir a sua clicando em **Ver todos**! 😉",
                ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="◀ Anterior", style=discord.ButtonStyle.secondary)
    async def anterior(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.pagina -= 1
        self._atualizar_botoes()
        await interaction.response.edit_message(embed=self.montar_embed(), view=self)

    @discord.ui.button(label="Próximo ▶", style=discord.ButtonStyle.secondary)
    async def proximo(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.pagina += 1
        self._atualizar_botoes()
        await interaction.response.edit_message(embed=self.montar_embed(), view=self)


# ───────────────────────────── Painel principal ─────────────────────────────

class PainelAniversarioView(discord.ui.View):

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Registrar",
        emoji="🎉",
        style=discord.ButtonStyle.primary,
        custom_id="eternals_registrar_aniversario",
        row=0
    )
    async def registrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ModalAniversario(self.cog))

    @discord.ui.button(
        label="Meu registro",
        emoji="📖",
        style=discord.ButtonStyle.secondary,
        custom_id="eternals_ver_aniversario",
        row=0
    )
    async def ver_aniversario(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.cog.pool.acquire() as conn:
            dados = await conn.fetchrow(
                "SELECT nome, dia, mes FROM aniversarios WHERE user_id = $1",
                interaction.user.id
            )

        if not dados:
            await interaction.response.send_message(
                "❌ Você ainda não registrou seu aniversário. Clique em **Registrar** para participar!",
                ephemeral=True
            )
            return

        hoje = datetime.now(BRASILIA).date()
        proxima = calcular_proxima_data(dados["dia"], dados["mes"], hoje)
        ts = timestamp_unix(proxima)

        embed = discord.Embed(
            title=f"📖 Registro de {dados['nome']}",
            description=(
                f"Sua data: `{dados['dia']:02d}/{dados['mes']:02d}`\n"
                f"Próxima comemoração: <t:{ts}:D> (<t:{ts}:R>)"
            ),
            color=COR_PADRAO
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="Ver todos",
        emoji="📅",
        style=discord.ButtonStyle.secondary,
        custom_id="eternals_ver_todos_aniversarios",
        row=1
    )
    async def ver_todos(self, interaction: discord.Interaction, button: discord.ui.Button):
        lista = await self.cog.buscar_aniversarios_ordenados()
        view = ListaAniversariantesView(lista, interaction.user.id)
        await interaction.response.send_message(embed=view.montar_embed(), view=view, ephemeral=True)

    @discord.ui.button(
        label="Remover registro",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="eternals_remover_aniversario",
        row=1
    )
    async def remover(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.cog.pool.acquire() as conn:
            dados = await conn.fetchrow(
                "SELECT nome FROM aniversarios WHERE user_id = $1",
                interaction.user.id
            )

        if not dados:
            await interaction.response.send_message(
                "Você ainda não tem um aniversário registrado.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"Tem certeza que quer remover o registro de **{dados['nome']}**?",
            view=ConfirmarRemocaoView(self.cog, interaction.user.id, dados["nome"]),
            ephemeral=True
        )


# ───────────────────────────── Cog ─────────────────────────────

class Aniversarios(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.pool = None

    async def cog_load(self):
        self.pool = await asyncpg.create_pool(DATABASE_URL)
        await self.criar_tabelas()
        self.bot.add_view(PainelAniversarioView(self))
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

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS aniversarios_painel (
                    guild_id BIGINT PRIMARY KEY,
                    channel_id BIGINT NOT NULL,
                    message_id BIGINT NOT NULL
                )
                """
            )

    # ─────────────── Consultas auxiliares ───────────────

    async def buscar_aniversarios_ordenados(self) -> list:
        hoje = datetime.now(BRASILIA).date()

        async with self.pool.acquire() as conn:
            dados = await conn.fetch(
                "SELECT user_id, nome, dia, mes FROM aniversarios"
            )

        lista = []

        for item in dados:
            proxima = calcular_proxima_data(item["dia"], item["mes"], hoje)
            dias = (proxima - hoje).days

            lista.append({
                "user_id": item["user_id"],
                "nome": item["nome"],
                "dia": item["dia"],
                "mes": item["mes"],
                "data": proxima,
                "dias": dias
            })

        lista.sort(key=lambda x: x["dias"])
        return lista

    async def montar_bloco_proximos(self, limite: int = 3) -> str:
        lista = (await self.buscar_aniversarios_ordenados())[:limite]

        if not lista:
            return "🌙 Ninguém registrou um aniversário ainda. Que tal ser a primeira pessoa?"

        linhas = []

        for item in lista:
            ts = timestamp_unix(item["data"])

            if item["dias"] == 0:
                linhas.append(f"🎉 **{item['nome']}** — é hoje!")
            else:
                linhas.append(f"👤 **{item['nome']}** — <t:{ts}:D> (<t:{ts}:R>)")

        return "\n".join(linhas)

    async def montar_embed_painel(self, guild: discord.Guild) -> discord.Embed:
        bloco = await self.montar_bloco_proximos(limite=3)

        embed = discord.Embed(
            title="🎂 Aniversários do Eternals Hub",
            description=(
                "Todo mundo merece ser lembrado no seu dia. 🌙✨\n\n"
                "Clique em **Registrar** e deixe sua data guardada aqui — quando ela chegar, "
                "o servidor inteiro vai saber que hoje é o seu dia.\n\n"
                "**🔜 Próximos a comemorar**\n"
                f"{bloco}"
            ),
            color=COR_PADRAO
        )

        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        if BANNER_PAINEL_ANIVERSARIO_URL:
            embed.set_image(url=BANNER_PAINEL_ANIVERSARIO_URL)

        embed.set_footer(text="Eternals Hub • Criando memórias desde o primeiro dia")
        return embed

    async def atualizar_painel(self, guild_id: int):
        async with self.pool.acquire() as conn:
            config = await conn.fetchrow(
                "SELECT channel_id, message_id FROM aniversarios_painel WHERE guild_id = $1",
                guild_id
            )

        if not config:
            return

        canal = self.bot.get_channel(config["channel_id"])

        if not canal:
            return

        try:
            mensagem = await canal.fetch_message(config["message_id"])
        except (discord.NotFound, discord.Forbidden):
            return

        embed = await self.montar_embed_painel(canal.guild)

        try:
            await mensagem.edit(embed=embed)
        except discord.HTTPException:
            pass

    # ─────────────── Comandos ───────────────

    @app_commands.command(
        name="instalar_aniversarios",
        description="Instala o painel de aniversários no canal atual."
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def instalar_aniversarios(self, interaction: discord.Interaction):
        embed = await self.montar_embed_painel(interaction.guild)

        mensagem = await interaction.channel.send(
            embed=embed,
            view=PainelAniversarioView(self)
        )

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO aniversarios_painel (guild_id, channel_id, message_id)
                VALUES ($1, $2, $3)
                ON CONFLICT (guild_id)
                DO UPDATE SET
                    channel_id = EXCLUDED.channel_id,
                    message_id = EXCLUDED.message_id
                """,
                interaction.guild_id,
                interaction.channel_id,
                mensagem.id
            )

        await interaction.response.send_message(
            "✅ Painel instalado! Agora ele se atualiza sozinho sempre que alguém registrar um aniversário.",
            ephemeral=True
        )

    @app_commands.command(
        name="aniversariantes",
        description="Mostra todos os aniversários registrados."
    )
    async def aniversariantes(self, interaction: discord.Interaction):
        lista = await self.buscar_aniversarios_ordenados()
        view = ListaAniversariantesView(lista, interaction.user.id)
        await interaction.response.send_message(embed=view.montar_embed(), view=view, ephemeral=True)

    @app_commands.command(
        name="meu_aniversario",
        description="Mostra a data de aniversário que você registrou."
    )
    async def meu_aniversario(self, interaction: discord.Interaction):
        async with self.pool.acquire() as conn:
            dados = await conn.fetchrow(
                "SELECT nome, dia, mes FROM aniversarios WHERE user_id = $1",
                interaction.user.id
            )

        if not dados:
            await interaction.response.send_message(
                "❌ Você ainda não registrou seu aniversário. Use o painel ou clique em **Registrar**!",
                ephemeral=True
            )
            return

        hoje = datetime.now(BRASILIA).date()
        proxima = calcular_proxima_data(dados["dia"], dados["mes"], hoje)
        ts = timestamp_unix(proxima)

        embed = discord.Embed(
            title=f"📖 Registro de {dados['nome']}",
            description=(
                f"Sua data: `{dados['dia']:02d}/{dados['mes']:02d}`\n"
                f"Próxima comemoração: <t:{ts}:D> (<t:{ts}:R>)"
            ),
            color=COR_PADRAO
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="remover_aniversario",
        description="Remove o seu aniversário registrado."
    )
    async def remover_aniversario(self, interaction: discord.Interaction):
        async with self.pool.acquire() as conn:
            dados = await conn.fetchrow(
                "SELECT nome FROM aniversarios WHERE user_id = $1",
                interaction.user.id
            )

        if not dados:
            await interaction.response.send_message(
                "Você ainda não tem um aniversário registrado.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"Tem certeza que quer remover o registro de **{dados['nome']}**?",
            view=ConfirmarRemocaoView(self, interaction.user.id, dados["nome"]),
            ephemeral=True
        )

    # ─────────────── Tarefa diária ───────────────

    @tasks.loop(time=time(hour=0, minute=1, tzinfo=BRASILIA))
    async def verificar_aniversarios(self):
        agora = datetime.now(BRASILIA)

        dia_atual = agora.day
        mes_atual = agora.month
        ano_atual = agora.year

        canal = self.bot.get_channel(CANAL_ANIVERSARIOS_ID)

        if canal:
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

                    membro = canal.guild.get_member(pessoa["user_id"])
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
                        color=COR_PADRAO
                    )

                    if BANNER_ANIVERSARIO_URL:
                        embed.set_image(url=BANNER_ANIVERSARIO_URL)

                    embed.set_footer(text="Eternals Hub • Memórias que ficam")

                    mensagem = await canal.send(content=f"🎂 {mencao}", embed=embed)

                    await conn.execute(
                        "UPDATE aniversarios SET ultimo_anuncio = $1 WHERE user_id = $2",
                        ano_atual,
                        pessoa["user_id"]
                    )

                    self.bot.loop.create_task(self.apagar_as_2359(mensagem))

        # Mantém os painéis de todos os servidores com a lista de "próximos" em dia.
        async with self.pool.acquire() as conn:
            configs = await conn.fetch("SELECT guild_id FROM aniversarios_painel")

        for config in configs:
            try:
                await self.atualizar_painel(config["guild_id"])
            except Exception as e:
                print(f"⚠️ Não consegui atualizar o painel do servidor {config['guild_id']}: {e}")

    async def apagar_as_2359(self, mensagem: discord.Message):
        agora = datetime.now(BRASILIA)

        alvo = datetime.combine(agora.date(), time(23, 59), tzinfo=BRASILIA)
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
    await bot.add_cog(Aniversarios(bot))
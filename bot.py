import os
import discord
from discord.ext import commands, tasks
import csv
import asyncio
import datetime
import json
from dotenv import load_dotenv
from googleapiclient.discovery import build
import requests
from string import Template
import logging

load_dotenv()
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0"))
AUKCJE_KANAL_ID = int(os.getenv("AUKCJE_KANAL_ID", "0"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ORDER_CHANNEL_ID = int(os.getenv("ORDER_CHANNEL_ID", "0"))
SELLER_CHANNEL_ID = int(os.getenv("SELLER_CHANNEL_ID", "0"))
OGLOSZENIA_KANAL_ID = int(os.getenv("OGLOSZENIA_KANAL_ID", "0"))
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
LIVE_CHAT_ID = os.getenv("LIVE_CHAT_ID")
POKEMONTCG_API_TOKEN = os.getenv("POKEMONTCG_API_TOKEN")

bot = commands.Bot(command_prefix='/', intents=discord.Intents.all())

aukcje_kolejka = []
aktualna_aukcja = None
youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY) if YOUTUBE_API_KEY else None
yt_page_token = None
pending_orders = {}
seller_panel_msg: discord.Message | None = None
auction_msg: discord.Message | None = None
user_bid_messages: dict[int, discord.Message] = {}
announcement_msg: discord.Message | None = None
paused = False


def fetch_card_assets(nazwa: str, numer: str) -> tuple[str | None, str | None]:
    """Return card and set logo image URLs from PokemonTCG API if available."""
    base = "https://api.pokemontcg.io/v2/cards"
    numer = numer.strip().lower()
    headers = {}
    if POKEMONTCG_API_TOKEN:
        headers["X-Api-Key"] = POKEMONTCG_API_TOKEN

    def _parse(card):
        if card:
            return (
                card.get("images", {}).get("large"),
                card.get("set", {}).get("images", {}).get("logo"),
            )
        return None, None

    # First try to fetch by card ID (e.g. sv2-10)
    try:
        resp = requests.get(f"{base}/{numer}", headers=headers, timeout=5)
        resp.raise_for_status()
        card = resp.json().get("data")
        return _parse(card)
    except Exception as e:
        logging.warning("Direct lookup for %s failed: %s", numer, e)

    # Fallback to search query if direct lookup failed
    try:
        set_id = ""
        card_no = numer
        if "-" in numer:
            set_id, card_no = numer.split("-", 1)
        parts = [f'name:"{nazwa}"', f'number:"{card_no}"']
        if set_id:
            parts.append(f'set.id:{set_id}')
        query = " ".join(parts)
        params = {"q": query, "pageSize": 1}
        resp = requests.get(base, params=params, headers=headers, timeout=5)
        resp.raise_for_status()
        cards = resp.json().get("data")
        if cards:
            return _parse(cards[0])
        logging.warning("No results for query: %s", query)
    except Exception as e:
        logging.warning("Search request for %s failed: %s", numer, e)
    logging.warning("Card image for %s (%s) not found", nazwa, numer)
    return None, None


async def fetch_card_assets_async(nazwa: str, numer: str) -> tuple[str | None, str | None]:
    """Asynchronously fetch card assets without blocking the event loop."""
    return await asyncio.to_thread(fetch_card_assets, nazwa, numer)


async def update_panel_embed():
    """Update or create the seller control panel embed."""
    channel = bot.get_channel(SELLER_CHANNEL_ID)
    if channel is None:
        return
    embed = discord.Embed(title="Panel aukcji", color=0x00FF90)
    queue_preview = "\n".join(
        f"{a.nazwa} ({a.numer})" for a in aukcje_kolejka[:5]
    ) or "Brak"
    embed.add_field(name="W kolejce", value=queue_preview, inline=False)
    if aktualna_aukcja:
        info = f"{aktualna_aukcja.nazwa} ({aktualna_aukcja.numer})\nCena: {aktualna_aukcja.cena:.2f} PLN"
        if aktualna_aukcja.zwyciezca:
            info += f"\nProwadzi: {aktualna_aukcja.zwyciezca}"
        if aktualna_aukcja.start_time:
            koniec = aktualna_aukcja.start_time + datetime.timedelta(seconds=aktualna_aukcja.czas)
            pozostalo = int((koniec - datetime.datetime.utcnow()).total_seconds())
            if pozostalo < 0:
                pozostalo = 0
            info += f"\nPozostało: {pozostalo}s"
        embed.add_field(name="Aktualna aukcja", value=info, inline=False)
    view = PanelView()
    global seller_panel_msg
    if seller_panel_msg:
        try:
            await seller_panel_msg.edit(embed=embed, view=view)
        except discord.NotFound:
            seller_panel_msg = await channel.send(embed=embed, view=view)
    else:
        seller_panel_msg = await channel.send(embed=embed, view=view)



async def update_announcement_embed():
    """Uaktualnij lub utwórz embed z ogłoszeniem aukcji."""
    channel = bot.get_channel(OGLOSZENIA_KANAL_ID)
    if channel is None:
        return

    embed = discord.Embed(title="🔔 Ogłoszenie aukcji", color=0x00BFFF)

    if aktualna_aukcja:
        embed.add_field(
            name="Aktualna karta",
            value=f"{aktualna_aukcja.nazwa} ({aktualna_aukcja.numer})",
            inline=False,
        )
        embed.add_field(
            name="Cena",
            value=f"{aktualna_aukcja.cena:.2f} PLN",
            inline=True,
        )
        embed.add_field(
            name="Prowadzi",
            value=f"{aktualna_aukcja.zwyciezca or 'Brak'}",
            inline=True,
        )

        if aktualna_aukcja.start_time:
            czas_koniec = aktualna_aukcja.start_time + datetime.timedelta(seconds=aktualna_aukcja.czas)
            embed.add_field(
                name="Koniec aukcji",
                value=czas_koniec.strftime('%H:%M:%S'),
                inline=False,
            )

        if aktualna_aukcja.obraz_url:
            embed.set_thumbnail(url=aktualna_aukcja.obraz_url)
    else:
        embed.add_field(name="Status", value="Brak aktywnej aukcji", inline=False)

    kolejka = "\n".join(f"{a.nazwa} ({a.numer})" for a in aukcje_kolejka[:5]) or "Brak"
    embed.add_field(name="W kolejce", value=kolejka, inline=False)

    view = AnnouncementView()
    global announcement_msg
    if announcement_msg:
        try:
            await announcement_msg.edit(embed=embed, view=view)
        except discord.NotFound:
            announcement_msg = await channel.send(embed=embed, view=view)
    else:
        announcement_msg = await channel.send(embed=embed, view=view)

async def announce_winner(aukcja: 'Aukcja'):
    """Wyświetl w ogłoszeniach wynik zakończonej aukcji."""
    channel = bot.get_channel(OGLOSZENIA_KANAL_ID)
    if channel is None:
        return
    embed = discord.Embed(
        title="✅ Aukcja zakończona",
        color=0xFF0000,
    )
    embed.add_field(
        name="Karta",
        value=f"{aukcja.nazwa} ({aukcja.numer})",
        inline=False,
    )
    embed.add_field(
        name="Cena końcowa",
        value=f"{aukcja.cena:.2f} PLN",
        inline=True,
    )
    embed.add_field(
        name="Zwycięzca",
        value=str(aukcja.zwyciezca or 'Brak'),
        inline=True,
    )
    if aukcja.obraz_url:
        embed.set_thumbnail(url=aukcja.obraz_url)
    view = AnnouncementView()
    global announcement_msg
    if announcement_msg:
        try:
            await announcement_msg.edit(embed=embed, view=view)
        except discord.NotFound:
            announcement_msg = await channel.send(embed=embed, view=view)
    else:
        announcement_msg = await channel.send(embed=embed, view=view)



async def update_auction_embed():
    """Aktualizuje embed licytacyjny z grafiką, ceną, prowadzącym i odliczaniem."""
    if not aktualna_aukcja or not auction_msg:
        return

    embed = discord.Embed(
        title=f"🎴 {aktualna_aukcja.nazwa} ({aktualna_aukcja.numer})",
        description=aktualna_aukcja.opis or "Brak opisu.",
        color=0xFFD700
    )

    embed.add_field(
        name="💸 Aktualna cena",
        value=f"**{aktualna_aukcja.cena:.2f} PLN**",
        inline=True
    )

    embed.add_field(
        name="➕ Kwota przebicia",
        value=f"{aktualna_aukcja.przebicie:.2f} PLN",
        inline=True
    )

    embed.add_field(
        name="🏆 Prowadzi",
        value=str(aktualna_aukcja.zwyciezca) if aktualna_aukcja.zwyciezca else "Brak",
        inline=True
    )

    if aktualna_aukcja.start_time:
        koniec = aktualna_aukcja.start_time + datetime.timedelta(seconds=aktualna_aukcja.czas)
        pozostalo = int((koniec - datetime.datetime.utcnow()).total_seconds())
        pozostalo = max(pozostalo, 0)
        embed.set_footer(text=f"⏳ Pozostało: {pozostalo}s")

    if aktualna_aukcja.logo_url:
        embed.set_author(name="Aukcja Pokémon", icon_url=aktualna_aukcja.logo_url)

    if aktualna_aukcja.obraz_url:
        embed.set_image(url=aktualna_aukcja.obraz_url)
    else:
        embed.add_field(name="Obraz", value="Brak zdjęcia karty", inline=False)

    await auction_msg.edit(embed=embed)

async def countdown_task(message: discord.Message, seconds: int):
    await update_auction_embed()
    await update_announcement_embed()
    for _ in range(seconds):
        await asyncio.sleep(1)
        await update_auction_embed()
        await update_announcement_embed()
    await zakoncz_aukcje(message)
    await update_panel_embed()


async def start_next_auction(interaction: discord.Interaction | None = None):
    global aktualna_aukcja
    if paused:
        if interaction:
            await interaction.response.send_message("Panel wstrzymany.", ephemeral=True)
        return
    if aktualna_aukcja:
        if interaction:
            await interaction.response.send_message("Aukcja w toku.", ephemeral=True)
        return
    if not aukcje_kolejka:
        if interaction:
            await interaction.response.send_message("Brak aukcji w kolejce.", ephemeral=True)
        return
    if interaction:
        await interaction.response.defer()

    aktualna_aukcja = aukcje_kolejka.pop(0)
    aktualna_aukcja.start_time = datetime.datetime.utcnow()
    img, logo = await fetch_card_assets_async(
        aktualna_aukcja.nazwa, aktualna_aukcja.numer
    )
    aktualna_aukcja.obraz_url = img
    aktualna_aukcja.logo_url = logo
    logging.info(
        "Fetched assets for %s (%s): image=%s logo=%s",
        aktualna_aukcja.nazwa,
        aktualna_aukcja.numer,
        bool(img),
        bool(logo),
    )

    embed = discord.Embed(
        title=f"🏁 **{aktualna_aukcja.nazwa}** ({aktualna_aukcja.numer})",
        description=aktualna_aukcja.opis,
        color=0x00ff90,
    )
    embed.add_field(name="Numer", value=f"**{aktualna_aukcja.numer}**", inline=True)
    embed.add_field(name="Cena startowa", value=f"**{aktualna_aukcja.cena:.2f} PLN**", inline=True)
    embed.set_footer(text=f"⏳ Czas trwania: {aktualna_aukcja.czas} s")
    if aktualna_aukcja.logo_url:
        embed.set_thumbnail(url=aktualna_aukcja.logo_url)
    if aktualna_aukcja.obraz_url:
        embed.set_image(url=aktualna_aukcja.obraz_url)
    else:
        embed.add_field(name="Obraz", value="Brak zdjęcia karty", inline=False)

    channel = bot.get_channel(AUKCJE_KANAL_ID)
    msg = await channel.send(embed=embed, view=LicytacjaView())
    global auction_msg
    auction_msg = msg

    await update_auction_embed()

    zapisz_html(aktualna_aukcja)
    zapisz_json(aktualna_aukcja)

    bot.loop.create_task(countdown_task(msg, aktualna_aukcja.czas))
    await update_panel_embed()
    await update_announcement_embed()


@bot.event
async def on_ready():
    if youtube:
        check_youtube_chat.start()
    refresh_panel.start()

class Aukcja:
    def __init__(self, nazwa, numer, opis, cena_start, przebicie, czas):
        self.nazwa = nazwa
        self.numer = numer
        self.opis = opis
        # Support both comma and dot as decimal separators
        self.cena = float(str(cena_start).replace(",", "."))
        self.przebicie = float(str(przebicie).replace(",", "."))
        self.czas = int(czas)
        self.historia = []
        self.zwyciezca = None
        self.start_time = None
        self.order_number = None
        self.payment_method = None
        self.obraz_url = None
        self.logo_url = None

    def licytuj(self, user):
        self.cena += self.przebicie
        self.historia.append((str(user), self.cena, datetime.datetime.utcnow().isoformat()))
        self.zwyciezca = user

@bot.command()
async def zaladuj(ctx):
    if ctx.author.id != ADMIN_ID:
        await ctx.send('Brak uprawnień.')
        return
    global aukcje_kolejka
    aukcje_kolejka.clear()
    with open('aukcje.csv', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            aukcja = Aukcja(row['nazwa_karty'], row['numer_karty'], row['opis'], row['cena_początkowa'], row['kwota_przebicia'], row['czas_trwania'])
            aukcje_kolejka.append(aukcja)
    await ctx.send(f'Załadowano {len(aukcje_kolejka)} aukcji.')
    await update_panel_embed()
    await start_next_auction()
    await update_announcement_embed()

@bot.command()
async def start_aukcja(ctx):
    if ctx.author.id != ADMIN_ID:
        await ctx.send('Brak uprawnień.')
        return
    await start_next_auction()


def zapisz_html(aukcja: Aukcja, template_path: str = "templates/auction_template.html"):
    with open(template_path, encoding="utf-8") as f:
        template = Template(f.read())

    historia_html = "".join(
        f"<li>{u} - {c:.2f} PLN - {t}</li>" for u, c, t in aukcja.historia[-4:]
    )

    html = template.safe_substitute(
        nazwa=aukcja.nazwa,
        numer=aukcja.numer,
        opis=aukcja.opis,
        cena=f"{aukcja.cena:.2f}",
        zwyciezca=aukcja.zwyciezca or "",
        historia=historia_html,
        obraz=aukcja.obraz_url or "",
    )

    with open("aktualna_aukcja.html", "w", encoding="utf-8") as f:
        f.write(html)

def zapisz_json(aukcja: Aukcja):
    dane = {
        "nazwa": aukcja.nazwa,
        "numer": aukcja.numer,
        "opis": aukcja.opis,
        "ostateczna_cena": aukcja.cena,
        "zwyciezca": str(aukcja.zwyciezca) if aukcja.zwyciezca else None,
        "historia": aukcja.historia[-4:],
        "start_time": (aukcja.start_time.isoformat() + "Z") if aukcja.start_time else None,
        "czas": aukcja.czas,
        "obraz": aukcja.obraz_url,
        "logo": aukcja.logo_url,
    }
    with open('aktualna_aukcja.json', 'w', encoding='utf-8') as f:
        json.dump(dane, f, ensure_ascii=False, indent=2)

def generate_order_number() -> str:
    now = datetime.datetime.utcnow()
    prefix = f"AUC-{now.year}-{now.month:02d}-"
    os.makedirs('orders', exist_ok=True)
    counter_file = 'orders/counter.txt'
    try:
        with open(counter_file, 'r') as f:
            counter = int(f.read().strip())
    except FileNotFoundError:
        counter = 0
    counter += 1
    with open(counter_file, 'w') as f:
        f.write(str(counter))
    return prefix + f"{counter:04d}"

def zapisz_zamowienie(aukcja: Aukcja):
    order_number = generate_order_number()
    aukcja.order_number = order_number
    os.makedirs('orders', exist_ok=True)
    with open(f'orders/zamowienie_{order_number}.txt', 'w', encoding='utf-8') as f:
        f.write(
            f'Uzytkownik: {aukcja.zwyciezca}\n'
            f'Karta: {aukcja.nazwa}\n'
            f'Cena: {aukcja.cena:.2f}\n'
            f'Numer zamowienia: {order_number}\n'
        )

async def send_order_dm(aukcja: Aukcja):
    if not isinstance(aukcja.zwyciezca, discord.User):
        return
    due_date = (datetime.datetime.utcnow() + datetime.timedelta(days=2)).strftime('%d %B %Y %H:%M')
    view = OrderView(aukcja)
    message = (
        f"Gratulacje! Wygrałeś licytację karty {aukcja.nazwa} {aukcja.numer} za {aukcja.cena:.2f} PLN.\n"
        f"Koszt wysyłki: 10,00 PLN (jeśli to Twoja pierwsza karta).\n"
        "Wybierz metodę płatności i potwierdź zakup.\n"
        "W przyszłości pojawi się tutaj link do strony z płatnościami i wysyłką."
    )
    try:
        if aukcja.obraz_url:
            await aukcja.zwyciezca.send(aukcja.obraz_url)
        await aukcja.zwyciezca.send(message, view=view)
        await aukcja.zwyciezca.send(f"Masz czas do {due_date}.")
    except discord.Forbidden:
        pass

async def notify_order_channel(aukcja: Aukcja):
    channel = bot.get_channel(ORDER_CHANNEL_ID)
    if channel is None:
        return
    embed = discord.Embed(
        title=f"Zamówienie {aukcja.order_number}",
        description=f"{aukcja.nazwa} ({aukcja.numer})",
        color=0x00FF00,
    )
    if aukcja.obraz_url:
        embed.set_image(url=aukcja.obraz_url)
    else:
        embed.add_field(name="Obraz", value="Brak zdjęcia karty", inline=False)
    embed.add_field(name="Cena", value=f"{aukcja.cena:.2f} PLN", inline=False)
    if aukcja.payment_method:
        embed.add_field(name="Metoda płatności", value=aukcja.payment_method, inline=False)
    embed.add_field(name="Kupujący", value=str(aukcja.zwyciezca), inline=False)
    embed.set_footer(text="Status: oczekuje na potwierdzenie")
    msg = await channel.send(embed=embed)
    pending_orders[msg.id] = aukcja
    await msg.add_reaction("✅")

async def zakoncz_aukcje(msg):
    global aktualna_aukcja, auction_msg
    if aktualna_aukcja:
        zapisz_html(aktualna_aukcja)
        zapisz_json(aktualna_aukcja)
        if aktualna_aukcja.zwyciezca:
            zapisz_zamowienie(aktualna_aukcja)
            await send_order_dm(aktualna_aukcja)

        embed = discord.Embed(
            title=f"✅ Aukcja zako\u0144czona: {aktualna_aukcja.nazwa}",
            color=0xff0000,
        )
        embed.add_field(
            name="💵 Cena ko\u0144cowa",
            value=f"**{aktualna_aukcja.cena:.2f} PLN**",
            inline=True,
        )
        embed.add_field(
            name="🏆 Zwyci\u0119zca",
            value=f"**{aktualna_aukcja.zwyciezca or 'Brak'}**",
            inline=True,
        )
        if aktualna_aukcja.logo_url:
            embed.set_thumbnail(url=aktualna_aukcja.logo_url)
        if aktualna_aukcja.obraz_url:
            embed.set_image(url=aktualna_aukcja.obraz_url)
        try:
            await msg.edit(embed=embed, view=None)
        except discord.NotFound:
            pass

        await announce_winner(aktualna_aukcja)

        # finalize user bid messages
        for m in list(user_bid_messages.values()):
            try:
                await m.edit(content=f"Aukcja zakończona. Cena końcowa: {aktualna_aukcja.cena:.2f} PLN")
            except (discord.NotFound, discord.HTTPException):
                pass
        user_bid_messages.clear()

        aktualna_aukcja = None
        auction_msg = None
        await update_panel_embed()


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Następna karta', style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != ADMIN_ID:
            await interaction.response.send_message('Brak uprawnień.', ephemeral=True)
            return
        await start_next_auction(interaction)

    @discord.ui.button(label='⏸ Pauza', style=discord.ButtonStyle.secondary)
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != ADMIN_ID:
            await interaction.response.send_message('Brak uprawnień.', ephemeral=True)
            return
        global paused
        paused = not paused
        button.label = '▶ Wznów' if paused else '⏸ Pauza'
        await interaction.response.defer()
        await update_panel_embed()

class AnnouncementView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Następna karta', style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != ADMIN_ID:
            await interaction.response.send_message('Brak uprawnień.', ephemeral=True)
            return
        await start_next_auction(interaction)

class LicytacjaView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='🔼 LICYTUJ', style=discord.ButtonStyle.green)
    async def licytuj(self, interaction: discord.Interaction, button: discord.ui.Button):
        global aktualna_aukcja
        if not aktualna_aukcja:
            await interaction.response.send_message("Brak aktywnej aukcji.", ephemeral=True)
            return
        if aktualna_aukcja.start_time:
            end_time = aktualna_aukcja.start_time + datetime.timedelta(seconds=aktualna_aukcja.czas)
            if datetime.datetime.utcnow() >= end_time:
                await interaction.response.send_message("Aukcja już zakończona.", ephemeral=True)
                return
        aktualna_aukcja.licytuj(interaction.user)
        zapisz_html(aktualna_aukcja)
        zapisz_json(aktualna_aukcja)
        content = f"✅ Twoja oferta: {aktualna_aukcja.cena:.2f} PLN"
        msg = user_bid_messages.get(interaction.user.id)
        if msg:
            await interaction.response.defer()
            try:
                await msg.edit(content=content)
            except discord.NotFound:
                msg = None
        if not msg:
            await interaction.response.send_message(content, ephemeral=True)
            try:
                user_bid_messages[interaction.user.id] = await interaction.original_response()
            except Exception:
                pass
        await update_panel_embed()
        await update_auction_embed()
        await update_announcement_embed()


class OrderView(discord.ui.View):
    def __init__(self, aukcja: Aukcja):
        super().__init__(timeout=None)
        self.aukcja = aukcja

    async def _process(self, interaction: discord.Interaction, method: str):
        self.aukcja.payment_method = method
        await interaction.response.send_message("Dziękujemy za potwierdzenie.", ephemeral=True)
        await notify_order_channel(self.aukcja)
        self.stop()

    @discord.ui.button(label='BLIK', style=discord.ButtonStyle.primary)
    async def blik(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._process(interaction, "BLIK")

    @discord.ui.button(label='PRZELEW', style=discord.ButtonStyle.secondary)
    async def przelew(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._process(interaction, "PRZELEW")


@tasks.loop(seconds=1)
async def refresh_panel():
    await update_panel_embed()

@tasks.loop(seconds=5)
async def check_youtube_chat():
    global yt_page_token
    if not youtube or not LIVE_CHAT_ID or not aktualna_aukcja:
        return
    try:
        resp = youtube.liveChatMessages().list(
            liveChatId=LIVE_CHAT_ID,
            part="snippet,authorDetails",
            pageToken=yt_page_token
        ).execute()
        yt_page_token = resp.get("nextPageToken")
        for item in resp.get("items", []):
            msg_text = item["snippet"]["displayMessage"].lower()
            if "!bit" in msg_text:
                user = item["authorDetails"]["displayName"]
                aktualna_aukcja.licytuj(user)
                zapisz_html(aktualna_aukcja)
                zapisz_json(aktualna_aukcja)
                await update_panel_embed()
                await update_auction_embed()
                await update_announcement_embed()
    except Exception:
        pass


@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    if reaction.message.id in pending_orders and str(reaction.emoji) == "✅" and user.id == ADMIN_ID:
        aukcja = pending_orders.pop(reaction.message.id)
        if isinstance(aukcja.zwyciezca, discord.User):
            try:
                await aukcja.zwyciezca.send(
                    f"✅ Twoje zamówienie {aukcja.order_number} zostało potwierdzone.\nWkrótce karta trafi do wysyłki. Dzięki za udział w licytacji!"
                )
            except discord.Forbidden:
                pass

bot.run(TOKEN)

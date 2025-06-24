import os
import discord
from discord.ext import commands, tasks
import csv
import asyncio
import datetime
import json
from typing import List
from dotenv import load_dotenv
from googleapiclient.discovery import build

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0"))
AUKCJE_KANAL_ID = int(os.getenv("AUKCJE_KANAL_ID", "0"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
LIVE_CHAT_ID = os.getenv("LIVE_CHAT_ID")

bot = commands.Bot(command_prefix='/', intents=discord.Intents.all())

aukcje_kolejka = []
aktualna_aukcja = None
youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY) if YOUTUBE_API_KEY else None
yt_page_token = None


@bot.event
async def on_ready():
    if youtube:
        check_youtube_chat.start()

class Aukcja:
    def __init__(self, nazwa, numer, opis, cena_start, przebicie, czas):
        self.nazwa = nazwa
        self.numer = numer
        self.opis = opis
        self.cena = float(cena_start)
        self.przebicie = float(przebicie)
        self.czas = int(czas)
        self.historia = []
        self.zwyciezca = None
        self.start_time = None

    def licytuj(self, user):
        self.cena += self.przebicie
        self.historia.append((str(user), self.cena, datetime.datetime.utcnow().isoformat()))
        self.zwyciezca = user

@bot.command()
async def zaladuj(ctx):
    if ctx.author.id != ADMIN_ID:
        return
    global aukcje_kolejka
    aukcje_kolejka.clear()
    with open('aukcje.csv', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            aukcja = Aukcja(row['nazwa_karty'], row['numer_karty'], row['opis'], row['cena_początkowa'], row['kwota_przebicia'], row['czas_trwania'])
            aukcje_kolejka.append(aukcja)
    await ctx.send(f'Załadowano {len(aukcje_kolejka)} aukcji.')

@bot.command()
async def start_aukcja(ctx):
    if ctx.author.id != ADMIN_ID:
        return
    global aktualna_aukcja
    if not aukcje_kolejka:
        await ctx.send("Brak aukcji w kolejce.")
        return

    aktualna_aukcja = aukcje_kolejka.pop(0)
    aktualna_aukcja.start_time = datetime.datetime.utcnow()

    embed = discord.Embed(title=f"Aukcja: {aktualna_aukcja.nazwa}", description=aktualna_aukcja.opis, color=0xffd700)
    embed.add_field(name="Numer", value=aktualna_aukcja.numer, inline=True)
    embed.add_field(name="Cena startowa", value=f"{aktualna_aukcja.cena:.2f} PLN", inline=True)
    embed.set_footer(text=f"Czas trwania: {aktualna_aukcja.czas} sekund")

    kanal = bot.get_channel(AUKCJE_KANAL_ID)
    msg = await kanal.send(embed=embed, view=LicytacjaView())

    await asyncio.sleep(aktualna_aukcja.czas)
    await zakoncz_aukcje(msg)

def zapisz_html(aukcja: Aukcja, template_path: str = "templates/auction_template.html"):
    with open(template_path, encoding="utf-8") as f:
        template = f.read()

    historia_html = "".join(
        f"<li>{u} - {c:.2f} PLN - {t}</li>" for u, c, t in aukcja.historia
    )

    html = template.format(
        nazwa=aukcja.nazwa,
        numer=aukcja.numer,
        opis=aukcja.opis,
        cena=aukcja.cena,
        zwyciezca=aukcja.zwyciezca,
        historia=historia_html,
    )

    with open("aktualna_aukcja.html", "w", encoding="utf-8") as f:
        f.write(html)

def zapisz_json(aukcja: Aukcja):
    dane = {
        "nazwa": aukcja.nazwa,
        "numer": aukcja.numer,
        "opis": aukcja.opis,
        "ostateczna_cena": aukcja.cena,
        "zwyciezca": str(aukcja.zwyciezca),
        "historia": aukcja.historia,
        "start_time": aukcja.start_time.isoformat() if aukcja.start_time else None
    }
    with open('aktualna_aukcja.json', 'w', encoding='utf-8') as f:
        json.dump(dane, f, ensure_ascii=False, indent=2)

def zapisz_zamowienie(aukcja: Aukcja):
    order_number = datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')
    os.makedirs('orders', exist_ok=True)
    with open(f'orders/zamowienie_{order_number}.txt', 'w', encoding='utf-8') as f:
        f.write(
            f'Uzytkownik: {aukcja.zwyciezca}\n'
            f'Karta: {aukcja.nazwa}\n'
            f'Cena: {aukcja.cena:.2f}\n'
            f'Numer zamowienia: {order_number}\n'
        )

async def zakoncz_aukcje(msg):
    global aktualna_aukcja
    if aktualna_aukcja:
        zapisz_html(aktualna_aukcja)
        zapisz_json(aktualna_aukcja)
        zapisz_zamowienie(aktualna_aukcja)
        await msg.reply(f"🔔 Aukcja zakończona! Wygrał **{aktualna_aukcja.zwyciezca}** za **{aktualna_aukcja.cena:.2f} PLN**")
        aktualna_aukcja = None

class LicytacjaView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='🔼 LICYTUJ', style=discord.ButtonStyle.green)
    async def licytuj(self, interaction: discord.Interaction, button: discord.ui.Button):
        global aktualna_aukcja
        if not aktualna_aukcja:
            await interaction.response.send_message("Brak aktywnej aukcji.", ephemeral=True)
            return
        aktualna_aukcja.licytuj(interaction.user)
        await interaction.response.send_message(f"✅ Twoja oferta: {aktualna_aukcja.cena:.2f} PLN", ephemeral=True)


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
    except Exception:
        pass

bot.run(TOKEN)

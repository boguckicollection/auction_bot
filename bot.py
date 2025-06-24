import discord
from discord.ext import commands, tasks
import csv
import asyncio
import datetime
import json
from typing import List

TOKEN = 'TU_WPROWADZ_TOKEN_BOTA'
GUILD_ID = 1234567890  # ID twojego serwera
AUKCJE_KANAL_ID = 1234567890  # ID kanału licytacji
ADMIN_ID = 1234567890  # Twój Discord ID

bot = commands.Bot(command_prefix='/', intents=discord.Intents.all())

aukcje_kolejka = []
aktualna_aukcja = None

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

def zapisz_html(aukcja: Aukcja):
    html = f"""
    <html>
    <head><meta charset='utf-8'><title>Licytacja - {aukcja.nazwa}</title></head>
    <body style='font-family: sans-serif;'>
    <h1>{aukcja.nazwa} ({aukcja.numer})</h1>
    <p>{aukcja.opis}</p>
    <h2>Ostateczna cena: {aukcja.cena:.2f} PLN</h2>
    <h3>Zwycięzca: {aukcja.zwyciezca}</h3>
    <h4>Historia licytacji:</h4>
    <ul>
    {''.join([f'<li>{u} - {c} PLN - {t}</li>' for u, c, t in aukcja.historia])}
    </ul>
    </body>
    </html>
    """
    with open('aktualna_aukcja.html', 'w', encoding='utf-8') as f:
        f.write(html)

async def zakoncz_aukcje(msg):
    global aktualna_aukcja
    if aktualna_aukcja:
        zapisz_html(aktualna_aukcja)
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

bot.run(TOKEN)

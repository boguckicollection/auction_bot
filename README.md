# Auction Bot

This project contains a Discord bot for running live card auctions. Bids can be placed directly on Discord and, optionally, from YouTube live chat.

## Requirements

- Python 3.10 or newer
- The packages listed in `requirements.txt`

## Configuration

Create a `.env` file in the repository root with the following variables:

```
DISCORD_TOKEN=your_discord_bot_token
DISCORD_GUILD_ID=your_server_id
AUKCJE_KANAL_ID=channel_id_for_auctions
ADMIN_ID=discord_user_id_allowed_to_manage
YOUTUBE_API_KEY=optional_youtube_api_key
LIVE_CHAT_ID=optional_live_chat_id
```

`YOUTUBE_API_KEY` and `LIVE_CHAT_ID` enable bidding from YouTube chat. Without them the bot works only on Discord.

## Loading auctions

Auctions are loaded from a CSV file named `aukcje.csv` with columns:

```
nazwa_karty,numer_karty,opis,cena_początkowa,kwota_przebicia,czas_trwania
```

Use the `/zaladuj` command (available only to the admin) to read this file and queue the auctions.

## Running

Install dependencies and start the bot:

```bash
pip install -r requirements.txt
python bot.py
```

## Using the bot

1. Run `/zaladuj` to load auctions from `aukcje.csv`.
2. Run `/start_aukcja` to begin the next auction. The bot posts an embed with item details and a **🔼 LICYTUJ** button.
3. Participants click the button to increase the price by the configured increment. Messages containing `!bit` in the configured YouTube live chat also count as bids if YouTube integration is enabled.
4. When the timer expires the auction ends. The winner and final price are announced and saved to:
   - `aktualna_aukcja.html` – summary page generated from `templates/auction_template.html`
   - `aktualna_aukcja.json` – machine‑readable auction data
   - `orders/` – text file with basic order information

Feel free to modify `templates/auction_template.html` to change how the summary page looks.

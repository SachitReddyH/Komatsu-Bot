# Komatsu Watcher Bot

Two-agent system that watches **komatsu.com.au/equipment/used-equipment** for
specific equipment models and alerts YANTRA LIVE the moment they appear.

```
Agent 1 – Watcher   →   scrapes listings every hour
Agent 2 – Informer  →   sends email alert with price + phone
Enquiry Bot         →   auto-fills the dealer enquiry form
```

---

## Quick Start

### 1. Install dependencies

```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

### 2. Configure targets

Edit **`config.yaml`** – add the model(s) you want to watch:

```yaml
targets:
  - model: "HD785"       # model number to search for (required)
    type: "Rigid"        # label for your reference only
    year_min: 2015       # optional
    price_max: 1000000   # optional
```

### 3. Set up email alerts

Copy `.env.example` → `.env` and fill in your Gmail credentials:

```
EMAIL_FROM=yourbot@gmail.com
EMAIL_TO=team@yantralive.com
EMAIL_PASSWORD=xxxx xxxx xxxx xxxx   # Gmail App Password
ENQUIRY_PHONE=+91XXXXXXXXXX
ENQUIRY_EMAIL=contact@yantralive.com
```

> **Gmail App Password**: Google Account → Security → 2-Step Verification →
> App passwords. Generate one for "Mail".

### 4. Start the watcher

```bash
python3 main.py watch
```

The bot runs immediately, then every 60 minutes (configurable in config.yaml).

---

## All Commands

| Command | What it does |
|---------|-------------|
| `python3 main.py watch` | Start hourly watcher (runs forever) |
| `python3 main.py check` | Run one check right now |
| `python3 main.py list` | Show all listings found so far |
| `python3 main.py history` | Show watcher run log |
| `python3 main.py enquiry <ID>` | Auto-fill enquiry form for a listing |

### Enquiry command flags

```bash
python3 main.py enquiry 986999 \
    --phone "+91XXXXXXXXXX" \
    --email "contact@yantralive.com" \
    --message "We are interested in this machine. Please contact us."
```

By default the browser opens and **waits for you to press Enter** before
submitting. Add `--auto-submit` to click SEND ENQUIRY automatically.

---

## How it works

```
komatsu.com.au/equipment/used-equipment
        │
        └── embeds iframe from tradeearthmovers.com.au
                │
                ├── Bot fetches HTML directly (no browser needed for watching)
                ├── Parses __NEXT_DATA__ JSON from the SSR page
                ├── Filters by: model keyword, year range, price range
                ├── Compares with local SQLite DB (seen_listings table)
                └── NEW listing → Agent 2 sends email alert
```

**Enquiry flow**

```
python3 main.py list          # find the listing ID
python3 main.py enquiry <ID>  # opens real browser
                              # fills: Name=YANTRA LIVE, Phone, Email, Message
                              # press Enter → SEND ENQUIRY clicked
```

---

## Project Structure

```
komatsu-bot/
├── main.py                   # CLI entry point
├── config.yaml               # Watch targets & settings
├── .env                      # Secrets (never commit this)
├── requirements.txt
│
├── agents/
│   ├── watcher.py            # Agent 1: fetch + filter + detect new
│   └── informer.py           # Agent 2: alert via email + terminal
│
├── bot/
│   ├── scraper.py            # HTTP scraper (parses __NEXT_DATA__)
│   └── enquiry.py            # Playwright form filler
│
├── db/
│   └── database.py           # SQLite – seen listings + run log
│
└── notifications/
    └── email_notifier.py     # HTML email via Gmail SMTP
```

---

## Tips

- **India time zone**: The bot checks every 60 minutes around the clock.
  Email alerts will reach your phone even at midnight IST.
- **Multiple models**: Add as many `targets` entries as you need in config.yaml.
- **Adjust interval**: Change `watcher.interval_minutes` in config.yaml.
- **No email yet?** The bot still prints alerts in the terminal — email is optional.

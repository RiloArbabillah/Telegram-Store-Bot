# Free-Telegram-Store-Bot

I made this Bot Free 100%.

> Message me at [@InDMDev](https://t.me/InDMDev) for your advanced bot customizations.
> For more Bots like this, and to be the first to know when I publish more advanced bots, join my channel: [@InDMDevBots](https://t.me/InDMDevBots)
Telegram bot for selling digital products: · sell software license keys on Telegram · Telegram shop/store bot · crypto payment bot · CryptoBot integration · Telegram Payments card checkout · automated digital delivery · Python e-commerce bot · python-telegram-bot store · SQLAlchemy SQLite Telegram bot · self-hosted digital goods storefront.
> 
# Digital Products Store — Telegram Bot

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![python-telegram-bot](https://img.shields.io/badge/python--telegram--bot-20.7-26A5E4?logo=telegram&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-D71F00?logo=sqlalchemy&logoColor=white)
![SQLite](https://img.shields.io/badge/database-SQLite%20%7C%20PostgreSQL-003B57?logo=sqlite&logoColor=white)
![Platform](https://img.shields.io/badge/OS-Windows%20%7C%20Linux%20%7C%20macOS-555)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Telegram bot for selling digital products (software license keys and downloadable files).
Customers browse a catalog, top up an internal wallet with crypto or a card, and spend that balance on products.
License keys are automatically delivered from inventory; file products are delivered via download links.
A protected web admin panel and the in-Telegram admin menu handle products, categories, stock, orders, disputes, users, broadcasts, and store settings.

Built with **Python**, **python-telegram-bot v20** (async), and **SQLAlchemy** (SQLite by default).

---

<img width="434" height="501" alt="image" src="https://github.com/user-attachments/assets/45c50008-6b86-4d0c-b0a9-d329b492862b" />

## Table of Contents

1. [Features](#features)
2. [Tech Stack](#tech-stack)
3. [Project Structure](#project-structure)
4. [Prerequisites](#prerequisites)
5. [Step 1 — Get your Telegram credentials](#step-1--get-your-telegram-credentials)
6. [Step 2 — Clone the repository](#step-2--clone-the-repository)
7. [Step 3 — Create a virtual environment](#step-3--create-a-virtual-environment)
8. [Step 4 — Install dependencies](#step-4--install-dependencies)
9. [Step 5 — Configure environment variables](#step-5--configure-environment-variables)
10. [Step 6 — Run the bot](#step-6--run-the-bot)
11. [Step 7 — Use the bot (`/start` and `/admin`)](#step-7--use-the-bot-start-and-admin)
12. [Deploy to Coolify with Docker](#deploy-to-coolify-with-docker)
13. [Optional — Real-time CryptoBot webhooks](#optional--real-time-cryptobot-webhooks)
14. [Optional — Keep the bot running 24/7](#optional--keep-the-bot-running-247)
15. [Database notes](#database-notes)
16. [Troubleshooting](#troubleshooting)
17. [Security notes](#security-notes)

---

## Features

- 🛒 Product catalog with categories and subcategories
- 🔑 Two product types: **license keys** (auto-delivered from inventory) and **downloadable files** (delivered as links)
- 💰 Internal wallet — users top up, then spend the balance on purchases
- 💳 Two top-up methods, both optional and independently toggled by config:
  - **CryptoBot** — pay with any cryptocurrency via [@CryptoBot](https://t.me/CryptoBot)
  - **Card** — native in-Telegram card payments via Telegram Payments
- 🛠 Full in-Telegram **admin panel**: products, categories, stock/restock, orders, disputes, users (ban/unban), broadcasts, and store settings
- 🌐 Responsive web admin panel with one-time login links issued only to `ADMIN_TELEGRAM_ID`
- ⏱ Background jobs for payment verification and periodic availability broadcasts

## Tech Stack

| Component | Version |
|-----------|---------|
| Python | 3.10+ recommended (3.9+ supported) |
| python-telegram-bot | 20.7 |
| SQLAlchemy | 2.0.23 |
| Database | SQLite (default) or PostgreSQL |

---

**How it fits together:** `bot.py` is the single wiring point — it validates config (`config/`), initializes the database (`database/`), then registers all the `handlers/`. Handlers talk to Telegram and call into `services/` (external APIs) and `utils/` (keyboards + helpers); all data access goes through `get_db_session()` in `database/db.py`.

---

## Prerequisites

Install these before you start:

- **Git** — [git-scm.com/downloads](https://git-scm.com/downloads)
- **Python 3.10+** — [python.org/downloads](https://www.python.org/downloads/)
  - On **Windows**, tick **“Add Python to PATH”** in the installer.
- A **Telegram account**

Verify your tools are installed:

**Windows (PowerShell):**
```powershell
git --version
python --version
```

**Linux / macOS:**
```bash
git --version
python3 --version
```

---

## Step 1 — Get your Telegram credentials

You need a **bot token** and your **admin Telegram ID**. The two payment keys are optional.

### 1a. Bot token (required)
1. Open [@BotFather](https://t.me/BotFather) in Telegram.
2. Send `/newbot` and follow the prompts (choose a name and a username ending in `bot`).
3. Copy the **API token** it gives you (looks like `1234567890:ABCdef...`).

### 1b. Your admin Telegram ID (required)
1. Open [@userinfobot](https://t.me/userinfobot) in Telegram.
2. Send any message; it replies with your numeric **Id** (e.g. `123456789`).
3. This ID is the only account that can access `/admin`.

### 1c. CryptoBot API key (optional — enables crypto top-ups)
1. Open [@CryptoBot](https://t.me/CryptoBot) → **Crypto Pay** → **My Apps** → create an app.
2. Copy the **API token**. Leave blank to disable the CryptoBot option.

### 1d. Telegram Payments provider token (optional — enables card top-ups)
1. Open [@BotFather](https://t.me/BotFather) → select your bot → **Payments**.
2. Connect a payment provider and copy the **provider token**. Leave blank to disable the Card option.
   > Card-provider availability is region-dependent — pick a provider supported in your country. Use the provider’s **TEST** token while developing.

---

## Step 2 — Clone the repository

**Windows (PowerShell) and Linux / macOS** (same commands):
```bash
git clone <YOUR_REPOSITORY_URL>
cd FreeTelegramStoreBot
```
> Replace `<YOUR_REPOSITORY_URL>` with your repo’s clone URL, and `FreeTelegramStoreBot` with the folder name if it differs.

---

## Step 3 — Create a virtual environment

A virtual environment keeps this project’s dependencies isolated.

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```
> If activation is blocked by execution policy, run once:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned.`
> (or use the CMD activator: `venv\Scripts\activate.bat`).

**Linux / macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
```

When active, your shell prompt is prefixed with `(venv)`. To leave it later, run `deactivate`.

---

## Step 4 — Install dependencies

With the virtual environment active:

**Windows (PowerShell):**
```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

**Linux / macOS:**
```bash
python3 -m pip install --upgrade pip
pip install -r requirements.txt
```

---

## Step 5 — Configure environment variables

Copy the example file to a real `.env` and fill in your values.

**Windows (PowerShell):**
```powershell
Copy-Item .env.example .env
notepad .env
```

**Linux / macOS:**
```bash
cp .env.example .env
nano .env
```

Fill in the variables:

| Variable | Required | Description |
|----------|:--------:|-------------|
| `BOT_TOKEN` | ✅ | Bot token from [@BotFather](https://t.me/BotFather) (Step 1a). |
| `ADMIN_TELEGRAM_ID` | ✅ | Your numeric Telegram ID (Step 1b). The only admin account. |
| `ADMIN_TELEGRAM_USERNAME` | ➖ | Your username without `@` (used in some messages). |
| `ADMIN_SESSION_SECRET` | ✅ | Random secret of at least 32 characters used to sign the 24-hour web admin session. Generate one with `openssl rand -hex 32`. |
| `ADMIN_COOKIE_SECURE` | ➖ | Keep `true` on HTTPS deployments. Use `false` only for local HTTP access. |
| `DATABASE_URL` | ➖ | Defaults to `sqlite:///bot_database.db`. Set a PostgreSQL URL to use Postgres. |
| `WEBHOOK_BASE_URL` | ➖ | Public HTTPS origin used to display provider callback endpoints, without a path. |
| `PORT` | ➖ | Webhook HTTP port used by Docker/Gunicorn. Defaults to `3000`. |
| `CRYPTO_BOT_API_KEY` | ➖ | CryptoBot Crypto Pay token (Step 1c). Blank disables crypto top-up. |
| `TELEGRAM_PROVIDER_TOKEN` | ➖ | Telegram Payments provider token (Step 1d). Blank disables card top-up. |
| `PAYMENT_CURRENCY` | ➖ | Business currency for the whole app (default `IDR`). All wallet, product, order, and top-up amounts are treated as whole rupiah. |
| `MAILBOX_SEARCH_KEYWORD` | ➖ | Mailbox keyword for the OTP checker (default `openai`). |
| `MAILBOX_DEACTIVATED_SEARCH_KEYWORD` | ➖ | Mailbox keyword for deactivation notices (default `deactivated`). |
| `DANA_API_MODE` | ➖ | Set to `disabled` to use manual QRIS fallback, or any non-disabled value to enable DANA QRIS when required config is filled. |
| `DANA_BASE_URL` | ➖ | DANA API base URL. Use `https://api.sandbox.dana.id` for sandbox and `https://api.saas.dana.id` for production. |
| `DANA_PARTNER_ID` | ➖ | DANA partner/client ID. |
| `DANA_CHANNEL_ID` | ➖ | Optional channel ID header sent to DANA. |
| `DANA_MERCHANT_ID` | ➖ | DANA merchant ID. |
| `DANA_STORE_ID` | ➖ | DANA store ID. |
| `DANA_SUB_MERCHANT_ID` | ➖ | Optional DANA sub-merchant/external division ID. |
| `DANA_TERMINAL_ID` | ➖ | Optional terminal ID. |
| `DANA_PRIVATE_KEY_PATH` | ➖ | Path to the merchant RSA private key PEM used for outbound request signing. |
| `DANA_PUBLIC_KEY_PATH` | ➖ | Path to the DANA public key PEM used for callback signature verification. |
| `DANA_CALLBACK_URL` | ➖ | The reachable HTTPS callback URL registered in the DANA dashboard. |

> The Docker service will not start until `BOT_TOKEN`, `ADMIN_TELEGRAM_ID`, `WEBHOOK_BASE_URL`, and a strong `ADMIN_SESSION_SECRET` are configured.

---

## Step 6 — Run the bot

The database is created and seeded automatically on first run — there is no separate setup command.

**Windows (PowerShell):**
```powershell
python bot.py
```

**Linux / macOS:**
```bash
python3 bot.py
```

You should see log lines ending with:
```
Bot started successfully!
```
Leave this terminal open — the bot runs as long as the process is running. Press **Ctrl+C** to stop it.

---

## Step 7 — Use the bot (`/start` and `/admin`)

With the bot running:

1. Open Telegram and search for your bot by the username you chose in Step 1a.
2. Send **`/start`** — you’ll get the welcome message and the main menu (Products, Top Up, Order History, Availability, Support).
3. Send **`/admin`** — if your Telegram ID matches `ADMIN_TELEGRAM_ID`, the admin menu opens.
4. Tap **Buat OTP Panel**. The bot sends an 8-digit OTP that works once and expires after 5 minutes.
5. Open `/admin/login` on your web panel domain, enter the OTP, and submit the form.
6. The web session remains active for 24 hours. Use **Keluar** on shared devices.

> If `/admin` says access is denied or does nothing, your `ADMIN_TELEGRAM_ID` doesn’t match your account — recheck Step 1b, fix `.env`, and restart the bot.

The web panel is served at `/admin` on the same domain and port as the payment callbacks. Keep `/health` public for container health checks. Do not run more than one polling bot replica because Telegram accepts only one active `getUpdates` consumer per token.

**🎉 That’s it — your bot is live.** A typical first run as admin: open `/admin` → **Product Management** → create a category, then a product, then **Restock Keys** to add inventory. As a user, `/start` → **Top Up** to fund the wallet, then buy a product.

---

## Optional — DANA QRIS

By default, QRIS uses the manual fallback flow: admin-managed instructions, user proof upload, and manual admin approval.

To use DANA QRIS via API + callback:

1. Set `DANA_API_MODE` to any non-disabled value once your credentials are ready.
2. Fill the required DANA env values (`DANA_PARTNER_ID`, `DANA_MERCHANT_ID`, `DANA_STORE_ID`, `DANA_PRIVATE_KEY_PATH`, `DANA_PUBLIC_KEY_PATH`, `DANA_CALLBACK_URL`, etc.).
3. Register the exact HTTPS callback URL from your deployment in the DANA dashboard.
4. Expose the webhook endpoint `POST /webhook/dana` from `webhook_server.py` on that URL.
5. Restart the bot. When DANA config is complete, the QRIS top-up path switches from manual mode to DANA mode automatically.

If the required DANA values are incomplete or `DANA_API_MODE=disabled`, QRIS automatically falls back to the current manual flow.

---

## Deploy to Coolify with Docker

The included `Dockerfile` runs both long-lived services in one container:

- The Telegram bot receives updates through long polling.
- Gunicorn exposes the payment webhook server on `PORT`.

### 1. Create PostgreSQL

Create a PostgreSQL resource in the same Coolify project or environment. Use its internal hostname and credentials to build the SQLAlchemy connection URL:

```dotenv
DATABASE_URL=postgresql+psycopg://tele_store_bot:your_password@postgres:5432/tele_store_bot
```

Replace every value with the credentials and internal hostname shown by Coolify. Do not use `localhost`, because PostgreSQL runs in a different container.
URLs generated by Coolify with a `postgres://` or `postgresql://` prefix are normalized automatically to the installed `psycopg` driver.

### 2. Create the application

Create a new Coolify application from this repository and select **Dockerfile** as the build pack. The image starts both processes automatically; do not override its start command.

Configure at least these environment variables in Coolify:

```dotenv
BOT_TOKEN=your_botfather_token
ADMIN_TELEGRAM_ID=123456789
ADMIN_TELEGRAM_USERNAME=your_username
DATABASE_URL=postgresql+psycopg://tele_store_bot:your_password@postgres:5432/tele_store_bot
WEBHOOK_BASE_URL=https://bot.example.com
PORT=3000
```

Add payment and DANA variables from `.env.example` only when those integrations are used. Environment values are loaded directly; a `.env` file is not copied into the Docker image.

### 3. Configure domain and health check

Attach your public HTTPS domain to the application and route it to container port `3000` (or the same value configured in `PORT`). Configure Coolify's health check as:

```text
GET /health
```

After deployment, `https://bot.example.com/health` must return JSON with `"status": "ok"`.

### 4. Register provider callbacks

`WEBHOOK_BASE_URL` only constructs callback addresses. The application does not register or modify provider configuration automatically. Register the relevant URLs in each provider dashboard:

```text
https://bot.example.com/webhook/cryptobot
https://bot.example.com/webhook/dana
https://bot.example.com/webhook/payment-deka
```

When DANA API mode is enabled, set `DANA_CALLBACK_URL` to the full DANA endpoint, for example `https://bot.example.com/webhook/dana`.

### 5. Optional media persistence

Catalog, user, and transaction data are stored in PostgreSQL. If the bot must retain files written locally between redeploys, add Coolify persistent storage for:

```text
/app/uploads
/app/assets
```

Telegram `file_id` values and remote download links stored in PostgreSQL do not need these volumes.

---

## Run locally with Docker Compose

Docker Compose runs the bot, webhook server, and PostgreSQL together. PostgreSQL is only reachable inside the Compose network; the application is available on port `3000`.

1. Create the local environment file and set at least `BOT_TOKEN` and `ADMIN_TELEGRAM_ID`:

   ```bash
   cp .env.example .env
   ```

2. Set a local PostgreSQL password in `.env`:

   ```dotenv
   POSTGRES_DB=tele_store_bot
   POSTGRES_USER=tele_store_bot
   POSTGRES_PASSWORD=replace_with_a_local_password
   APP_PORT=3000
   ```

   Compose constructs `DATABASE_URL` from these values automatically.

3. Build and start both containers:

   ```bash
   docker compose up -d --build
   ```

4. Check status and health:

   ```bash
   docker compose ps
   curl http://localhost:3000/health
   ```

5. Follow logs or stop the stack:

   ```bash
   docker compose logs -f app
   docker compose down
   ```

Database and local media remain in named Docker volumes. To intentionally delete all local data, run `docker compose down -v`.

---

## Optional — Real-time CryptoBot webhooks

By default, CryptoBot payments are confirmed by polling every ~30 seconds (no extra setup). For **instant** confirmation, run the included webhook server alongside the bot.

1. Start the webhook server (separate terminal, same virtual environment):

   **Windows (PowerShell):**
   ```powershell
   python webhook_server.py
   ```
   **Linux / macOS:**
   ```bash
   python3 webhook_server.py
   ```
   It listens on port **3000**.

2. Expose it over HTTPS (e.g. with [ngrok](https://ngrok.com/)):
   ```bash
   ngrok http 3000
   ```

3. In [@CryptoBot](https://t.me/CryptoBot) → **Crypto Pay → My Apps → Webhooks**, set the URL to:
   ```
   https://<your-ngrok-or-domain>/webhook/cryptobot
   ```

> On Windows, you can launch the bot and the webhook server together with `start_with_webhooks.bat` (you still run ngrok yourself).
> Card payments need no webhook — Telegram delivers their confirmation through the bot’s normal update polling.

---

## Optional — Keep the bot running 24/7

### Linux (systemd)

Create `/etc/systemd/system/digitalstore-bot.service` (adjust paths and `User`):

```ini
[Unit]
Description: Digital Products Store Telegram Bot
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/home/youruser/FreeTelegramStoreBot
ExecStart=/home/youruser/FreeTelegramStoreBot/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then enable and start it:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now digitalstore-bot
sudo systemctl status digitalstore-bot      # check it's running
journalctl -u digitalstore-bot -f            # follow logs
```

### Windows
Keep the `python bot.py` window open, or run it as a background/scheduled task (e.g. Task Scheduler), or host it on a Linux server using the steps above.

---

## Database notes

- **Default:** SQLite, stored in `bot_database.db` in the project folder. Created automatically on first run.
- **Backup:** simply copy the `bot_database.db` file.
- **Reset (deletes all data):** stop the bot, delete `bot_database.db`, and start the bot again to recreate an empty database.

  **Windows (PowerShell):**
  ```powershell
  Remove-Item bot_database.db
  ```
  **Linux / macOS:**
  ```bash
  rm bot_database.db
  ```
- **PostgreSQL (optional):** set `DATABASE_URL` to a Postgres URL, e.g.
  `postgresql+psycopg://user:password@localhost:5432/digitalstore`
  (The `psycopg` driver is already in `requirements.txt`).
- **Upgrading an older database:** if you’re migrating an existing SQLite DB created before category fields were made optional, run once:
  `python migrations/categorynullable.py` (not needed for fresh installs).

---

## FAQ

**What is this project?**
An open-source, self-hosted **Telegram bot for selling digital products** — software license/activation keys and downloadable files — with a customer-facing storefront and a full admin panel, all inside Telegram.

**What can I sell with it?**
Anything digital: software license keys, game keys, gift-card codes, e-books, PDFs, courses, templates, or any downloadable file delivered via a link.

**How do customers pay?**
Customers fund an in-bot **wallet**, then spend the balance on purchases. Top-ups are supported via **CryptoBot** (any cryptocurrency) and **card payments** (Telegram Payments). Both methods are optional and toggled by config.

**Is delivery automatic?**
Yes. License keys are assigned automatically from your inventory the moment a purchase is confirmed; file products are delivered as a download link — no manual fulfillment.

**Do I need to know how to code to run it?**
No. Clone the repo, fill in a `.env` file, and run one command. The database is created automatically on first launch.

**Which database does it use?**
**SQLite** by default (zero setup). You can switch to **PostgreSQL** by changing a single environment variable.

**Does it work on Windows and Linux?**
Yes — the [setup guide](#table-of-contents) has step-by-step commands for **Windows, Linux, and macOS**, plus a `systemd` service for 24/7 hosting.

**Is it free and open source?**
Yes — released under the [MIT License](LICENSE).

---
## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Configuration error: BOT_TOKEN is required` | `.env` is missing or `BOT_TOKEN`/`ADMIN_TELEGRAM_ID` is empty. Recheck Step 5 and that `.env` is in the project root. |
| `/admin` denied or no response | `ADMIN_TELEGRAM_ID` doesn’t match your account. Re-get your ID (Step 1b), update `.env`, restart. |
| `ModuleNotFoundError` / import errors | The virtual environment isn’t active or deps aren’t installed. Re-do Step 3 and Step 4. |
| `python` not found (Windows) | Reinstall Python with **“Add Python to PATH”** ticked, or use the `py` launcher (`py bot.py`). |
| Activation blocked (Windows) | `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`, then re-activate. |
| Card button shows “not configured” | `TELEGRAM_PROVIDER_TOKEN` is blank or invalid — see Step 1d. |
| Crypto top-up not auto-confirming | Verify `CRYPTO_BOT_API_KEY`, check the console for API errors, or set up webhooks for instant confirmation. |
| Bot stops when you close the terminal | That’s expected — use the [24/7 section](#optional--keep-the-bot-running-247). |


## License

Released under the [MIT License](LICENSE).

> ⚠️ **Note: Use this program only for legal purposes.**
> InDMDev is not and will not be responsible for any illegal activity/activities you indulge in using any of our programs.

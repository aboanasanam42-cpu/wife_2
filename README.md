# MEXC 24/7 Spot Algorithmic Trading Bot (Railway Ready)

A production-ready, fault-tolerant, modular Python application engineered for continuous automated Spot Trading on MEXC Global. Optimized for zero-downtime deployment on [Railway](https://railway.app) via automated GitHub CI/CD pipelines.

---

## 🏛️ Architecture & System Design

```
                     ┌───────────────────────────┐
                     │   Railway Worker Daemon   │
                     │       (Procfile)          │
                     └─────────────┬─────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
     ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
     │  MEXC Spot API  │  │ Strategy Engine │  │    Telegram     │
     │     (CCXT)      │  │  (RSI + 20 EMA) │  │  Async Notifier │
     └────────┬────────┘  └────────┬────────┘  └────────┬────────┘
              │                    │                    │
              └────────────────────┼────────────────────┘
                                   ▼
                       ┌───────────────────────┐
                       │ State Persistence     │
                       │   (bot_state.json)    │
                       └───────────────────────┘
```

- **Strict Spot Isolation:** Connects via `ccxt` with `defaultType: "spot"` and `enableRateLimit: True`. Contract and Futures endpoints are disabled.
- **Closed Candle Integrity:** Drops the incomplete in-progress candle so indicators (Wilder's RSI + 20-period EMA) calculate exclusively on finalized market data.
- **Risk Management:** Programmatic Stop-Loss (e.g., -2.0%) and Take-Profit (e.g., +4.0%) evaluated dynamically per cycle.
- **State Recovery:** Persists active position entries, order IDs, and risk thresholds to `bot_state.json`, ensuring recovery even if Railway restarts or updates the container.
- **Graceful Termination:** Listens for `SIGINT` and `SIGTERM` signals to cleanly save state and alert Telegram before shutting down.

---

## 📂 Repository File Structure

```
├── config.py             # Strongly-typed configuration parser & validator
├── exchange_client.py    # Authenticated MEXC Spot API client with order execution & error handling
├── strategy.py           # Technical analysis, indicators calculation, and signal engine
├── notifier.py           # Threaded Telegram alerts dispatcher
├── main.py               # Main 24/7 worker loop & process lifecycle manager
├── requirements.txt      # Locked production dependencies
├── Procfile              # Railway worker process specification
├── .env.example          # Environment variables template
└── README.md             # Architecture & deployment documentation
```

---

## 🚀 Fast Deployment Guide to Railway

### Step 1: Create a GitHub Repository
1. Initialize a git repository and commit all files:
   ```bash
   git init
   git add .
   git commit -m "feat: initial mexc spot trading bot"
   git branch -M main
   git remote add origin https://github.com/<your-username>/mexc-spot-bot.git
   git push -u origin main
   ```

### Step 2: Set Up MEXC API Credentials
1. Log into your [MEXC Account](https://www.mexc.com).
2. Navigate to **User Profile** → **API Management**.
3. Click **Create API**:
   - Enable **Spot Trade** and **Read** permissions.
   - ⚠️ **CRITICAL:** Ensure **Withdrawal** is **DISABLED**.
   - (Recommended) Bind your Railway static outbound IP if available, or set 90-day validity.
4. Save your `API Key` and `API Secret`.

### Step 3: (Optional) Create Telegram Bot for Real-time Alerts
1. Open Telegram and search for [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, choose a name and username, and copy the `HTTP API Token`.
3. Start a chat with your new bot and send any test message.
4. Obtain your numerical Chat ID via [@userinfobot](https://t.me/userinfobot) or `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`.

### Step 4: Deploy on Railway
1. Log in to [Railway.app](https://railway.app).
2. Click **New Project** → **Deploy from GitHub repo**.
3. Select your `mexc-spot-bot` repository.
4. Navigate to the **Variables** tab of the service and add:

| Variable Name | Description | Example Value |
|---|---|---|
| `MEXC_API_KEY` | MEXC Spot API Key | `mx0vglYourKey...` |
| `MEXC_API_SECRET` | MEXC Spot Secret | `yourSecretHere...` |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | `123456789:ABC...` |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID | `987654321` |
| `TRADE_SYMBOL` | Spot Pair | `BTC/USDT` |
| `TRADE_AMOUNT_USDT` | Allocation per trade | `15.0` |
| `TIMEFRAME` | Candle interval | `15m` |
| `POLL_INTERVAL_SECONDS`| Loop wait time | `30` |
| `STOP_LOSS_PCT` | Stop-Loss percentage | `0.02` (2%) |
| `TAKE_PROFIT_PCT` | Take-Profit percentage | `0.04` (4%) |
| `SIMULATION_MODE` | Dry run flag | `False` (or `True` to test) |

5. Railway will automatically detect the `Procfile` (`worker: python main.py`) and launch the 24/7 background worker.
6. Open the **Deploy Logs** to inspect live startup and candle evaluation cycles.

---

## 🧪 Local Testing & Dry Run (Simulation)

To test the bot locally on your machine before enabling real money:

```bash
# Clone and setup virtualenv
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Set SIMULATION_MODE=True in .env

# Run worker
python main.py
```

---

## 🔒 Security Best Practices
- **Never commit `.env` or API credentials** to Git. Keep `.env` inside `.gitignore`.
- Always set `SIMULATION_MODE=True` first when modifying trading strategies or indicators.
- Regularly review MEXC API logs and Telegram execution alerts.

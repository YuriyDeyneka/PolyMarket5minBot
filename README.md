# ⚡ Polymarket BTC 5-Min Trader

An [OpenClaw](https://openclaw.ai) skill for trading Bitcoin 5-minute prediction markets on [Polymarket](https://polymarket.com/crypto/5M).

Handles the full trade lifecycle — dynamic market discovery, order book analysis, and order execution — all from a single chat message to your bot.

---

## How It Works

The BTC 5-minute market series is unique: the `tokenID` changes every 5 minutes as new windows open. This skill **never hardcodes a tokenID** — it fetches the active one dynamically before every action.

Each trade goes through three phases:

```
/btc5m buy $25
    │
    ├─ 1. DISCOVER   Gamma API → finds active window → extracts YES/NO tokenIDs
    │
    ├─ 2. BOOK       CLOB API → walks order book → calculates real fill cost + slippage
    │
    ├─ 3. CONFIRM    Bot shows you the order summary before touching your money
    │
    └─ 4. EXECUTE    py-clob-client → signs + posts order → returns order ID
```

---

## Requirements

- [OpenClaw](https://openclaw.ai) installed and running
- Python 3.9+
- A [Polymarket](https://polymarket.com) account with USDC.e balance on Polygon
- Your Polymarket wallet private key

```bash
pip install py-clob-client requests
```

---

## Installation

```bash
# 1. Create the skill folder
mkdir -p ~/.openclaw/skills/polymarket-btc-5m

# 2. Copy all 4 files into it
cp SKILL.md btc5m_trader.py config.json _meta.json \
   ~/.openclaw/skills/polymarket-btc-5m/

# 3. Install dependencies
pip install py-clob-client requests

# 4. Add credentials to OpenClaw (see below)
```

---

## Credentials

Add your keys to `~/.openclaw/openclaw.json`. **Never put your private key inside the skill folder.**

```json
{
  "skills": {
    "entries": {
      "polymarket-btc-5m": {
        "enabled": true,
        "env": {
          "POLY_PRIVATE_KEY": "0x...",
          "POLY_FUNDER":      "0x...",
          "POLY_SIG_TYPE":    "1"
        }
      }
    }
  }
}
```

| Variable | Where to get it | Notes |
|---|---|---|
| `POLY_PRIVATE_KEY` | [reveal.polymarket.com](https://reveal.polymarket.com) (email) or MetaMask export | Signs your orders |
| `POLY_FUNDER` | Your Polymarket deposit address (shown on profile) | The address holding your USDC.e |
| `POLY_SIG_TYPE` | — | `1` = email/Magic login · `0` = MetaMask/hardware wallet |

---

## Usage

### Via your OpenClaw bot (Telegram / WhatsApp / Discord)

| Command | What it does |
|---|---|
| `/btc5m` | Discover active window + show order book snapshot |
| `/btc5m buy $25` | Guided BUY YES flow with confirmation |
| `/btc5m sell $25` | Guided BUY NO flow with confirmation |
| `/btc5m limit 0.55 $50` | Place GTC limit order at $0.55 |
| `/btc5m orders` | List your open orders |
| `/btc5m cancel <id>` | Cancel an order by ID |

### Via CLI directly

```bash
cd ~/.openclaw/skills/polymarket-btc-5m

# Dry run — discover + book (no money spent)
python btc5m_trader.py

# Show order book only
python btc5m_trader.py --book

# Market buy (FOK — fills immediately or cancels)
python btc5m_trader.py --live --side BUY --size 25 --type FOK

# Limit buy (GTC — rests on the book at your price)
python btc5m_trader.py --live --side BUY --price 0.55 --size 50 --type GTC

# Buy NO (bet BTC goes down)
python btc5m_trader.py --live --side SELL --size 25 --type FOK

# List open orders
python btc5m_trader.py --orders

# Cancel an order
python btc5m_trader.py --cancel <order_id>

# View / change config
python btc5m_trader.py --config
python btc5m_trader.py --set default_size=50
python btc5m_trader.py --set order_type=FOK
```

---

## Configuration

Edit `config.json` or use `--set` from the CLI or bot:

| Setting | Default | Description |
|---|---|---|
| `default_size` | `25.0` | Default USDC amount per trade |
| `order_type` | `GTC` | Default order type (`GTC` or `FOK`) |
| `slippage_warn` | `3.0` | Warn user if slippage exceeds this % |
| `slippage_block` | `5.0` | Block trade if slippage exceeds this % (use `--force` to override) |
| `min_time_remaining` | `30` | Refuse to trade if window closes in fewer than this many seconds |
| `sig_type` | `1` | Signature type (`1` = email, `0` = MetaMask) |

---

## Order Types

| Type | You are | Behavior | When to use |
|---|---|---|---|
| `GTC` (Good-Til-Cancelled) | **Maker** | Rests on the book at your price. Fills when someone meets you. | You have a target price and can wait |
| `FOK` (Fill-Or-Kill) | **Taker** | Fills immediately at best available price, or cancels entirely. | You want in now, book slippage is acceptable |

---

## File Structure

```
~/.openclaw/skills/polymarket-btc-5m/
├── SKILL.md           Bot instructions & ClawHub metadata
├── btc5m_trader.py    Main script (discovery + book + execution)
├── config.json        Default settings
└── _meta.json         ClawHub registry metadata
```

---

## Troubleshooting

**"No active BTC 5-min market found"**
Markets briefly go offline between windows. Wait ~30 seconds and retry.

**"External wallet requires a pre-signed order"**
`POLY_PRIVATE_KEY` is missing from your environment. Check your `openclaw.json` env block.

**"Balance shows $0 but I have USDC on Polygon"**
Polymarket uses **USDC.e** (bridged USDC, contract `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`) — not native USDC. Swap native USDC → USDC.e on Polygon first.

**"FOK order not filled"**
The book moved before your order landed. Check book depth with `--book` and try a GTC limit order instead.

**Slippage blocked**
Your size is too large for available liquidity. Reduce `--size` or use `--force` to override (not recommended).

---

## Safety Rules

The bot enforces these automatically and will not bypass them without explicit instruction:

1. Always discovers a fresh tokenID before every action — never uses a cached one
2. Always shows the order book before execution
3. Refuses to trade if the window closes in fewer than `min_time_remaining` seconds
4. Blocks trades with slippage above `slippage_block` %
5. Always confirms the full order summary before posting
6. Never exposes the raw tokenID to the user — always labels YES or NO clearly

---

## Disclaimer

This skill executes real trades with real money. Always test with small sizes first. Prediction markets are speculative and you can lose your entire position. The 5-minute window means there is very little time to react if something goes wrong. Use responsibly.

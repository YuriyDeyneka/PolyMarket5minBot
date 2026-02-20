---
name: polymarket-btc-5m
displayName: Polymarket BTC 5-Min Trader
description: Trade Polymarket Bitcoin 5-minute prediction markets. Dynamically discovers the active tokenID each window, reads order book depth, calculates real fill cost, and executes BUY/SELL orders. Use when user wants to trade BTC fast/sprint markets, check current 5-min odds, or place limit/market orders on Polymarket.
metadata: {"clawdbot":{"emoji":"⚡","requires":{"env":["POLY_PRIVATE_KEY"],"pip":["py-clob-client","requests"]},"cron":null,"autostart":false}}
authors:
  - OpenBot
version: "1.0.0"
published: false
---

# Polymarket BTC 5-Min Trader

Trade the Bitcoin 5-minute prediction markets on Polymarket. The tokenID changes every 5 minutes — this skill always fetches it dynamically.

> ⚠️ The tokenID for each window expires in 5 minutes. NEVER hardcode a tokenID. ALWAYS run discovery first.

> **Dry run is the default.** Use `--live` for real trades.

## How It Works

1. **Discovery** — Queries Gamma API for the active BTC 5-min window and extracts YES/NO tokenIDs
2. **Book Check** — Walks the CLOB order book to calculate your real fill cost and slippage
3. **Execution** — Signs and posts the order via py-clob-client (GTC limit or FOK market)

## Quick Start
```bash
# Set credentials
export POLY_PRIVATE_KEY="0x..."
export POLY_FUNDER="0x..."      # your Polymarket deposit address
export POLY_SIG_TYPE="1"        # 1 = email/Magic login, 0 = MetaMask

# Dry run — discover + show book
python btc5m_trader.py

# Show book for current window
python btc5m_trader.py --book

# Place a limit buy (GTC — you are a Maker)
python btc5m_trader.py --live --side BUY --price 0.54 --size 50

# Place a market buy (FOK — you are a Taker)
python btc5m_trader.py --live --side BUY --size 25 --type FOK

# Sell (buy NO)
python btc5m_trader.py --live --side SELL --size 25 --type FOK

# Show your open orders
python btc5m_trader.py --orders

# Cancel an order
python btc5m_trader.py --cancel <order_id>
```

## Setup Flow

When user asks to install or configure this skill:

1. **Ask for wallet private key**
   - Get from: reveal.polymarket.com (email login) or MetaMask export
   - Store in environment as `POLY_PRIVATE_KEY`

2. **Ask for funder address**
   - This is the Polymarket deposit address shown on your profile
   - Store as `POLY_FUNDER`

3. **Ask for signature type**
   - Email/Magic login → `POLY_SIG_TYPE=1`
   - MetaMask/hardware wallet → `POLY_SIG_TYPE=0`

4. **Set preferences** (or confirm defaults):
   - Default trade size: $25
   - Order type: GTC (limit) or FOK (market)
   - Slippage warning threshold: 3%

## Configuration

Configure via `config.json`, environment variables, or `--set`:
```bash
python btc5m_trader.py --set default_size=50
python btc5m_trader.py --set slippage_warn=5
python btc5m_trader.py --set order_type=FOK
```

| Setting | Default | Env Var | Description |
|---------|---------|---------|-------------|
| `default_size` | 25.0 | `POLY_DEFAULT_SIZE` | Default USDC trade size |
| `order_type` | GTC | `POLY_ORDER_TYPE` | Default order type (GTC or FOK) |
| `slippage_warn` | 3.0 | `POLY_SLIPPAGE_WARN` | Warn if slippage exceeds this % |
| `slippage_block` | 5.0 | `POLY_SLIPPAGE_BLOCK` | Refuse trade if slippage exceeds this % |
| `min_time_remaining` | 30 | `POLY_MIN_TIME` | Refuse to trade if window closes in <N seconds |
| `sig_type` | 1 | `POLY_SIG_TYPE` | 0=MetaMask, 1=Email/Magic |

## Slash Commands

- `/btc5m` → discover + show book summary for current window
- `/btc5m buy $<amount>` → discover + book check + confirm + execute BUY YES
- `/btc5m sell $<amount>` → discover + book check + confirm + execute BUY NO
- `/btc5m limit <price> $<amount>` → place GTC limit order
- `/btc5m orders` → list open orders
- `/btc5m cancel <id>` → cancel an order

## Decision Rules (never break these)

1. Always run discovery before any other action — tokenIDs expire every 5 minutes
2. Always show the book before execution — never skip the cost/slippage check
3. If `seconds_remaining < min_time_remaining`, refuse the trade and say why
4. If `slippage > slippage_block`, refuse unless user explicitly overrides with `--force`
5. Always confirm order details before posting: side, token (YES/NO), price, size
6. Always label YES/NO clearly — never show just the raw tokenID to the user
7. If `POLY_PRIVATE_KEY` is not set, explain setup and refuse to trade

## Troubleshooting

**"No active BTC 5-min market found"**
- Markets may be between windows (last expired, next not live yet)
- Wait ~30 seconds and retry

**"External wallet requires a pre-signed order"**
- `POLY_PRIVATE_KEY` is not set — the client needs it to sign orders
- Fix: `export POLY_PRIVATE_KEY=0x<your-key>`

**"Balance shows $0 but I have USDC on Polygon"**
- Polymarket uses **USDC.e** (contract `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`)
- Swap native USDC → USDC.e on Polygon, then retry

**"FOK order not filled"**
- No matching orders at your price — book moved
- Try GTC limit order or check book depth first

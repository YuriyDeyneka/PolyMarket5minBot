#!/usr/bin/env python3
"""
Polymarket BTC 5-Min Trader

Dynamically discovers the active Bitcoin 5-minute market window,
reads the CLOB order book, and executes trades via py-clob-client.

The tokenID changes every 5 minutes — always fetched dynamically.

Usage:
    python btc5m_trader.py                              # dry run: discover + book
    python btc5m_trader.py --live --side BUY --size 25 --type FOK
    python btc5m_trader.py --live --side BUY --price 0.55 --size 50 --type GTC
    python btc5m_trader.py --book                       # show book only
    python btc5m_trader.py --orders                     # list open orders
    python btc5m_trader.py --cancel <order_id>
    python btc5m_trader.py --config                     # show config
    python btc5m_trader.py --set default_size=50        # update config

Requires:
    POLY_PRIVATE_KEY environment variable
    pip install py-clob-client requests
"""

import os, sys, json, argparse
from pathlib import Path
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

sys.stdout.reconfigure(line_buffering=True)

# =============================================================================
# Configuration
# =============================================================================

CONFIG_SCHEMA = {
    "default_size":       {"default": 25.0,  "env": "POLY_DEFAULT_SIZE",   "type": float},
    "order_type":         {"default": "GTC",  "env": "POLY_ORDER_TYPE",     "type": str},
    "slippage_warn":      {"default": 3.0,   "env": "POLY_SLIPPAGE_WARN",  "type": float},
    "slippage_block":     {"default": 5.0,   "env": "POLY_SLIPPAGE_BLOCK", "type": float},
    "min_time_remaining": {"default": 30,    "env": "POLY_MIN_TIME",       "type": int},
    "sig_type":           {"default": 1,     "env": "POLY_SIG_TYPE",       "type": int},
}

GAMMA_URL  = "https://gamma-api.polymarket.com/markets"
CLOB_URL   = "https://clob.polymarket.com"
CHAIN_ID   = 137
TAG_ID     = 100381
SEARCH_STR = "bitcoin price 5"


def load_config():
    cfg_path = Path(__file__).parent / "config.json"
    file_cfg = {}
    if cfg_path.exists():
        try:
            file_cfg = json.loads(cfg_path.read_text())
        except Exception:
            pass
    out = {}
    for k, spec in CONFIG_SCHEMA.items():
        if k in file_cfg:
            out[k] = file_cfg[k]
        elif spec.get("env") and os.environ.get(spec["env"]):
            raw = os.environ[spec["env"]]
            t = spec["type"]
            try:
                out[k] = t(raw)
            except Exception:
                out[k] = spec["default"]
        else:
            out[k] = spec["default"]
    return out


def update_config(updates):
    cfg_path = Path(__file__).parent / "config.json"
    existing = {}
    if cfg_path.exists():
        try:
            existing = json.loads(cfg_path.read_text())
        except Exception:
            pass
    existing.update(updates)
    cfg_path.write_text(json.dumps(existing, indent=2))
    return existing


cfg = load_config()

# =============================================================================
# HTTP Helper
# =============================================================================

def api_get(url, params=None, timeout=10):
    if params:
        from urllib.parse import urlencode
        url = f"{url}?{urlencode(params)}"
    try:
        req = Request(url, headers={"User-Agent": "poly-btc5m/1.0"})
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except HTTPError as e:
        return {"error": str(e), "status": e.code}
    except Exception as e:
        return {"error": str(e)}

# =============================================================================
# Phase 1 — Discovery
# =============================================================================

def discover():
    """Find the active BTC 5-min market and extract YES/NO tokenIDs."""
    params = {"active": "true", "closed": "false", "tag_id": TAG_ID, "limit": 50}
    markets = api_get(GAMMA_URL, params)

    if isinstance(markets, dict) and markets.get("error"):
        return None, f"Gamma API error: {markets['error']}"

    matches = [
        m for m in markets
        if SEARCH_STR in (m.get("question") or m.get("title") or "").lower()
    ]

    if not matches:
        return None, "No active Bitcoin 5-Min market found. Market may be between windows."

    def end_ts(m):
        raw = m.get("endDate") or m.get("end_date_iso") or ""
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except Exception:
            return float("inf")

    matches.sort(key=end_ts)
    m = matches[0]

    ids = m.get("clobTokenIds") or []
    if isinstance(ids, str):
        try:
            ids = json.loads(ids)
        except Exception:
            ids = []

    if len(ids) < 2:
        outcomes = m.get("outcomes") or []
        ids = [o.get("clobTokenId", "") for o in outcomes]

    if len(ids) < 2 or not ids[0]:
        return None, "Could not extract tokenIDs from market data"

    raw_end = m.get("endDate") or m.get("end_date_iso") or ""
    try:
        end_dt = datetime.fromisoformat(raw_end.replace("Z", "+00:00"))
        secs = max(0, int((end_dt - datetime.now(timezone.utc)).total_seconds()))
        end_str = end_dt.strftime("%H:%M:%S UTC")
    except Exception:
        secs, end_str = 999, "unknown"

    return {
        "title":             m.get("question") or m.get("title"),
        "yes_token":         ids[0],
        "no_token":          ids[1],
        "end_time":          end_str,
        "seconds_remaining": secs,
        "liquidity":         float(m.get("liquidityClob") or m.get("liquidity") or 0),
    }, None

# =============================================================================
# Phase 2 — Order Book
# =============================================================================

def fetch_book(token_id):
    return api_get(f"{CLOB_URL}/book", {"token_id": token_id})


def walk_book(asks, usdc_budget):
    total_shares, total_cost, remaining = 0.0, 0.0, usdc_budget
    for order in sorted(asks, key=lambda x: float(x.get("price", 1))):
        price = float(order.get("price", 0))
        size  = float(order.get("size", 0))
        if price <= 0 or size <= 0:
            continue
        level_cost = price * size
        if level_cost <= remaining:
            total_shares += size
            total_cost   += level_cost
            remaining    -= level_cost
        else:
            total_shares += remaining / price
            total_cost   += remaining
            remaining = 0
            break
    return total_shares, total_cost


def analyze_book(token_id, usdc_size):
    book = fetch_book(token_id)
    if not book or book.get("error"):
        return None, f"CLOB error: {book.get('error') if book else 'no response'}"

    asks = book.get("asks", [])
    bids = book.get("bids", [])

    best_ask  = float(asks[0]["price"]) if asks else None
    best_bid  = float(bids[0]["price"]) if bids else None
    ask_depth = sum(float(o["price"]) * float(o["size"]) for o in asks)

    shares, cost = walk_book(asks, usdc_size)
    avg_price = (cost / shares) if shares > 0 else None
    slippage  = 0.0
    if best_ask and avg_price:
        slippage = round(((avg_price - best_ask) / best_ask) * 100, 2)

    warnings = []
    if slippage > cfg["slippage_block"]:
        warnings.append(f"BLOCKED: slippage {slippage}% exceeds limit {cfg['slippage_block']}%. Use --force to override.")
    elif slippage > cfg["slippage_warn"]:
        warnings.append(f"High slippage: {slippage}%. Consider smaller size.")
    if cost < usdc_size * 0.99:
        warnings.append(f"Insufficient liquidity. Max fillable: ${cost:.2f}")

    return {
        "best_ask":     best_ask,
        "best_bid":     best_bid,
        "ask_depth":    round(ask_depth, 2),
        "shares":       round(shares, 4),
        "actual_cost":  round(cost, 4),
        "avg_price":    round(avg_price, 4) if avg_price else None,
        "slippage_pct": slippage,
        "warnings":     warnings,
        "blocked":      slippage > cfg["slippage_block"],
    }, None

# =============================================================================
# Phase 3 — CLOB Client & Execution
# =============================================================================

def get_client():
    try:
        from py_clob_client.client import ClobClient
    except ImportError:
        print("Error: py-clob-client not installed. Run: pip install py-clob-client")
        sys.exit(1)

    pk     = os.environ.get("POLY_PRIVATE_KEY", "")
    funder = os.environ.get("POLY_FUNDER", "")
    sig_t  = cfg["sig_type"]

    if not pk:
        print("Error: POLY_PRIVATE_KEY not set.")
        print("Get it from reveal.polymarket.com (email) or MetaMask export.")
        print("Set it in ~/.openclaw/openclaw.json under skills.entries.polymarket-btc-5m.env")
        sys.exit(1)

    client = ClobClient(
        CLOB_URL, key=pk, chain_id=CHAIN_ID,
        signature_type=sig_t,
        funder=funder if funder else None
    )
    try:
        client.set_api_creds(client.create_or_derive_api_creds())
    except Exception as e:
        print(f"Auth failed: {e}")
        print("Check POLY_PRIVATE_KEY and POLY_FUNDER.")
        sys.exit(1)
    return client


def place_gtc(client, token_id, side, price, size):
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY, SELL
    side_c = BUY if side == "BUY" else SELL
    signed = client.create_order(OrderArgs(token_id=token_id, price=price, size=size, side=side_c))
    return client.post_order(signed, OrderType.GTC)


def place_fok(client, token_id, side, usdc_amount):
    from py_clob_client.clob_types import MarketOrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY, SELL
    side_c = BUY if side == "BUY" else SELL
    signed = client.create_market_order(
        MarketOrderArgs(token_id=token_id, amount=usdc_amount, side=side_c, order_type=OrderType.FOK)
    )
    return client.post_order(signed, OrderType.FOK)


def list_orders(client):
    try:
        return client.get_orders()
    except Exception as e:
        return {"error": str(e)}


def cancel_order(client, order_id):
    try:
        return client.cancel(order_id)
    except Exception as e:
        return {"error": str(e)}

# =============================================================================
# Main
# =============================================================================

def run(args):
    if args.config:
        print("\n⚙️  Current Config:")
        for k, v in cfg.items():
            print(f"  {k}: {v}")
        print(f"\n  Config file: {Path(__file__).parent / 'config.json'}")
        return

    if args.orders:
        client = get_client()
        orders = list_orders(client)
        if isinstance(orders, dict) and orders.get("error"):
            print(f"Error: {orders['error']}")
            return
        print(f"\n📋 Open Orders ({len(orders)} total)")
        for o in orders:
            oid   = (o.get("id") or "")[:12]
            side  = o.get("side", "?")
            price = o.get("price", "?")
            orig  = o.get("original_size", "?")
            match = o.get("size_matched", "?")
            stat  = o.get("status", "?")
            print(f"  {oid}... | {side} {match}/{orig} shares @ ${price} | {stat}")
        return

    if args.cancel:
        client = get_client()
        resp   = cancel_order(client, args.cancel)
        if isinstance(resp, dict) and resp.get("error"):
            print(f"❌ Cancel failed: {resp['error']}")
        else:
            print(f"✅ Cancelled order {args.cancel}")
        return

    # Phase 1: Discovery
    print("\n🔍 Discovering active BTC 5-min market...")
    market, err = discover()
    if err:
        print(f"❌ {err}")
        return

    secs  = market["seconds_remaining"]
    min_t = cfg["min_time_remaining"]

    print(f"  Title:      {market['title']}")
    print(f"  YES token:  {market['yes_token'][:24]}...")
    print(f"  NO token:   {market['no_token'][:24]}...")
    print(f"  Closes in:  {secs}s  ({market['end_time']})")
    print(f"  Liquidity:  ${market['liquidity']:.0f}")

    if secs < min_t:
        print(f"\n⛔ Market closes in {secs}s — under {min_t}s minimum. Refusing to trade.")
        return

    # Phase 2: Order Book
    side  = (args.side or "BUY").upper()
    token = market["yes_token"] if side == "BUY" else market["no_token"]
    token_label = "YES" if side == "BUY" else "NO"
    size  = args.size or cfg["default_size"]

    print(f"\n📖 Order Book ({token_label} token, ${size} USDC)...")
    book, berr = analyze_book(token, size)
    if berr:
        print(f"❌ {berr}")
        return

    print(f"  Best ask:     ${book['best_ask']}")
    print(f"  Best bid:     ${book['best_bid']}")
    print(f"  Ask depth:    ${book['ask_depth']} USDC")
    print(f"  Your shares:  {book['shares']:.2f}")
    print(f"  Avg fill:     ${book['avg_price']}")
    print(f"  Slippage:     {book['slippage_pct']}%")

    for w in book["warnings"]:
        print(f"  ⚠️  {w}")

    if book["blocked"] and not args.force:
        print("\n⛔ Trade blocked due to high slippage. Use --force to override.")
        return

    if args.book:
        return

    # Phase 3: Execution
    order_type = (args.type or cfg["order_type"]).upper()

    if order_type == "GTC" and not args.price:
        print("\n❌ GTC orders require --price. Use --type FOK for a market order.")
        return

    print(f"\n📝 Order Summary:")
    print(f"  Side:       {side} {token_label}")
    print(f"  Type:       {order_type}")
    if order_type == "GTC":
        print(f"  Price:      ${args.price}")
    print(f"  Size:       ${size} USDC (~{book['shares']:.2f} shares)")
    print(f"  Window:     closes in {secs}s")

    if not args.live:
        print("\n  [DRY RUN] No order placed. Use --live to execute.")
        return

    print("\n🚀 Placing order...")
    client = get_client()

    try:
        if order_type == "GTC":
            resp = place_gtc(client, token, side, args.price, size)
        else:
            resp = place_fok(client, token, side, size)
    except Exception as e:
        print(f"❌ Order failed: {e}")
        return

    oid    = resp.get("orderID") or resp.get("id") or "N/A"
    status = resp.get("status", "UNKNOWN")
    errmsg = resp.get("errorMsg")

    print(f"  Order ID:  {oid}")
    print(f"  Status:    {status}")
    if errmsg:
        print(f"  ⚠️  {errmsg}")

    if status in ("MATCHED", "LIVE"):
        print(f"\n✅ Order {status.lower()}.")
    else:
        print(f"\n⚠️  Unexpected status: {status}. Check Polymarket for order state.")

# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Polymarket BTC 5-Min Trader")
    p.add_argument("--live",   action="store_true", help="Execute real trades (default: dry run)")
    p.add_argument("--book",   action="store_true", help="Show order book only, no trade")
    p.add_argument("--orders", action="store_true", help="List open orders")
    p.add_argument("--cancel", metavar="ORDER_ID",  help="Cancel an order by ID")
    p.add_argument("--side",   choices=["BUY","SELL"], default="BUY",
                   help="BUY = buy YES token; SELL = buy NO token")
    p.add_argument("--price",  type=float, help="Limit price for GTC orders (e.g. 0.55)")
    p.add_argument("--size",   type=float, help="USDC amount to spend (default from config)")
    p.add_argument("--type",   choices=["GTC","FOK"], help="Order type (default from config)")
    p.add_argument("--force",  action="store_true", help="Override high-slippage block")
    p.add_argument("--config", action="store_true", help="Show current config")
    p.add_argument("--set",    action="append", metavar="KEY=VALUE", help="Update config")
    args = p.parse_args()

    if args.set:
        updates = {}
        for item in args.set:
            if "=" not in item:
                print(f"Invalid --set format: {item}. Use KEY=VALUE")
                sys.exit(1)
            k, v = item.split("=", 1)
            if k not in CONFIG_SCHEMA:
                print(f"Unknown key: {k}. Valid: {', '.join(CONFIG_SCHEMA)}")
                sys.exit(1)
            updates[k] = CONFIG_SCHEMA[k]["type"](v)
        update_config(updates)
        print(f"✅ Config updated: {json.dumps(updates)}")
        sys.exit(0)

    run(args)

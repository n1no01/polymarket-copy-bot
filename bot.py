import os
import time
import requests
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType, Side

load_dotenv()

# Konfiguracija iz .env fajla
CLOB_API_URL = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
DATA_API_URL = "https://data-api.polymarket.com"
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
CHAIN_ID = int(os.getenv("CHAIN_ID", 137))
FUNDER_ADDRESS = os.getenv("POLY_FUNDER_ADDRESS", "").strip()
TARGET_WALLET = os.getenv("TARGET_WALLET_TO_COPY", "").strip()
COPY_AMOUNT_USDC = float(os.getenv("COPY_AMOUNT_USDC", 10))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_MS", 5000)) / 1000

# Za Google / Social Login koristimo signature_type = 2 (Proxy/Safe wallet)
SIGNATURE_TYPE = 1

# Skup za pamćenje već obrađenih trejdova da ih ne kopiramo više puta
seen_trades = set()

def get_clob_client():
    if not FUNDER_ADDRESS or not PRIVATE_KEY:
        raise ValueError("GRESKA: PRIVATE_KEY ili POLY_FUNDER_ADDRESS nisu podešeni u .env fajlu!")
    
    print("[Bot] Inicijalizacija CLOB klijenta za Google Proxy wallet...")
    client = ClobClient(
        host=CLOB_API_URL,
        key=PRIVATE_KEY,
        chain_id=CHAIN_ID,
        signature_type=SIGNATURE_TYPE,
        funder=FUNDER_ADDRESS
    )
    creds = client.derive_api_key()
    client.set_api_creds(creds)
    print("[Bot] CLOB klijent uspešno spreman.")
    return client

def get_user_token_balance(token_id: str) -> float:
    """Proverava naš trenutni balans tokena preko V2 positions API-ja"""
    try:
        response = requests.get(f"{DATA_API_URL}/v2/positions", params={"user": FUNDER_ADDRESS}, timeout=5)
        if response.status_code == 200:
            data = response.json().get("data", [])
            for pos in data:
                if pos.get("token_id") == token_id or pos.get("asset") == token_id:
                    return float(pos.get("current_size", 0))
        return 0.0
    except Exception as e:
        print(f"[Upozorenje] Nije moguće pročitati balans: {e}")
        return 0.0

def fetch_latest_activities():
    """Dohvata najnovije aktivnosti target wallet-a"""
    try:
        response = requests.get(f"{DATA_API_URL}/activity", params={"user": TARGET_WALLET, "limit": 10}, timeout=5)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"[Upozorenje] Greška pri dohvatanju aktivnosti target wallet-a: {e}")
    return []

def execute_trade(client, trade):
    side_str = trade.get("side")
    token_id = trade.get("asset")
    price = float(trade.get("price", 0))
    
    if not token_id or price <= 0:
        return

    is_buy = (side_str == "BUY")
    size = 0.0

    if is_buy:
        # Proveravamo da li već držimo ovaj token da ne dupliramo kupovinu
        current_balance = get_user_token_balance(token_id)
        if current_balance > 0:
            print(f"[Bot] Već posedujemo poziciju za token {token_id}. Preskačem BUY.")
            return
        
        size = COPY_AMOUNT_USDC / price
        print(f"[Bot] BUY nalog -> Kupujemo za {COPY_AMOUNT_USDC} USDC | Token: {token_id} | Količina: {size}")
        side = Side.BUY
    else:
        # SELL: Mirror exit — prodajemo sav naš balans tog tokena
        current_balance = get_user_token_balance(token_id)
        if current_balance <= 0:
            print(f"[Bot] Preskačem SELL: Nemamo ovaj token u novčaniku (balans je 0).")
            return
        
        size = current_balance
        print(f"[Bot] SELL nalog -> Pronađen balans: {size} tokena. Zatvaramo poziciju!")
        side = Side.SELL

    try:
        # Kreiranje i slanje Market / FOK naloga
        order_args = MarketOrderArgs(
            token_id=token_id,
            amount=COPY_AMOUNT_USDC if is_buy else (size * price),
            side=side,
            order_type=OrderType.FOK
        )
        
        signed_order = client.create_market_order(order_args)
        response = client.post_order(signed_order, OrderType.FOK)
        print(f"[Bot] USPEŠNO IZVRŠEN TREJD! Odgovor berze:", response)
    except Exception as e:
        print(f"[Bot Greška] Neuspelo izvršenje naloga na Polymarketu: {e}")

def main():
    print("==================================================")
    print("   POLYMARKET PYTHON COPY-BOT (24/7 REŽIM)        ")
    print(f"   Pratim wallet: {TARGET_WALLET}")
    print("==================================================\n")

    client = get_clob_client()

    while True:
        try:
            activities = fetch_latest_activities()
            for activity in activities:
                if activity.get("type") == "TRADE":
                    tx_hash = activity.get("transactionHash")
                    asset = activity.get("asset")
                    trade_id = f"{tx_hash}_{asset}"

                    if trade_id not in seen_trades:
                        seen_trades.add(trade_id)
                        print(f"\n[Aktivnost detektovana] Strana: {activity.get('side')} | Token: {asset}")
                        execute_trade(client, activity)
        except Exception as e:
            print(f"[Greška u glavnoj petlji]: {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
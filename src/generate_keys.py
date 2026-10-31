from py_clob_client.client import ClobClient

# Definiši host i chain id za Polygon Mainnet
HOST = "https://clob.polymarket.com"
CHAIN_ID = 137

# Tvoj privatni ključ (isti onaj iz .env fajla)
PRIVATE_KEY = "" #Izbrisan zbog sigurnosti, koristi svoj privatni ključ iz .env fajla

def generate_api_keys():
    # Inicijalizujemo klijent sa tvojim privatnim ključem
    client = ClobClient(
        host=HOST,
        chain_id=CHAIN_ID,
        key=PRIVATE_KEY
    )

    # Kreiramo ili preuzimamo postojeće API akreditive
    # Ova funkcija komunicira sa Polymarket serverom i vezuje API ključ za tvoj wallet
    creds = client.create_or_derive_api_creds()
    
    print("--- USPEŠNO GENERISANI API KLJUČEVI ---")
    print(f"API_KEY={creds.api_key}")
    print(f"API_SECRET={creds.api_secret}")
    print(f"API_PASSPHRASE={creds.api_passphrase}")

if __name__ == "__main__":
    generate_api_keys()
import axios from 'axios';
import dotenv from 'dotenv';

dotenv.config();

const DATA_API_URL = 'https://data-api.polymarket.com';
const TARGET_WALLET = process.env.TARGET_WALLET_TO_COPY || '';

// Čuvamo ID poslednje obrađene transakcije da se ne bi ponavljali
let lastProcessedTxHash: string | null = null;

export async function checkTargetActivity() {
    try {
        // Pozivamo Data API v2 za listu skorašnjih trejdova ili aktivnosti ciljnog korisnika
        const response = await axios.get(`${DATA_API_URL}/trades`, {
            params: {
                maker_address: TARGET_WALLET, // ili user_address zavisno od endpoint specifikacije
                limit: 5
            }
        });

        const trades = response.data;
        if (!trades || trades.length === 0) return null;

        // Uzimamo najsvežiji trejd
        const latestTrade = trades[0];
        const txHash = latestTrade.transactionHash || latestTrade.id;

        if (lastProcessedTxHash === null) {
            // Prvo pokretanje: samo memorišemo poslednji trejd da ne kopiramo stare akcije od juče
            lastProcessedTxHash = txHash;
            console.log(`[Monitor] Inicijalizovan praćeni wallet. Poslednji tx: ${txHash}`);
            return null;
        }

        if (txHash !== lastProcessedTxHash) {
            lastProcessedTxHash = txHash;
            console.log(`[Monitor] Detektovan novi trejd od mete!`, latestTrade);
            return latestTrade; // Vraćamo podatke o novom trejdu
        }

        return null;
    } catch (error) {
        console.error(
            '[Monitor Greška] Neuspjelo dohvatanje podataka:',
            error instanceof Error ? error.message : String(error)
        );
        return null;
    }
}
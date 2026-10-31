import axios from 'axios';
import dotenv from 'dotenv';

dotenv.config();

const DATA_API_URL = 'https://data-api.polymarket.com';
const TARGET_WALLET = process.env.TARGET_WALLET_TO_COPY || '';

async function testMonitor() {
    console.log(`[Test] Proveravam aktivnosti za wallet: ${TARGET_WALLET}`);
    try {
        const response = await axios.get(`${DATA_API_URL}/trades`, {
            params: {
                maker_address: TARGET_WALLET,
                limit: 3
            }
        });

        console.log(`[Test] Uspešno dobijeni podaci! Pronađeno trejdova:`, response.data.length);
        if (response.data.length > 0) {
            console.log(`[Test] Poslednji zabeleženi trejd mete:`, response.data[0]);
        } else {
            console.log(`[Test] Wallet nema skorašnjih trejdova ili je adresa neispravna.`);
        }
    } catch (error: any) {
        console.error(`[Test Greška]:`, error.response?.data || error.message);
    }
}

testMonitor();
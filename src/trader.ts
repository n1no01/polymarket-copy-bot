import { createWalletClient, http } from 'viem';
import { polygon } from 'viem/chains';
import { privateKeyToAccount } from 'viem/accounts';
import { ClobClient, Side } from '@polymarket/clob-client';
import axios from 'axios';
import dotenv from 'dotenv';

dotenv.config();

const CLOB_API_URL = process.env.POLYMARKET_HOST || 'https://clob.polymarket.com';
const DATA_API_URL = 'https://data-api.polymarket.com';
const PRIVATE_KEY = process.env.PRIVATE_KEY as `0x${string}` || '';
const CHAIN_ID = Number(process.env.CHAIN_ID || 137);

const FUNDER_ADDRESS = process.env.POLY_FUNDER_ADDRESS?.trim() || '';
const SIGNATURE_TYPE = 1;

const account = privateKeyToAccount(PRIVATE_KEY);
const signer = createWalletClient({
    account,
    chain: polygon,
    transport: http('https://polygon-rpc.com'),
});

let cachedClobClient: ClobClient | null = null;

async function getClobClient(): Promise<ClobClient> {
    if (cachedClobClient) {
        return cachedClobClient;
    }

    if (!FUNDER_ADDRESS) {
        throw new Error('POLY_FUNDER_ADDRESS nije definisan u .env fajlu!');
    }

    console.log('[Trader] Inicijalizacija Polymarket CLOB klijenta za Proxy...');
    const creds = await new ClobClient(
        CLOB_API_URL,
        CHAIN_ID,
        signer as any
    ).createOrDeriveApiKey();

    cachedClobClient = new ClobClient(
        CLOB_API_URL,
        CHAIN_ID,
        signer as any,
        creds,
        SIGNATURE_TYPE,
        FUNDER_ADDRESS,
        undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        true
    );

    console.log('[Trader] CLOB klijent uspešno inicijalizovan.');
    return cachedClobClient;
}

// Pomoćna funkcija za dinamičko preuzimanje parametara marketa (tickSize i negRisk)
async function getMarketParams(clobClient: ClobClient, tokenId: string) {
    try {
        // Možemo upotrebiti clobClient za preuzimanje tick size-a direktno sa CLOB-a
        const tickSize = await clobClient.getTickSize(tokenId);
        const negRisk = await clobClient.getNegRisk(tokenId);

        return {
            tickSize: tickSize || "0.01",
            negRisk: Boolean(negRisk)
        };
    } catch (error) {
        console.warn(`[Trader Upozorenje] Neuspelo dinamičko preuzimanje market parametara za token ${tokenId}. Koristim fallback (0.01 / false).`, error);
        return {
            tickSize: "0.01",
            negRisk: false
        };
    }
}

async function getUserTokenBalance(assetId: string): Promise<number> {
    try {
        const response = await axios.get(`${DATA_API_URL}/v2/positions`, {
            params: { user: FUNDER_ADDRESS },
            timeout: 5000
        });

        const positions = response.data?.data || [];
        const position = positions.find((p: any) => p.token_id === assetId || p.asset === assetId);

        if (position && position.current_size) {
            return Number(position.current_size);
        }
        return 0;
    } catch (error) {
        console.error('[Trader Greška] Neuspelo čitanje V2 pozicija:', error);
        throw error;
    }
}

export async function executeCopyTrade(tradeData: any) {
    try {
        console.log(`[Trader] Obrada trejda za token: ${tradeData.asset} | Strana: ${tradeData.side}`);

        const clobClient = await getClobClient();
        const price = Number(tradeData.price);
        const isBuy = tradeData.side === 'BUY';
        let size: number;

        if (isBuy) {
            const amountUsdc = Number(process.env.COPY_AMOUNT_USDC || 10);
            size = amountUsdc / price;
            console.log(`[Trader] BUY nalog -> Kupujemo za ${amountUsdc} USDC. Cena: ${price}, Količina: ${size}`);
        } else {
            console.log(`[Trader] SELL nalog detektovan. Proveravam balans za token ${tradeData.asset}...`);
            const myBalance = await getUserTokenBalance(tradeData.asset);

            if (myBalance <= 0) {
                console.log(`[Trader] Preskačem SELL: Nemamo ovaj token u novčaniku (balans je 0).`);
                return;
            }

            size = myBalance;
            console.log(`[Trader] SELL nalog -> Pronađen balans: ${myBalance} tokena. Zatvaramo poziciju!`);
        }

        if (size <= 0 || isNaN(size)) {
            console.error('[Trader Greška] Izračunata količina (size) nije validna:', size);
            return;
        }

        // Dinamičko povlačenje tickSize i negRisk umesto hardkodiranja
        const marketParams = await getMarketParams(clobClient, tradeData.asset);
        console.log(`[Trader] Parametri marketa preuzeti: tickSize=${marketParams.tickSize}, negRisk=${marketParams.negRisk}`);

        console.log(`[Trader] Šaljem nalog na Polymarket... Token: ${tradeData.asset}, Cena: ${price}, Količina: ${size}`);

        const order = await clobClient.createAndPostOrder(
            {
                tokenID: tradeData.asset,
                price: price,
                size: size,
                side: isBuy ? Side.BUY : Side.SELL,
            },
            {
                tickSize: marketParams.tickSize as any,
                negRisk: marketParams.negRisk,
            }
        );

        console.log(`[Trader] Uspešno poslat live nalog! Odgovor servera:`, order);

    } catch (error: any) {
        const errorMsg = error?.response?.data || error?.message || String(error);
        console.error('[Trader Greška] Neuspelo izvršenje live naloga:', errorMsg);
        throw error;
    }
}
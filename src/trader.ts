import { ethers } from 'ethers';
import { ClobClient, Side } from '@polymarket/clob-client';
import dotenv from 'dotenv';

dotenv.config();

const CLOB_API_URL = process.env.POLYMARKET_HOST || 'https://clob.polymarket.com';
const PRIVATE_KEY = process.env.PRIVATE_KEY || '';
const CHAIN_ID = Number(process.env.CHAIN_ID || 137);

// Inicijalizacija wallet-a
const provider = new ethers.JsonRpcProvider('https://polygon-rpc.com');
const wallet = new ethers.Wallet(PRIVATE_KEY, provider);

export async function executeCopyTrade(tradeData: any) {
    try {
        console.log(`[Trader] Priprema live kopiranja trejda za token: ${tradeData.asset}`);

        // Koristimo 'as any' da zaobiđemo restriktivne TypeScript tipove u clob-client paketu
        const clobClient = new ClobClient(
            CLOB_API_URL,
            CHAIN_ID,
            wallet as any,
            {
                key: process.env.POLY_API_KEY || '',
                secret: process.env.POLY_API_SECRET || '',
                passphrase: process.env.POLY_API_PASSPHRASE || '',
            }
        );

        const amountUsdc = Number(process.env.COPY_AMOUNT_USDC || 10);
        const price = Number(tradeData.price);
        const size = amountUsdc / price;

        console.log(`[Trader] Šaljem pravi nalog na Polymarket... Token: ${tradeData.asset}, Cena: ${price}, Količina: ${size}, Strana: ${tradeData.side}`);

        const order = await clobClient.createAndPostOrder({
            tokenID: tradeData.asset,
            price: price,
            size: size,
            side: tradeData.side === 'BUY' ? Side.BUY : Side.SELL,
        });

        console.log(`[Trader] Uspešno izvršen live trejd! Odgovor servera:`, order);
    } catch (error: any) {
        console.error('[Trader Greška] Neuspelo izvršenje live naloga:', error?.message || error);
    }
}
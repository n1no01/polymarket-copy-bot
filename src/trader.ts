import { createWalletClient, http } from 'viem';
import { polygon } from 'viem/chains';
import { privateKeyToAccount } from 'viem/accounts';
import { ClobClient, Side } from '@polymarket/clob-client';
import dotenv from 'dotenv';

dotenv.config();

const CLOB_API_URL = process.env.POLYMARKET_HOST || 'https://clob.polymarket.com';
const PRIVATE_KEY = process.env.PRIVATE_KEY as `0x${string}` || '';
const CHAIN_ID = Number(process.env.CHAIN_ID || 137);

const account = privateKeyToAccount(PRIVATE_KEY);
const signer = createWalletClient({
    account,
    chain: polygon,
    transport: http('https://polygon-rpc.com'),
});

export async function executeCopyTrade(tradeData: any) {
    try {
        console.log(`[Trader] Priprema live kopiranja trejda za token: ${tradeData.asset}`);

        const creds = await new ClobClient(
            CLOB_API_URL,
            CHAIN_ID,
            signer as any
        ).createOrDeriveApiKey();

        const signatureType = 0;
        const funderAddress = account.address;

        const clobClient = new ClobClient(
            CLOB_API_URL,
            CHAIN_ID,
            signer as any,
            creds,
            signatureType,
            funderAddress,
            undefined,
            undefined,
            undefined,
            undefined,
            undefined,
            undefined,
            true
        );

        const amountUsdc = Number(process.env.COPY_AMOUNT_USDC || 10);
        const price = Number(tradeData.price);
        const size = amountUsdc / price;

        console.log(`[Trader] Šaljem pravi nalog direktno na Polymarket... Token: ${tradeData.asset}, Cena: ${price}, Količina: ${size}, Strana: ${tradeData.side}`);

        const order = await clobClient.createAndPostOrder(
            {
                tokenID: tradeData.asset,
                price: price,
                size: size,
                side: tradeData.side === 'BUY' ? Side.BUY : Side.SELL,
            },
            {
                tickSize: "0.01",
                negRisk: false,
            }
        );

        console.log(`[Trader] Uspešno izvršen live trejd! Odgovor servera:`, order);
    } catch (error: any) {
        const errorMsg = error?.response?.data || error?.message || String(error);
        console.error('[Trader Greška] Neuspelo izvršenje live naloga:', errorMsg);
    }
}
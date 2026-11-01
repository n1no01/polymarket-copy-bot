import axios from 'axios';
import dotenv from 'dotenv';

dotenv.config();

const DATA_API_URL = 'https://data-api.polymarket.com/v2/trades';
const TARGET_WALLET = process.env.TARGET_WALLET_TO_COPY?.trim();

const PAGE_LIMIT = 100;

// Tradeovi koje smo već registrovali
const seenTrades = new Set<string>();

let initialized = false;

function getTradeKey(trade: any): string {
    return trade.id ||
        trade.trade_id ||
        [
            trade.transaction_hash,
            trade.timestamp,
            trade.token_id,
            trade.side,
            trade.price,
            trade.size
        ].join(':');
}

async function fetchTrades(cursor: string | null = null) {
    // Definišemo tip za parametre da TypeScript zna za cursor
    const params: { user: string | undefined; limit: number; cursor?: string } = {
        user: TARGET_WALLET,
        limit: PAGE_LIMIT
    };

    if (cursor) {
        params.cursor = cursor;
    }

    const response = await axios.get(DATA_API_URL, {
        params,
        timeout: 10000
    });

    return {
        trades: response.data.data || [],
        nextCursor: response.data.pagination?.next_cursor || null
    };
}

export async function checkTargetActivity() {
    if (!TARGET_WALLET) {
        throw new Error('TARGET_WALLET_TO_COPY nije definisan u .env fajlu.');
    }

    try {
        const newTrades: any[] = [];
        let cursor: string | null = null;
        let firstPage = true;
        let reachedKnownTrade = false;

        do {
            const result = await fetchTrades(cursor);

            if (firstPage && !initialized) {
                for (const trade of result.trades) {
                    seenTrades.add(getTradeKey(trade));
                }

                initialized = true;

                console.log(
                    `[Monitor] Wallet inicijalizovan. ` +
                    `Registrovano ${seenTrades.size} postojećih tradeova.`
                );

                return [];
            }

            for (const trade of result.trades) {
                const key = getTradeKey(trade);

                if (seenTrades.has(key)) {
                    reachedKnownTrade = true;
                    continue;
                }

                seenTrades.add(key);
                newTrades.push(trade);
            }

            cursor = result.nextCursor;
            firstPage = false;

        } while (cursor && !reachedKnownTrade);

        // Najstariji novi trade prvo
        newTrades.sort(
            (a, b) => Number(a.timestamp) - Number(b.timestamp)
        );

        if (newTrades.length > 0) {
            console.log(
                `[Monitor] Detektovano ${newTrades.length} novih tradeova!`
            );
        }

        return newTrades;

    } catch (error: unknown) {
        const err = error as any;
        console.error(
            '[Monitor Greška]',
            err.response?.data || err.message || String(error)
        );

        throw error;
    }
}
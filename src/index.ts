import { checkTargetActivity } from './monitor.js';
import { executeCopyTrade } from './trader.js';

const CHECK_INTERVAL_MS = 3000;

async function mainLoop() {
    console.log('Polymarket Copy-Bot started...');

    while (true) {
        try {
            const newTrades = await checkTargetActivity();

            if (newTrades?.length) {
                for (const trade of newTrades) {
                    await executeCopyTrade(trade);
                }
            }
        } catch (error: any) { // Dodali smo : any ovdje
            console.error('Bot error:', error?.message || String(error));
        }

        await new Promise(resolve =>
            setTimeout(resolve, CHECK_INTERVAL_MS)
        );
    }
}

mainLoop();
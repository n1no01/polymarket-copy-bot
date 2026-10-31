import { checkTargetActivity } from './monitor.js';
import { executeCopyTrade } from './trader.js';

const CHECK_INTERVAL_MS = 3000; // Provera svake 3 sekunde

async function mainLoop() {
    console.log('Polymarket Copy-Bot je pokrenut i aktivno prati metu...');

    setInterval(async () => {
        const newTrade = await checkTargetActivity();
        if (newTrade) {
            await executeCopyTrade(newTrade);
        }
    }, CHECK_INTERVAL_MS);
}

mainLoop();
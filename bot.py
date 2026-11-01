#!/usr/bin/env python3
"""Polymarket copy bot (zvanični `polymarket-client` SDK).

Šta radi:
  * prati jedan wallet (TARGET_WALLET_TO_COPY);
  * svaki njegov BUY kopira fiksnim iznosom (COPY_AMOUNT_USDC), sa ograničenjem cijene;
  * kad on radi SELL nekog tokena, prodaje SVE što držiš u tom tokenu.

Zaštite: DRY_RUN (default uključen), limit po tokenu, dnevni limit, provjera starosti
tradea, ograničenje slippagea, trajno pamćenje viđenih tradeova (nema duplog kopiranja
poslije restarta) i ponovni pokušaj prodaje dok se pozicija ne zatvori.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from polymarket import AsyncPublicClient, AsyncSecureClient

log = logging.getLogger("copybot")

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
MAX_SEEN = 5000          # koliko viđenih tradeova pamtimo
MAX_SELL_ATTEMPTS = 12   # koliko puta pokušavamo zatvoriti poziciju
SELL_RETRY_S = 5         # razmak između pokušaja prodaje
MAX_BACKOFF_S = 60


class ConfigError(Exception):
    pass


# --------------------------------------------------------------------------- config
def _dec(name: str, default: str) -> Decimal:
    raw = os.getenv(name, "").strip() or default
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise ConfigError(f"{name}={raw!r} nije broj") from None


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "da")


@dataclass(frozen=True)
class Config:
    target: str
    wallet: str
    private_key: str
    dry_run: bool
    copy_amount: Decimal
    max_per_asset: Decimal
    max_daily: Decimal
    buy_slippage: Decimal
    sell_slippage: Decimal
    min_price: Decimal
    max_price: Decimal
    max_trade_age_s: int
    interval_s: float
    sell_only_copied: bool
    setup_approvals: bool
    state_file: Path

    @classmethod
    def from_env(cls) -> "Config":
        target = os.getenv("TARGET_WALLET_TO_COPY", "").strip().lower()
        wallet = os.getenv("POLYMARKET_WALLET_ADDRESS", "").strip().lower()
        private_key = os.getenv("PRIVATE_KEY", "").strip()
        dry_run = _bool("DRY_RUN", True)

        if not ADDRESS_RE.match(target):
            raise ConfigError("TARGET_WALLET_TO_COPY nije postavljen ili nije ispravna 0x adresa")
        if not dry_run:
            if not private_key:
                raise ConfigError("PRIVATE_KEY nije postavljen (potreban kad je DRY_RUN=false)")
            if not ADDRESS_RE.match(wallet):
                raise ConfigError(
                    "POLYMARKET_WALLET_ADDRESS nije postavljen ili nije ispravna 0x adresa "
                    "(adresa tvog walleta sa Polymarket profila)"
                )
            if wallet == target:
                raise ConfigError("POLYMARKET_WALLET_ADDRESS i TARGET_WALLET_TO_COPY su isti")

        copy_amount = _dec("COPY_AMOUNT_USDC", "5")
        if copy_amount < 1:
            raise ConfigError("COPY_AMOUNT_USDC mora biti najmanje 1 (minimum naloga)")
        max_per_asset = _dec("MAX_USDC_PER_ASSET", str(copy_amount))
        if max_per_asset < copy_amount:
            raise ConfigError("MAX_USDC_PER_ASSET ne smije biti manji od COPY_AMOUNT_USDC")

        return cls(
            target=target,
            wallet=wallet,
            private_key=private_key,
            dry_run=dry_run,
            copy_amount=copy_amount,
            max_per_asset=max_per_asset,
            max_daily=_dec("MAX_DAILY_USDC", "50"),
            buy_slippage=_dec("BUY_SLIPPAGE", "0.03"),
            sell_slippage=_dec("SELL_SLIPPAGE", "0.10"),
            min_price=_dec("MIN_PRICE", "0.03"),
            max_price=_dec("MAX_PRICE", "0.97"),
            max_trade_age_s=int(_dec("MAX_TRADE_AGE_S", "60")),
            interval_s=float(_dec("CHECK_INTERVAL_S", "3")),
            sell_only_copied=_bool("SELL_ONLY_COPIED", False),
            setup_approvals=_bool("SETUP_APPROVALS", False),
            state_file=Path(os.getenv("STATE_FILE", "state.json")),
        )


# --------------------------------------------------------------------------- state
class State:
    """Trajno stanje (JSON). Piše se atomično, prije slanja svakog naloga."""

    def __init__(self, path: Path):
        self.path = path
        self.fresh = not path.exists()  # prvo pokretanje -> samo zapamti stare tradeove
        self.seen: list[str] = []
        self._seen_set: set[str] = set()
        self.day = self._today()
        self.spent_today = Decimal(0)
        self.asset_spent: dict[str, Decimal] = {}
        self.copied: set[str] = set()
        self.pending_sells: dict[str, dict[str, Any]] = {}
        self.dry_positions: dict[str, Decimal] = {}
        if not self.fresh:
            self._load()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load(self) -> None:
        data = json.loads(self.path.read_text())
        self.seen = list(data.get("seen", []))
        self._seen_set = set(self.seen)
        self.day = data.get("day", self._today())
        self.spent_today = Decimal(data.get("spent_today", "0"))
        self.asset_spent = {k: Decimal(v) for k, v in data.get("asset_spent", {}).items()}
        self.copied = set(data.get("copied", []))
        self.pending_sells = data.get("pending_sells", {})
        self.dry_positions = {k: Decimal(v) for k, v in data.get("dry_positions", {}).items()}

    def save(self) -> None:
        data = {
            "seen": self.seen[-MAX_SEEN:],
            "day": self.day,
            "spent_today": str(self.spent_today),
            "asset_spent": {k: str(v) for k, v in self.asset_spent.items()},
            "copied": sorted(self.copied),
            "pending_sells": self.pending_sells,
            "dry_positions": {k: str(v) for k, v in self.dry_positions.items()},
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        os.replace(tmp, self.path)

    def is_seen(self, key: str) -> bool:
        return key in self._seen_set

    def mark_seen(self, key: str) -> None:
        if key in self._seen_set:
            return
        self.seen.append(key)
        self._seen_set.add(key)
        if len(self.seen) > MAX_SEEN:
            self._seen_set.discard(self.seen.pop(0))

    def roll_day(self) -> None:
        today = self._today()
        if today != self.day:
            self.day, self.spent_today = today, Decimal(0)


# --------------------------------------------------------------------------- bot
def trade_key(t: Any) -> str:
    return f"{t.transaction_hash}:{t.asset_id}:{t.side}:{t.price}:{t.size}"


def age_seconds(ts: datetime) -> float:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds()


def short(asset: str) -> str:
    return asset[:10] + "..." if len(asset) > 12 else asset


class CopyBot:
    def __init__(self, cfg: Config, reader: Any, trader: Any | None, state: State):
        self.cfg = cfg
        self.reader = reader   # objekat sa list_trades (public ili secure klijent)
        self.trader = trader   # secure klijent (None u DRY_RUN)
        self.state = state

    # ---- jedan ciklus
    async def poll_once(self) -> None:
        page = await self.reader.list_trades(
            user=self.cfg.target, taker_only=False, page_size=50
        ).first_page()
        trades = [t for t in page.items if str(t.wallet).lower() == self.cfg.target]

        if self.state.fresh:
            for t in trades:
                self.state.mark_seen(trade_key(t))
            self.state.fresh = False
            self.state.save()
            log.info("Prvo pokretanje: zapamćeno %d starih tradeova, ništa se ne kopira.", len(trades))
            return

        new = [t for t in trades if not self.state.is_seen(trade_key(t))]
        new.sort(key=lambda t: t.timestamp)  # najstariji prvo
        if new:
            log.info("Novih tradeova mete: %d", len(new))
        for t in new:
            # prvo zabilježi da je viđen, pa tek onda šalji nalog: radije propustimo trade nego kupimo dvaput
            self.state.mark_seen(trade_key(t))
            self.state.save()
            try:
                await self.handle_trade(t)
            except Exception:
                log.exception("Greška pri obradi tradea %s", trade_key(t))
        await self.retry_pending_sells()

    async def handle_trade(self, t: Any) -> None:
        asset = str(t.asset_id)
        price = Decimal(str(t.price))
        label = f"{t.side} {(t.title or asset)[:45]} [{t.outcome}] @ {price}"
        age = age_seconds(t.timestamp)
        if t.side == "BUY":
            await self.copy_buy(asset, price, age, label)
        elif t.side == "SELL":
            await self.copy_sell(asset, price, label)

    # ---- BUY
    async def copy_buy(self, asset: str, price: Decimal, age: float, label: str) -> None:
        cfg, st = self.cfg, self.state
        if age > cfg.max_trade_age_s:
            log.info("PRESKAČEM (star %.0fs): %s", age, label)
            return
        if not (cfg.min_price <= price <= cfg.max_price):
            log.info("PRESKAČEM (cijena van %s-%s): %s", cfg.min_price, cfg.max_price, label)
            return
        spent_asset = st.asset_spent.get(asset, Decimal(0))
        if spent_asset + cfg.copy_amount > cfg.max_per_asset:
            log.info("PRESKAČEM (limit po tokenu %s USDC): %s", cfg.max_per_asset, label)
            return
        st.roll_day()
        if st.spent_today + cfg.copy_amount > cfg.max_daily:
            log.warning("PRESKAČEM (dnevni limit %s USDC): %s", cfg.max_daily, label)
            return

        max_price = min(price + cfg.buy_slippage, Decimal("0.99"))

        if cfg.dry_run:
            shares = (cfg.copy_amount / price).quantize(Decimal("0.01"), ROUND_DOWN)
            st.dry_positions[asset] = st.dry_positions.get(asset, Decimal(0)) + shares
            st.asset_spent[asset] = spent_asset + cfg.copy_amount
            st.spent_today += cfg.copy_amount
            st.copied.add(asset)
            st.save()
            log.info("[DRY_RUN] kupio bih %s USDC (max cijena %s, ~%s shares): %s",
                     cfg.copy_amount, max_price, shares, label)
            return

        # rezervacija prije slanja: ako poziv pukne usred mreže, ne znamo da li je nalog prošao
        st.asset_spent[asset] = spent_asset + cfg.copy_amount
        st.spent_today += cfg.copy_amount
        st.save()
        try:
            resp = await self.trader.place_market_order(
                asset_id=asset,
                side="BUY",
                amount=cfg.copy_amount,
                max_spend=cfg.copy_amount,
                max_price=max_price,
                order_type="FOK",
            )
        except Exception:
            log.exception("BUY nalog: ishod nepoznat, rezervacija ostaje (%s)", label)
            return
        if resp.ok:
            st.copied.add(asset)
            st.save()
            log.info("BUY OK [%s] %s USDC: %s", resp.status, cfg.copy_amount, label)
        else:
            st.asset_spent[asset] = spent_asset
            st.spent_today -= cfg.copy_amount
            st.save()
            log.warning("BUY ODBIJEN [%s] %s: %s", resp.code, resp.message, label)

    # ---- SELL
    async def copy_sell(self, asset: str, price: Decimal, label: str) -> None:
        if self.cfg.sell_only_copied and asset not in self.state.copied:
            log.info("PRESKAČEM SELL (token nije kupljen preko bota): %s", label)
            return
        log.info("Meta prodaje: %s", label)
        await self.sell_all(asset, price)

    async def held_shares(self, asset: str) -> Decimal:
        if self.cfg.dry_run:
            return self.state.dry_positions.get(asset, Decimal(0))
        pager = self.trader.list_positions(page_size=100)
        page = await pager.first_page()
        for _ in range(20):
            for p in page.items:
                if str(p.asset_id) == asset:
                    # riješeno tržište se ne prodaje preko CLOB-a nego se "redeem"-a
                    return Decimal(0) if p.redeemable else Decimal(p.current_size)
            if not page.has_more or not page.next_cursor:
                break
            page = await pager.from_cursor(page.next_cursor).first_page()
        return Decimal(0)

    async def sell_all(self, asset: str, ref_price: Decimal) -> None:
        cfg, st = self.cfg, self.state
        info = st.pending_sells.get(asset, {"attempts": 0})
        attempts = int(info["attempts"]) + 1

        held = await self.held_shares(asset)
        shares = held.quantize(Decimal("0.01"), ROUND_DOWN)
        if shares <= 0:
            if asset in st.pending_sells:
                log.info("Pozicija %s zatvorena.", short(asset))
            else:
                log.info("Nemaš token %s, ništa za prodati.", short(asset))
            st.pending_sells.pop(asset, None)
            st.save()
            return
        if attempts > MAX_SELL_ATTEMPTS:
            log.error("PRODAJA NIJE USPJELA nakon %d pokušaja za %s (%s shares). Zatvori ručno!",
                      MAX_SELL_ATTEMPTS, short(asset), shares)
            st.pending_sells.pop(asset, None)
            st.save()
            return

        min_price = max(ref_price - cfg.sell_slippage, Decimal("0.01"))

        if cfg.dry_run:
            st.dry_positions.pop(asset, None)
            st.pending_sells.pop(asset, None)
            st.save()
            log.info("[DRY_RUN] prodao bih %s shares tokena %s (min cijena %s)", shares, short(asset), min_price)
            return

        st.pending_sells[asset] = {
            "attempts": attempts,
            "next_try": time.time() + SELL_RETRY_S,
            "ref_price": str(ref_price),
        }
        st.save()
        try:
            resp = await self.trader.place_market_order(
                asset_id=asset, side="SELL", shares=shares, min_price=min_price, order_type="FAK"
            )
        except Exception:
            log.exception("SELL nalog: greška, pokušat ću ponovo (pokušaj %d)", attempts)
            return
        if resp.ok:
            log.info("SELL poslan [%s] %s shares, pokušaj %d; provjeravam da li je pozicija zatvorena",
                     resp.status, shares, attempts)
        else:
            log.warning("SELL ODBIJEN [%s] %s (pokušaj %d/%d)", resp.code, resp.message, attempts, MAX_SELL_ATTEMPTS)

    async def retry_pending_sells(self) -> None:
        for asset, info in list(self.state.pending_sells.items()):
            if time.time() < float(info.get("next_try", 0)):
                continue
            try:
                await self.sell_all(asset, Decimal(info.get("ref_price", "0.5")))
            except Exception:
                log.exception("Greška pri ponovnoj prodaji %s", short(asset))

    # ---- glavna petlja
    async def run(self, stop: asyncio.Event) -> None:
        failures = 0
        while not stop.is_set():
            try:
                await self.poll_once()
                failures = 0
            except Exception as e:  # mreža, API greška...
                failures += 1
                log.error("Greška u ciklusu (%d zaredom): %s", failures, e)
            delay = min(self.cfg.interval_s * (2 ** min(failures, 5)), MAX_BACKOFF_S) if failures else self.cfg.interval_s
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass


# --------------------------------------------------------------------------- start
async def check_geoblock() -> None:
    """Samo upozorenje: provjerava IP sa koje ovaj server izlazi na internet."""
    try:
        async with httpx.AsyncClient(timeout=6) as http:
            data = (await http.get("https://polymarket.com/api/geoblock")).json()
        where = f"{data.get('country')}/{data.get('region')} ({data.get('ip')})"
        if data.get("blocked"):
            log.warning("GEOBLOCK: IP servera %s je označen kao blokiran. Nalozi mogu biti odbijeni.", where)
        else:
            log.info("Geoblock provjera OK: %s", where)
    except Exception as e:
        log.info("Geoblock provjera nije uspjela (%s), nastavljam.", e)


async def main() -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    try:
        cfg = Config.from_env()
    except ConfigError as e:
        log.error("Konfiguracija: %s", e)
        return 2

    print("=" * 60)
    print(f"  Meta:        {cfg.target}")
    print(f"  Mod:         {'DRY RUN (ništa se ne šalje)' if cfg.dry_run else 'LIVE - pravi novac'}")
    print(f"  Po BUY-u:    {cfg.copy_amount} USDC | max po tokenu {cfg.max_per_asset} | dnevno {cfg.max_daily}")
    print(f"  Slippage:    BUY {cfg.buy_slippage} | SELL {cfg.sell_slippage}")
    print("=" * 60)

    await check_geoblock()
    state = State(cfg.state_file)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            signal.signal(sig, lambda *_: stop.set())

    if cfg.dry_run:
        async with AsyncPublicClient() as client:
            await CopyBot(cfg, client, None, state).run(stop)
    else:
        client = await AsyncSecureClient.create(private_key=cfg.private_key, wallet=cfg.wallet)
        try:
            if cfg.setup_approvals:
                log.info("Postavljam trading approvals...")
                await client.setup_trading_approvals()
                log.info("Approvals gotovi. Postavi SETUP_APPROVALS=false i pokreni ponovo.")
                return 0
            first = await client.list_positions(page_size=1).first_page()
            log.info("Povezan na wallet %s (otvorenih pozicija vidljivo: %s).",
                     cfg.wallet, "da" if first.items else "nema")
            await CopyBot(cfg, client, client, state).run(stop)
        finally:
            await client.close()
    log.info("Zaustavljeno.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
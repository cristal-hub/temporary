# main.py
# Tsclp_v2.2_volatile — основной цикл (ТОЛЬКО ПЕСОЧНИЦА).
#
# ИЗМЕНЕНИЯ (этап 4):
#   - Цены: TOP-10 + открытые позиции (объединение FIGI).
#   - prices=None -> monitoring_error, мониторинг продолжается.
#   - reconcile: двусторонний, с количествами и знаком (PositionData).
#   - process_closing: обработка UNCERTAIN через resolve_uncertain.
#   - partial BUY: cancel_order + подтверждённый финальный статус.
#   - потерянный ответ BUY: OrderResult.order_id сохраняется.
#   - _extract_price: цена за штуку (lots * lot_size).
#   - persistence (state.json): атомарная запись, обязательный reconcile.
#   - monitoring_error проверяется перед входами.
#   - trading.flag: main() НЕ создаёт автоматически.
#
# ИЗМЕНЕНИЯ (этап 4.1):
#   - trading_cycle: при status != FILL (например, NEW) — НЕ просто continue,
#     а запрос get_sandbox_order_state(order_id) и проверка lots_executed.
#     Если lots_executed > 0 -> создать TrackedPosition (как при FILL),
#     сохранить state.json, отправить POSITION_OPENED / notify.
#     Логирование ORDER_NOT_FILLED в JSONL (раньше — только logging.warning
#     в stdout, что терялось).
#
# ИЗМЕНЕНИЯ (этап 5, партия P0 — устранение потери учёта заявок и позиций):
#   ЦЕЛЬ: подтверждённое исполнение заявки не может быть потеряно; при
#   неопределённости новые BUY по соответствующему FIGI блокируются.
#
#   - OrderLedger (новый модуль order_ledger.py): order_id генерируется ДО
#     записи SIGNAL; запись INTENT — ДО сетевого вызова; если запись не
#     удалась — заявка НЕ отправляется (fail-closed). SENT — сразу после
#     ответа. Переходы — append-only с fsync.
#   - Ветки обработки BUY переписаны через ЕДИНУЮ точку регистрации
#     register_executed_buy(): тихие выходы FILL-ветки (D1) устранены —
#     при отсутствии цены исполнения или SL/TP позиция регистрируется в
#     RECOVERY (никогда не «continue молча»); цена входа НЕ заменяется
#     произвольной; расчёт SL/TP — только штатной стратегией.
#   - poll_open_orders(): пошаговый (по заявке за цикл) контроль незавершённых
#     BUY-заявок; отказ без исполнения фиксируется ТОЛЬКО при терминальном
#     статусе брокера с executed_lots==0; «не найдена»/ошибка -> UNCERTAIN,
#     НЕ отказ. При старении заявки остаток отменяется.
#   - promote_recovery_positions(): восстановление цены входа для RECOVERY
#     из статуса заявки + штатный расчёт SL/TP на свечах -> OPEN.
#     Автоматическое закрытие RECOVERY ЗАПРЕЩЕНО.
#   - reconcile(): адопция «брокер есть — локально нет» через единую точку
#     (RECOVERY), синхронизация количества, закрытие учёта при внешней
#     очистке (D2). Частичный снимок портфеля (None после fail-closed
#     в tinkoff_client) учёт не меняет.
#   - startup_recovery(): четырёхсторонняя сверка при рестарте (реестр ×
#     state.json × активные заявки брокера × позиции брокера); неизвестная
#     заявка брокера -> UNCERTAIN; подтверждённая позиция -> RECOVERY.
#   - _save_state_checked(): результат _save_state контролируется (D6);
#     при ошибке записи новые входы блокируются.
#   - close_position(): INTENT/SENT для SELL-заявок в реестре.
#   - Новые события: ORDER_INTENT, ORDER_INTENT_FAILED, ORDER_STATE_UNKNOWN,
#     POSITION_RECOVERED, POSITION_RECOVERY_RESOLVED, POSITION_RECOVERY_SYNC,
#     POSITION_RECOVERY_CLOSED, STARTUP_RECOVERY, STATE_SAVE_FAILED.
#     Существующие события (SIGNAL, POSITION_OPENED, BUY_UNKNOWN,
#     PARTIAL_FILL, ORDER_NOT_FILLED, RECONCILE_*, CLOSE_*) сохранены.
#     SIGNAL дополнен полем order_id (создаётся до записи события).
#
# ИЗМЕНЕНИЯ (партия P0-2 — поздние исполнения и промоция RECOVERY):
#   - promote_recovery_positions(): RECOVERY -> Open ТОЛЬКО при ТЕРМИНАЛЬНОМ
#     статусе входной заявки (FILL/CANCELLED/REJECTED/EXPIRED). Пока заявка
#     жива (NEW/PARTIALLYFILL), возможны дополнительные исполнения — позиция
#     остаётся в RECOVERY, количество синхронизируется опросом/reconcile.
#     Перед промоцией количество доуточняется по финальному lots_executed.
#   - register_executed_buy(): поздние дополнительные лоты ПОСЛЕ перехода
#     позиции в OPEN применяются к количеству (apply_late_entry_lots),
#     SL/TP не пересчитываются; синхронизация RECOVERY и запись в реестр
#     сохраняются немедленно.
#   - register_executed_buy(): исполнение с ПУСТЫМ (неопределённым) FIGI НЕ
#     регистрируется как обычная BUY-позиция — заявка остаётся UNCERTAIN,
#     новые BUY запрещены ГЛОБАЛЬНО до разрешения (has_unknown_figi_open_orders).
#   - register_executed_buy(): защита от «воскрешения» закрытой позиции —
#     терминальная заявка, чьё исполнение уже применено к учёту (executed_lots
#     в реестре), без активной позиции не регистрируется повторно; рост
#     lots_executed сверх учтённого обрабатывается как позднее исполнение.
#   - poll_open_orders(): повторная сверка терминальных заявок с исполнением
#     РЕАЛЬНО выполняется (order_ledger.open_orders() возвращает заявки с
#     residual-исполнением) — поздний рост lots_executed после терминального
#     CANCELLED/EXPIRED не теряется.
#   - Новые события: POSITION_ENTRY_LATE_FILL, BUY_UNKNOWN_FIGI.
#
# НЕ ИЗМЕНЕНО: стратегия (MA(9/21), фильтры, calculate_sl_tp), параметры
#   риска, Telegram-lifecycle, trading.flag логика, process_closing,
#   _load_state, resolve_closing/resolve_uncertain (execution.py — см. его
#   шапку). Режим — ТОЛЬКО ПЕСОЧНИЦА (SANDBOX).

import asyncio
import csv
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from t_tech.invest import (
    CandleInterval,
    OrderDirection,
    OrderExecutionReportStatus,
)

import config
import logger as research_logger
from order_ledger import OrderLedger
from risk_manager import RiskManager
from strategy import StrategyManager
from tinkoff_client import TinkoffClient, PositionData
from execution import ExecutionMonitor, PositionState, ACTIVE_STATES
from screener import Screener

if config.ENABLE_TELEGRAM:
    from telegram_bot import TelegramBot
else:
    TelegramBot = None

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.ext._utils.networkloop").setLevel(logging.WARNING)

TRADING_FLAG = "trading.flag"
STATE_FILE = "state.json"
STATE_FILE_TMP = "state.json.tmp"

# Этап 5: реестр заявок (рядом с state.json)
LEDGER_FILE = "order_ledger.jsonl"
# Этап 5: возраст нетерминальной заявки (в циклах), после которого остаток отменяется.
# ~5 минут при TRADING_CYCLE_SECONDS=30. Задержка исполнения — штатный сценарий;
# заявка не выбрасывается из учёта, а доводится до терминального статуса.
ORDER_POLL_MAX_AGE_CYCLES = 10

# Маппинг кодов статусов (верифицирован по документации T-Invest API:
# FILL=1, REJECTED=2, CANCELLED=3, NEW=4, PARTIALLYFILL=5; 6 — EXPIRED defensively)
_STATUS_NAME_BY_CODE = {
    1: "FILL", 2: "REJECTED", 3: "CANCELLED", 4: "NEW", 5: "PARTIALLYFILL", 6: "EXPIRED",
}
_TERMINAL_ORDER_STATUSES = {"FILL", "REJECTED", "CANCELLED", "EXPIRED"}


def _status_name(raw_status):
    """OrderExecutionReportStatus/число -> 'FILL'|'REJECTED'|'CANCELLED'|'NEW'|'PARTIALLYFILL'|'EXPIRED'|None."""
    if raw_status is None:
        return None
    try:
        code = int(raw_status)
    except (TypeError, ValueError):
        return None
    return _STATUS_NAME_BY_CODE.get(code)


def _age_in_cycles(rec) -> float:
    """
    Возраст записи реестра в циклах (для решения об отмене остатка).

    Дефект №3: возраст считается от НЕИЗМЕНЯЕМОГО времени создания заявки
    (created_at_utc — время записи INTENT), а не от timestamp_utc, который
    record_status() обновляет при каждом опросе. Обновление статуса NEW
    не сбрасывает возраст; после перезапуска возраст восстанавливается
    из реестра (created_at_utc хранится в журнале).
    """
    try:
        t0_raw = rec.get("created_at_utc") or rec.get("timestamp_utc")
        t0 = datetime.fromisoformat(t0_raw)
        return (datetime.now(timezone.utc) - t0).total_seconds() / max(1, config.TRADING_CYCLE_SECONDS)
    except Exception:
        return 0.0


top_instruments = []
telegram_bot = None
main_client = None
reconcile_done = False
monitor_ref = None
rm_ref = None

# Этап 5: реестр заявок и флаг ошибки сохранения state.json
ledger_ref = None
state_save_failed = False

_strategy_event_keys = set()
_promote_throttle = {"n": 0, "alive": 0}


def is_trading_enabled() -> bool:
    return os.path.exists(TRADING_FLAG)


def start_trading():
    try:
        with open(TRADING_FLAG, "w", encoding="utf-8") as f:
            f.write(datetime.now(timezone.utc).isoformat())
        logging.info("✅ Торговля ВКЛЮЧЕНА (создан trading.flag)")
        research_logger.log_event("TRADING_START", {})
    except Exception as e:
        logging.error(f"Не удалось создать {TRADING_FLAG}: {type(e).__name__}: {e}")


def stop_trading():
    try:
        if os.path.exists(TRADING_FLAG):
            os.remove(TRADING_FLAG)
        logging.info("🛑 Торговля ВЫКЛЮЧЕНА (удалён trading.flag)")
        research_logger.log_event("TRADING_STOP", {})
    except Exception as e:
        logging.error(f"Не удалось удалить {TRADING_FLAG}: {type(e).__name__}: {e}")


def get_status():
    instruments = ", ".join(i['ticker'] for i in top_instruments) or "—"
    monitored = ", ".join(
        p.ticker for p in (monitor_ref.positions.values() if monitor_ref else [])
        if p.state in ACTIVE_STATES
    ) or "—"
    uncertain = "ДА" if (main_client and main_client.is_uncertain()) else "нет"
    trading = "АКТИВНА" if is_trading_enabled() else "СТОП"
    tg_status = "работает" if telegram_bot else "НЕДОСТУПЕН"
    monitoring_err = "ДА" if (monitor_ref and monitor_ref.monitoring_error) else "нет"
    open_orders = ledger_ref.open_count() if ledger_ref else 0
    save_err = "ДА" if state_save_failed else "нет"
    ledger_ok = ledger_ref.is_reliable() if ledger_ref else True
    ledger_ok_text = "OK" if ledger_ok else "НЕДОСТОВЕРЕН — новые BUY запрещены"
    recovery = ", ".join(
        f"{p.ticker} ({p.recovery_source or '?'})"
        for p in (monitor_ref.positions.values() if monitor_ref else [])
        if p.state == PositionState.RECOVERY
    ) or "—"
    return (
        f"🤖 Статус\n"
        f"Торговля: {trading}\n"
        f"Telegram: {tg_status}\n"
        f"Режим: SANDBOX\n"
        f"Сверка: {'OK' if reconcile_done else 'НЕ ЗАВЕРШЕНА'}\n"
        f"Неопределённость: {uncertain}\n"
        f"Ошибка мониторинга цен: {monitoring_err}\n"
        f"Ошибка сохранения state: {save_err}\n"
        f"Реестр заявок: {ledger_ok_text}\n"
        f"Незавершённых заявок: {open_orders}\n"
        f"RECOVERY: {recovery}\n"
        f"Для новых входов ({len(top_instruments)}): {instruments}\n"
        f"Под мониторингом ({monitor_ref.active_count() if monitor_ref else 0}): {monitored}"
    )


async def notify(text: str):
    logging.info(f"[notify] {text}")
    if telegram_bot:
        try:
            await telegram_bot.send_message(text)
        except Exception as e:
            logging.warning(f"Telegram send error: {type(e).__name__}: {e}")


async def _stop_telegram_bot():
    global telegram_bot
    if telegram_bot is None:
        return
    try:
        try:
            await asyncio.wait_for(telegram_bot.application.updater.stop(), timeout=10.0)
        except Exception as e:
            logging.warning(f"[tg] updater.stop(): {type(e).__name__}: {e}")
        try:
            await asyncio.wait_for(telegram_bot.application.stop(), timeout=10.0)
        except Exception as e:
            logging.warning(f"[tg] application.stop(): {type(e).__name__}: {e}")
        try:
            await asyncio.wait_for(telegram_bot.application.shutdown(), timeout=10.0)
        except Exception as e:
            logging.warning(f"[tg] application.shutdown(): {type(e).__name__}: {e}")
        logging.info("[tg] Telegram-бот остановлен.")
    except Exception as e:
        logging.warning(f"[tg] Ошибка остановки: {type(e).__name__}: {e}")
    finally:
        telegram_bot = None


async def _try_create_telegram_bot() -> bool:
    global telegram_bot
    if not (config.ENABLE_TELEGRAM and TelegramBot):
        return False
    try:
        bot = TelegramBot(
            config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID,
            config.TELEGRAM_ALLOWED_USER_ID,
            {'start_trading': start_trading, 'stop_trading': stop_trading, 'get_status': get_status},
        )
        await asyncio.wait_for(bot.application.initialize(), timeout=20.0)
        await asyncio.wait_for(bot.application.start(), timeout=20.0)
        await asyncio.wait_for(bot.application.updater.start_polling(), timeout=20.0)
        await bot.register_commands()
        telegram_bot = bot
        logging.info("[tg] Telegram-бот создан и запущен (initialize → start → polling).")
        await notify("🤖 Tsclp_v2.2_volatile: Telegram восстановлен.")
        return True
    except Exception as e:
        logging.warning(f"[tg] Не удалось создать Telegram-бот: {type(e).__name__}: {e!r}")
        await _stop_telegram_bot()
        return False


async def _telegram_watchdog():
    if not (config.ENABLE_TELEGRAM and TelegramBot):
        return
    failures = 0
    while True:
        await asyncio.sleep(60)
        if telegram_bot is None:
            await _try_create_telegram_bot()
            continue
        try:
            await asyncio.wait_for(telegram_bot.application.bot.get_me(), timeout=15.0)
            if failures > 0:
                logging.info(f"[tg-watchdog] Telegram восстановлен после {failures} неудач.")
            failures = 0
        except Exception as e:
            failures += 1
            logging.warning(f"[tg-watchdog] Telegram недоступен ({failures}/3): {type(e).__name__}: {e!r}")
            if failures >= 3:
                logging.info("[tg-watchdog] Полный перезапуск Telegram...")
                await _stop_telegram_bot()
                failures = 0


def _trade_id_already_logged(trade_id: str) -> bool:
    if not trade_id:
        return False
    try:
        if not os.path.isfile(research_logger.TRADES_FILE):
            return False
        with open(research_logger.TRADES_FILE, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                if row.get("trade_id") == trade_id:
                    return True
        return False
    except Exception as e:
        logging.warning(f"[csv] Не удалось проверить trade_id {trade_id}: {type(e).__name__}: {e}")
        return True


def _timestamp_for_log(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def _log_strategy_diagnostic(
    event_type: str,
    ticker: str,
    figi: str,
    signal: str,
    reason: str,
    sm: StrategyManager,
    df: pd.DataFrame,
    price: float,
):
    try:
        candle_time = getattr(sm, "last_signal_candle_time", None)
        if candle_time is None and df is not None and not df.empty:
            candle_time = df.index[-1]
        candle_time_text = _timestamp_for_log(candle_time)
        if candle_time_text is None:
            candle_time_text = "UNKNOWN"
        event_key = (event_type, ticker, candle_time_text)
        if event_key in _strategy_event_keys:
            return

        last_close = None
        last_volume = None
        if df is not None and not df.empty:
            if "close" in df.columns:
                value = df["close"].iloc[-1]
                if pd.notna(value):
                    last_close = float(value)
            if "volume" in df.columns:
                value = df["volume"].iloc[-1]
                if pd.notna(value):
                    last_volume = float(value)

        adx_value = getattr(sm, "last_adx_value", None)
        if adx_value is not None:
            try:
                adx_value = float(adx_value)
            except (TypeError, ValueError):
                adx_value = None

        payload = {
            "ticker": ticker, "figi": figi, "signal": signal, "reason": reason,
            "signal_candle_time": candle_time_text,
            "candles_count": int(len(df)) if df is not None else 0,
            "regime": getattr(sm, "_last_regime", None),
            "regime_candidate": getattr(sm, "_last_regime_candidate", None),
            "regime_streak": getattr(sm, "_regime_streak", None),
            "adx": adx_value, "price": float(price),
            "last_close": last_close, "last_volume": last_volume,
        }
        if research_logger.log_event(event_type, payload):
            _strategy_event_keys.add(event_key)
    except Exception as e:
        logging.warning(
            f"[strategy-log] Не удалось записать {event_type} "
            f"для {ticker}: {type(e).__name__}: {e}"
        )


# ============================================================
# PERSISTENCE (state.json)
# ============================================================

def _save_state(monitor: ExecutionMonitor, rm: RiskManager) -> bool:
    try:
        data = {
            "positions": monitor.export_state().get("positions", {}),
            "monitoring_error": monitor.monitoring_error,
            "risk": rm.export_state(),
            # Этап 5: краткий срез реестра заявок для диагностики
            # (источник истины — order_ledger.jsonl, здесь только срез)
            "open_orders": ledger_ref.export_summary() if ledger_ref else [],
        }
        with open(STATE_FILE_TMP, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(STATE_FILE_TMP, STATE_FILE)
        return True
    except Exception as e:
        logging.error(f"[state] Ошибка сохранения: {type(e).__name__}: {e}")
        return False


def _save_state_checked(monitor: ExecutionMonitor, rm: RiskManager) -> bool:
    """
    Этап 5 (D6): результат сохранения контролируется. При ошибке записи
    новые входы блокируются (state_save_failed) до успешного сохранения —
    незаписанное состояние не должно дополняться новыми рисками.
    """
    global state_save_failed
    ok = _save_state(monitor, rm)
    if ok:
        state_save_failed = False
    else:
        if not state_save_failed:
            research_logger.log_event("STATE_SAVE_FAILED", {})
        state_save_failed = True
        logging.error("[state] _save_state не удался — новые входы заблокированы до успешного сохранения")
    return ok


def _load_state(monitor: ExecutionMonitor, rm: RiskManager) -> bool:
    if not os.path.isfile(STATE_FILE):
        logging.warning(f"[state] {STATE_FILE} отсутствует — новые входы запрещены до сверки.")
        return False
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logging.error("[state] Корневой объект не dict")
            return False
        positions = data.get("positions", {})
        if not isinstance(positions, dict):
            logging.error("[state] positions не dict")
            return False
        monitor.import_state({
            "positions": positions,
            "monitoring_error": bool(data.get("monitoring_error", False)),
        })
        risk = data.get("risk", {})
        if isinstance(risk, dict):
            rm.import_state(risk)
        logging.info(f"[state] Загружено позиций: {len(monitor.positions)}")
        return True
    except Exception as e:
        logging.error(f"[state] Ошибка загрузки: {type(e).__name__}: {e}")
        return False


# ============================================================
# RECONCILE (двусторонний)
# ============================================================

async def reconcile(client: TinkoffClient, monitor: ExecutionMonitor,
                    ledger: Optional[OrderLedger] = None, rm: Optional[RiskManager] = None):
    """
    Двусторонняя сверка (этап 5: с адопцией через единую точку регистрации).

    Важные правила:
      - get_positions()=None (ошибка/НЕПОЛНЫЙ снимок после fail-closed в
        tinkoff_client) — учёт НЕ меняется, сверка не завершена (D5).
      - позиция брокера без локального учёта -> регистрируется как RECOVERY
        (единая точка register_recovery, защита от дублей), а не вечный
        MISMATCH (D2). Направление/количество берутся из ДОСТОВЕРНОГО снимка.
      - RECOVERY-позиция, отсутствующая у брокера (достоверный снимок) —
        внешняя очистка: учёт закрывается, CSV-строка не пишется (данных
        входа нет).
      - Количество RECOVERY-позиции синхронизируется с брокером; направление
        не меняется. OPEN/CLOSING/UNCERTAIN — прежние правила.
    """
    global reconcile_done
    positions = await client.get_positions()

    if positions is None:
        logging.error("[reconcile] Портфель недостоверен (None) — учёт НЕ меняется, сверка не завершена.")
        client.uncertain_state = True
        reconcile_done = False
        return

    broker = {f: p for f, p in positions.items()
              if not f.startswith(("RUB", "USD", "EUR"))}

    local = {f: p for f, p in monitor.positions.items()
             if p.state in ACTIVE_STATES}

    mismatches = []
    mutated = False  # этап 5: изменения учёта сразу сохраняются (D6)

    for figi, pdata in broker.items():
        pos = local.get(figi)
        if pos is None:
            # Этап 5 (D2): адопция — позиция брокера без локального учёта.
            info = await client.get_instrument_info(figi)
            ticker = info.get("ticker") if info else figi
            if pdata.direction == "UNKNOWN":
                mismatches.append(f"{figi}: направление брокера=UNKNOWN — адопция невозможна")
                continue
            direction = "BUY" if pdata.direction == "LONG" else "SELL"
            lot = int(info.get("lot")) if info and info.get("lot") else 1
            adopted = monitor.register_recovery(
                figi=figi, ticker=ticker, direction=direction,
                quantity=abs(pdata.lots), lot_size=lot,
                recovery_source="adopted",
            )
            local[figi] = adopted
            mutated = True
            logging.error(
                f"[reconcile] {ticker}: позиция брокера без локального учёта — "
                f"зарегистрирована как RECOVERY ({abs(pdata.lots)} лот, {direction})"
            )
            research_logger.log_event("POSITION_RECOVERED", {
                "figi": figi, "ticker": ticker, "direction": direction,
                "lots": abs(pdata.lots), "source": "reconcile_adoption",
            })
            await notify(
                f"⚠️ <b>{ticker}</b>: позиция у брокера без локального учёта\n"
                f"Зарегистрирована как RECOVERY: {abs(pdata.lots)} лот ({direction})\n"
                f"Новые BUY по инструменту заблокированы. Автозакрытие отключено — решение за оператором."
            )
            pos = adopted

        if pos.state == PositionState.RECOVERY:
            our_side = "LONG" if pos.direction == "BUY" else "SHORT"
            if pdata.direction != our_side:
                mismatches.append(f"{figi}: направление брокера={pdata.direction}, локально={our_side} (RECOVERY)")
                continue
            if monitor.sync_recovery_quantity(pos, pdata.lots):
                mutated = True
                research_logger.log_event("POSITION_RECOVERY_SYNC", {
                    "figi": figi, "ticker": pos.ticker, "lots": pdata.lots,
                })
            continue

        our_side = "LONG" if pos.direction == "BUY" else "SHORT"
        if pdata.direction != our_side:
            mismatches.append(f"{figi}: направление брокера={pdata.direction}, локально={our_side}")
            continue
        if pdata.lots != pos.quantity:
            mismatches.append(f"{figi}: lots брокера={pdata.lots}, локально={pos.quantity}")

    for figi, pos in local.items():
        if figi in broker:
            continue
        if pos.state == PositionState.RECOVERY:
            # Достоверный снимок: позиция исчезла у брокера (внешняя очистка).
            # Учёт закрывается; CSV НЕ пишется (данных входа нет — расчёт PnL невозможен).
            pos.quantity = 0
            pos.state = PositionState.CLOSED
            pos.logged = True
            mutated = True
            logging.warning(
                f"[reconcile] {pos.ticker}: RECOVERY-позиция отсутствует у брокера — учёт закрыт (внешнее закрытие)"
            )
            research_logger.log_event("POSITION_RECOVERY_CLOSED", {
                "figi": figi, "ticker": pos.ticker, "source": "reconcile",
            })
            await notify(
                f"ℹ️ <b>{pos.ticker}</b>: RECOVERY-позиция отсутствует у брокера — учёт закрыт."
            )
            continue
        mismatches.append(f"{figi}: есть локально ({pos.quantity}), нет у брокера")

    if mutated and rm is not None:
        # Этап 5: изменения учёта (адопция/синхронизация/закрытие) сохраняются немедленно;
        # при ошибке записи новые входы блокируются (state_save_failed)
        _save_state_checked(monitor, rm)

    if mismatches:
        client.uncertain_state = True
        reconcile_done = False
        msg = "⚠️ Расхождения: " + "; ".join(mismatches)
        logging.error(f"[reconcile] {msg}")
        research_logger.log_event("RECONCILE_MISMATCH", {"mismatches": mismatches})
        await notify(msg)
        return

    if not local and not broker:
        logging.info("[reconcile] Позиций нет (достоверно).")
    else:
        logging.info(f"[reconcile] Все позиции совпадают ({len(local)} активных).")

    reconcile_done = True
    client.uncertain_state = False
    research_logger.log_event("RECONCILE_OK", {"positions": len(local)})


# ============================================================
# CLOSE / PROCESS CLOSING
# ============================================================

async def close_position(client: TinkoffClient, pos, price: float):
    direction = (
        OrderDirection.ORDER_DIRECTION_SELL if pos.direction == "BUY"
        else OrderDirection.ORDER_DIRECTION_BUY
    )

    # Этап 5: SELL-заявки тоже проходят через реестр (INTENT до сетевого вызова).
    # При ошибке записи закрытие откладывается: позиция остаётся CLOSING ->
    # resolve_closing -> UNCERTAIN -> сверка вернёт её в OPEN и SL/TP повторятся.
    close_order_id = str(uuid.uuid4())
    if ledger_ref is not None:
        intent_ok = ledger_ref.record_intent(
            close_order_id, trade_id=pos.trade_id, figi=pos.figi,
            ticker=pos.ticker, side="SELL", requested_lots=pos.quantity,
            reason=f"close:{pos.close_reason}",
        )
        if not intent_ok:
            logging.error(
                f"[close] {pos.ticker}: INTENT не сохранён — закрытие отложено (fail-closed)"
            )
            return

    result = await client.post_market_order(pos.figi, pos.quantity, direction, order_id=close_order_id)

    if result.state is None:
        pos.state = PositionState.UNCERTAIN
        client.uncertain_state = True
        if ledger_ref is not None:
            ledger_ref.record_uncertain(
                close_order_id, trade_id=pos.trade_id, figi=pos.figi, ticker=pos.ticker,
                side="SELL", requested_lots=pos.quantity,
                reason="post: ответ потерян — закрытие могло быть принято", source="close_position",
                error=str(result.error) if result.error else None,
            )
        logging.error(
            f"[close] {pos.ticker}: state=None (order_id={result.order_id}) — UNCERTAIN. "
            f"Повторная отправка запрещена."
        )
        research_logger.log_event("CLOSE_FAILED", {
            "trade_id": pos.trade_id, "ticker": pos.ticker, "figi": pos.figi,
            "order_id": result.order_id,
        })
        return

    if ledger_ref is not None:
        ledger_ref.record_sent(
            close_order_id, trade_id=pos.trade_id, figi=pos.figi, ticker=pos.ticker,
            side="SELL", requested_lots=pos.quantity,
            executed_lots=int(getattr(result.state, "lots_executed", 0) or 0),
            status_hint=_status_name(getattr(result.state, "execution_report_status", None)),
            source="post_response",
        )

    pos.close_order_id = result.order_id
    pos.close_requested_lots = pos.quantity
    pos.close_requested_at = datetime.now(timezone.utc).isoformat()
    logging.info(
        f"🔒 [CLOSE] {pos.ticker} {pos.close_reason} @ {price:.4f} → CLOSING "
        f"(id={result.order_id}, requested={pos.close_requested_lots})"
    )
    research_logger.log_event("CLOSE_SENT", {
        "trade_id": pos.trade_id,
        "ticker": pos.ticker, "figi": pos.figi, "reason": pos.close_reason,
        "order_id": result.order_id, "price_before_order": price,
        "requested_lots": pos.close_requested_lots,
    })


async def process_closing(client: TinkoffClient, monitor: ExecutionMonitor):
    global reconcile_done

    for pos in list(monitor.positions.values()):
        if pos.state == PositionState.CLOSING:
            await monitor.resolve_closing(pos)

    uncertain_positions = [p for p in monitor.positions.values() if p.state == PositionState.UNCERTAIN]
    if uncertain_positions:
        positions = await client.get_positions()
        if positions is None:
            logging.error("[process_closing] get_positions()=None — UNCERTAIN не разрешаются.")
            client.uncertain_state = True
            reconcile_done = False
        else:
            broker = {f: p for f, p in positions.items()
                      if not f.startswith(("RUB", "USD", "EUR"))}
            for pos in uncertain_positions:
                monitor.resolve_uncertain(pos, broker)
            await reconcile(client, monitor)

    for pos in list(monitor.positions.values()):
        if pos.state != PositionState.CLOSED or pos.logged:
            continue

        trade_id = pos.trade_id or uuid.uuid4().hex[:12]
        if _trade_id_already_logged(trade_id):
            logging.warning(f"[csv] {pos.ticker}: trade_id={trade_id} уже в CSV — помечаю logged=True")
            pos.logged = True
            continue

        exit_price = pos.execution_price or 0.0
        executed_lots = pos.executed_lots
        if executed_lots <= 0:
            logging.error(
                f"[csv] {pos.ticker}: executed_lots={executed_lots} — пропуск записи"
            )
            pos.logged = True
            continue
        shares = executed_lots * pos.lot_size

        entry_slippage = (pos.entry_price - pos.price_before_order) * shares
        exit_slippage = (pos.close_price_before_order - exit_price) * shares
        total_slippage = entry_slippage + exit_slippage

        gross_pnl = (exit_price - pos.entry_price) * shares
        turnover = (exit_price + pos.entry_price) * shares
        model_total_commission = abs(turnover * config.COMMISSION_PER_SIDE)

        if pos.actual_commission is not None:
            exit_commission = pos.actual_commission
        else:
            exit_commission = model_total_commission / 2.0

        if pos.entry_actual_commission is not None:
            entry_commission = pos.entry_actual_commission
        else:
            entry_commission = model_total_commission / 2.0

        total_commission = entry_commission + exit_commission

        entry_actual = pos.entry_actual_commission is not None
        exit_actual = pos.actual_commission is not None
        if entry_actual and exit_actual:
            commission_source = "actual"
        elif entry_actual or exit_actual:
            commission_source = "mixed"
        else:
            commission_source = "model"

        net_pnl = gross_pnl - total_commission
        slippage_percent = (total_slippage / turnover * 100.0) if turnover > 0 else 0.0

        cumulative_before = research_logger.get_cumulative_net_pnl()
        cumulative_after = cumulative_before + net_pnl

        trade_row = {
            "trade_id": trade_id,
            "timestamp_utc": pos.close_requested_at or "",
            "ticker": pos.ticker, "figi": pos.figi, "lot_size": pos.lot_size,
            "side": pos.direction,
            "signal_reason": pos.signal_reason,
            "signal_time": pos.signal_time,
            "price_before_order": pos.price_before_order,
            "order_send_time": pos.order_send_time,
            "order_id": pos.close_order_id or "",
            "execution_status": "FILL_PARTIAL" if pos.partial else "FILL",
            "requested_lots": pos.close_requested_lots or pos.quantity,
            "executed_lots": executed_lots,
            "execution_price": f"{pos.entry_price:.4f}",
            "exit_reason": pos.close_reason or "",
            "exit_price": f"{exit_price:.4f}",
            "exit_time": datetime.now(timezone.utc).isoformat(),
            "commission_rub": f"{total_commission:.2f}",
            "commission_source": commission_source,
            "actual_commission_rub": f"{pos.actual_commission:.2f}" if pos.actual_commission is not None else "",
            "entry_commission_rub": f"{entry_commission:.2f}",
            "exit_commission_rub": f"{exit_commission:.2f}",
            "slippage_rub": f"{total_slippage:+.2f}",
            "slippage_percent": f"{slippage_percent:+.3f}",
            "gross_pnl_rub": f"{gross_pnl:.2f}",
            "net_pnl_rub": f"{net_pnl:.2f}",
            "cumulative_net_pnl_rub": f"{cumulative_after:.2f}",
            "error_note": "",
        }

        if research_logger.log_trade(trade_row):
            pos.logged = True
            logging.info(
                f"✅ [CLOSED] {pos.ticker} {pos.close_reason} "
                f"entry={pos.entry_price:.4f} exit={exit_price:.4f} "
                f"lots={executed_lots} gross={gross_pnl:+.2f}₽ "
                f"net={net_pnl:+.2f}₽ cum={cumulative_after:+.2f}₽"
            )
            try:
                research_logger.log_event("CLOSED", {
                    "trade_id": trade_id, "ticker": pos.ticker, "figi": pos.figi,
                    "entry_price": pos.entry_price, "exit_price": exit_price,
                    "executed_lots": executed_lots, "lot_size": pos.lot_size,
                    "gross_pnl": gross_pnl,
                    "entry_commission": entry_commission,
                    "exit_commission": exit_commission,
                    "commission_source": commission_source,
                    "slippage": total_slippage, "slippage_percent": slippage_percent,
                    "net_pnl": net_pnl, "cumulative_net_pnl": cumulative_after,
                    "exit_reason": pos.close_reason or "",
                })
            except Exception as e:
                logging.warning(f"[jsonl] {pos.ticker}: не удалось записать CLOSED: {type(e).__name__}: {e}")
            await notify(
                f"🔒 <b>{pos.ticker}</b> закрыто по {pos.close_reason}\n"
                f"Вход: {pos.entry_price:.4f} → Выход: {exit_price:.4f}\n"
                f"Лотов: {executed_lots} (акций: {shares})\n"
                f"Net: <b>{net_pnl:+.2f} ₽</b>"
            )
        else:
            logging.error(f"[csv] {pos.ticker}: log_trade вернул False — повтор на следующем цикле.")


# ============================================================
# ЭТАП 5: ЕДИНАЯ ТОЧКА РЕГИСТРАЦИИ ИСПОЛНЕННОЙ ПОКУПКИ,
#         ОПРОС НЕЗАВЕРШЁННЫХ ЗАЯВОК, ВОССТАНОВЛЕНИЕ RECOVERY
# ============================================================

async def _build_candles_df(client: TinkoffClient, figi: str):
    """
    Свечи для расчёта SL/TP — та же конфигурация, что при входе
    (CANDLES_LOOKBACK_DAYS, 5-минутный интервал). Стратегия не меняется.
    Возвращает DataFrame или None (мало свечей/ошибка).
    """
    candles = await client.get_historical_candles(
        figi, days=config.CANDLES_LOOKBACK_DAYS,
        interval=CandleInterval.CANDLE_INTERVAL_5_MIN,
    )
    if len(candles) < config.MIN_CANDLES_FOR_SIGNAL:
        return None
    df = pd.DataFrame([{
        'time': c.time,
        'open': float(c.open.units + c.open.nano / 1e9),
        'high': float(c.high.units + c.high.nano / 1e9),
        'low': float(c.low.units + c.low.nano / 1e9),
        'close': float(c.close.units + c.close.nano / 1e9),
        'volume': int(c.volume),
        'is_complete': getattr(c, 'is_complete', None),
    } for c in candles])
    return df.set_index('time').sort_index()


async def register_executed_buy(
    client: TinkoffClient,
    monitor: ExecutionMonitor,
    rm: RiskManager,
    ledger: Optional[OrderLedger],
    *,
    figi: str,
    ticker: str,
    order_id: str,
    trade_id: str,
    reason: str,
    signal_time: str,
    price_before_order: float,
    order_send_time: str,
    requested_lots: int,
    executed_lots: int,
    exec_price,
    lot_size: int,
    entry_commission=None,
    partial: bool = False,
    df=None,
    source: str = "",
    ledger_status: str = "FILL",
):
    """
    ЕДИНАЯ ТОЧКА регистрации исполненной BUY-заявки (этап 5, P0).

    Гарантии:
      - подтверждённое исполнение (executed_lots > 0) не может быть потеряно:
        при отсутствии цены исполнения или SL/TP позиция регистрируется в
        RECOVERY (никогда не «continue молча»);
      - неизвестная цена входа НЕ заменяется произвольной (entry_price=0 —
        явная отметка «неизвестно»);
      - SL/TP рассчитываются ТОЛЬКО штатной стратегией (rm.calculate_sl_tp на
        свечах); без df (опрос/стартовое восстановление) регистрация идёт в
        RECOVERY, а расчёт выполняет promote_recovery_positions после загрузки
        свечей;
      - идемпотентность: активная позиция по FIGI не перезаписывается;
      - RECOVERY не закрывается автоматически.
    """
    if not executed_lots or executed_lots <= 0:
        logging.error(
            f"[register] {ticker}: executed_lots={executed_lots} — исполнение не "
            f"подтверждено, регистрация невозможна (order_id={order_id})"
        )
        return None

    if not figi:
        # Партия P0-2: исполнение с ПУСТЫМ (неопределённым) FIGI не может быть
        # зарегистрировано как обычная BUY-позиция — неизвестно, какой инструмент
        # затронут. Заявка остаётся UNCERTAIN (НЕ отказ), новые BUY запрещены
        # ГЛОБАЛЬНО до разрешения (has_unknown_figi_open_orders в trading_cycle);
        # существующие позиции продолжают мониториться.
        logging.error(
            f"[register] {ticker}: figi отсутствует при подтверждённом исполнении "
            f"{executed_lots} лот. (order_id={order_id}) — авторегистрация позиции "
            f"ЗАПРЕЩЕНА: UNCERTAIN + глобальная блокировка новых BUY"
        )
        client.uncertain_state = True
        if ledger is not None and order_id:
            ledger.record_uncertain(
                order_id, trade_id=trade_id, figi=None, ticker=ticker,
                side="BUY", requested_lots=requested_lots,
                executed_lots=executed_lots, avg_exec_price=exec_price,
                reason="исполнение без FIGI — авторегистрация позиции запрещена, "
                       "требуется разрешение оператором",
                source=source or "register_executed_buy",
            )
        research_logger.log_event("BUY_UNKNOWN_FIGI", {
            "order_id": order_id, "ticker": ticker,
            "executed_lots": executed_lots, "source": source,
        })
        await notify(
            "⛔ Исполнение с НЕИЗВЕСТНЫМ FIGI\n"
            f"Исполнено лотов: {executed_lots} (order_id={order_id or '—'})\n"
            "Позиция НЕ зарегистрирована автоматически (инструмент неизвестен).\n"
            "Новые BUY запрещены ГЛОБАЛЬНО до разрешения. Требуется оператор."
        )
        return None

    # Партия P0-2: защита от «воскрешения» закрытой позиции. Терминальная
    # заявка, чьё исполнение УЖЕ применено к учёту (executed_lots в записи
    # реестра совпадает с сообщённым брокером), без активной позиции не
    # регистрируется повторно. Рост lots_executed сверх учтённого — это
    # ПОЗДНЕЕ исполнение: обрабатывается штатно ниже. Страховка при
    # расхождении — reconcile-адопция по достоверному снимку брокера.
    rec_pre = ledger.latest(order_id) if (ledger is not None and order_id) else None
    if (rec_pre is not None
            and rec_pre.get("status") in _TERMINAL_ORDER_STATUSES
            and not monitor.has_active_position(figi)):
        recorded_le = int(rec_pre.get("executed_lots") or 0)
        if executed_lots <= recorded_le:
            logging.warning(
                f"[register] {ticker}: исполнение заявки {order_id} ({executed_lots} лот.) "
                f"уже применено к учёту (в реестре {recorded_le}), активной позиции нет — "
                f"повторная регистрация не выполняется (защита от «воскрешения»)"
            )
            return None

    if monitor.has_active_position(figi):
        existing = monitor.get_position(figi)
        logging.warning(
            f"[register] {ticker}: активная позиция уже есть "
            f"(state={existing.state.value}, order={existing.entry_order_id}) — "
            f"дубль не создаётся (order_id={order_id})"
        )
        # Этап 5: связываем усыновлённую (без order_id) RECOVERY-позицию
        # с подтверждённой заявкой — открывает путь промоции к OPEN
        if (existing.state == PositionState.RECOVERY
                and not existing.entry_order_id and order_id):
            existing.entry_order_id = order_id
            logging.info(
                f"[register] {ticker}: RECOVERY-позиция связана с заявкой "
                f"{order_id} — восстановление цены входа возможно"
            )
        qty_before = existing.quantity
        applied = False
        if existing.state == PositionState.RECOVERY and executed_lots != existing.quantity:
            # Поздний рост исполнения при RECOVERY: каноническая синхронизация
            # количества (дефект №6: знак отдельно, количество положительное).
            applied = monitor.sync_recovery_quantity(existing, executed_lots)
        elif existing.state == PositionState.OPEN:
            # Партия P0-2: поздние дополнительные лоты ПОСЛЕ перехода в OPEN
            # (например, отчёт брокера обновился после терминального CANCELLED).
            applied = monitor.apply_late_entry_lots(existing, executed_lots, order_id)
        if ledger is not None and order_id:
            rec = ledger.latest(order_id)
            if applied:
                ledger.record_status(
                    order_id, ledger_status, executed_lots=executed_lots,
                    avg_exec_price=exec_price,
                    reason=f"позднее исполнение применено к позиции "
                           f"(+{existing.quantity - qty_before} лот., итого {existing.quantity})",
                    source=source or "register_executed_buy",
                )
                _save_state_checked(monitor, rm)
                research_logger.log_event("POSITION_ENTRY_LATE_FILL", {
                    "figi": figi, "ticker": ticker, "order_id": order_id,
                    "added_lots": existing.quantity - qty_before,
                    "quantity": existing.quantity,
                    "state": existing.state.value,
                    "entry_price": existing.entry_price,
                    "sl": existing.sl_price, "tp": existing.tp_price,
                    "source": source,
                })
                if existing.state == PositionState.OPEN:
                    await notify(
                        f"⚠️ <b>{ticker}</b>: позднее исполнение входной заявки\n"
                        f"Добавлено лотов: +{existing.quantity - qty_before} "
                        f"(итого {existing.quantity})\n"
                        f"Цена входа: {existing.entry_price:.4f} (не менялась)\n"
                        f"SL: {existing.sl_price:.4f}\nTP: {existing.tp_price:.4f} — сохранены"
                    )
            elif rec is not None and rec["status"] not in _TERMINAL_ORDER_STATUSES:
                ledger.record_status(
                    order_id, ledger_status, executed_lots=executed_lots,
                    avg_exec_price=exec_price,
                    reason="позиция уже зарегистрирована — идемпотентность",
                    source=source or "register_executed_buy",
                )
        return existing

    pos = None
    recovery_source = None
    sl_price = None
    tp_price = None
    if exec_price is not None and exec_price > 0:
        if df is not None:
            # Дефект №7: исключение rm.calculate_sl_tp() НЕ срывает регистрацию
            # подтверждённого исполнения — позиция создаётся как RECOVERY
            # (без фиктивной цены входа и без фиктивных SL/TP).
            try:
                sl_price, tp_price = rm.calculate_sl_tp(exec_price, "BUY", df)
            except Exception as e:
                logging.error(
                    f"[register] {ticker}: ОШИБКА расчёта SL/TP: {type(e).__name__}: {e} — "
                    f"исполнение {executed_lots} лот. регистрируется как RECOVERY "
                    f"(без фиктивных цены/SL/TP, автозакрытие запрещено)"
                )
                research_logger.log_event("RECOVERY_SLTP_ERROR", {
                    "figi": figi, "ticker": ticker, "order_id": order_id,
                    "executed_lots": executed_lots, "partial": partial,
                    "error": f"{type(e).__name__}: {e}",
                })
                sl_price, tp_price = None, None
        else:
            # Свечей нет (опрос/старт): SL/TP считаются только по стратегии на
            # свечах — сначала RECOVERY, расчёт после загрузки свечей.
            sl_price, tp_price = None, None
        if sl_price is not None and tp_price is not None:
            try:
                pos = monitor.register(
                    figi=figi, ticker=ticker, direction="BUY",
                    entry_price=exec_price, quantity=executed_lots,
                    sl_price=sl_price, tp_price=tp_price, lot_size=lot_size,
                    trade_id=trade_id, signal_reason=reason,
                    signal_time=signal_time, price_before_order=price_before_order,
                    order_send_time=order_send_time,
                    entry_requested_lots=requested_lots,
                    entry_applied_lots=executed_lots,
                    entry_order_id=order_id,
                )
                pos.execution_price = exec_price
                pos.executed_lots = executed_lots
                pos.partial = partial
                pos.entry_actual_commission = entry_commission
            except Exception as e:
                # Дефект №7 (защита в глубину): даже сбой register() не должен
                # терять подтверждённое исполнение — fallback в RECOVERY.
                logging.error(
                    f"[register] {ticker}: ОШИБКА register(): {type(e).__name__}: {e} — "
                    f"исполнение {executed_lots} лот. регистрируется как RECOVERY"
                )
                research_logger.log_event("RECOVERY_REGISTER_ERROR", {
                    "figi": figi, "ticker": ticker, "order_id": order_id,
                    "executed_lots": executed_lots,
                    "error": f"{type(e).__name__}: {e}",
                })
                pos = None
                recovery_source = "register_error"
        else:
            recovery_source = "no_sl_tp"
    else:
        recovery_source = "no_price"

    if pos is None:
        pos = monitor.register_recovery(
            figi=figi, ticker=ticker, direction="BUY",
            quantity=executed_lots, lot_size=lot_size,
            trade_id=trade_id, order_id=order_id,
            signal_reason=reason, signal_time=signal_time,
            price_before_order=price_before_order, order_send_time=order_send_time,
            requested_lots=requested_lots, partial=partial,
            entry_commission=entry_commission,
            execution_price=exec_price if (exec_price is not None and exec_price > 0) else None,
            recovery_source=recovery_source,
        )

    rm.record_trade(figi)
    state_saved = _save_state_checked(monitor, rm)

    if pos.state == PositionState.OPEN:
        logging.info(
            f"🚀 [BUY] {ticker} {executed_lots} лот. @ {exec_price:.4f} "
            f"SL={pos.sl_price:.4f} TP={pos.tp_price:.4f} "
            f"entry_comm={entry_commission if entry_commission is not None else 'model'} "
            f"(reason: {reason})"
        )
        research_logger.log_event("POSITION_OPENED", {
            "trade_id": trade_id, "ticker": ticker, "figi": figi,
            "order_id": order_id, "exec_price": exec_price,
            "exec_lots": executed_lots, "sl": pos.sl_price, "tp": pos.tp_price,
            "entry_commission": entry_commission,
        })
        if partial:
            await notify(
                f"⚠️ <b>{ticker}</b> частичное исполнение BUY\n"
                f"Исполнено: {executed_lots} лот. @ {exec_price:.4f}\n"
                f"SL: {pos.sl_price:.4f}\nTP: {pos.tp_price:.4f}\n"
                f"<i>{reason}</i>"
            )
        else:
            await notify(
                f"🚀 <b>{ticker}</b> BUY\n"
                f"Исполнено: {executed_lots} лот. @ {exec_price:.4f}\n"
                f"SL: {pos.sl_price:.4f}\nTP: {pos.tp_price:.4f}\n"
                f"<i>{reason}</i>"
            )
    else:
        research_logger.log_event("POSITION_RECOVERED", {
            "trade_id": trade_id, "ticker": ticker, "figi": figi,
            "order_id": order_id, "exec_lots": executed_lots,
            "recovery_source": pos.recovery_source, "partial": partial,
            "source": source, "state_saved": state_saved,
        })
        await notify(
            f"⚠️ <b>{ticker}</b> BUY исполнен ({executed_lots} лот.), "
            f"цена входа/SL/TP уточняются\n"
            f"Позиция зарегистрирована как RECOVERY — контроль сохранён, "
            f"автозакрытие отключено."
        )

    if ledger is not None and order_id:
        ledger.record_status(
            order_id, ledger_status, trade_id=trade_id, figi=figi, ticker=ticker,
            side="BUY", requested_lots=requested_lots,
            executed_lots=executed_lots, avg_exec_price=exec_price,
            reason=("позиция зарегистрирована; RECOVERY: " + str(pos.recovery_source))
                   if pos.state == PositionState.RECOVERY else "позиция зарегистрирована",
            source=source or "register_executed_buy",
        )
    return pos


async def poll_open_orders(client: TinkoffClient, monitor: ExecutionMonitor,
                           rm: RiskManager, ledger: OrderLedger):
    """
    Пошаговый (по одной заявке за цикл, без блокировки цикла) контроль
    незавершённых BUY-заявок из реестра (этап 5).

    Правила разрешения:
      - терминальный статус с исполнением -> регистрация через единую точку
        (RECOVERY при неполных данных); для CANCELLED/EXPIRED с исполненными
        лотами статус заявки сохраняется как CANCELLED/EXPIRED с частичным
        исполнением (дефект №2: статус и факт исполнения учитываются
        раздельно, в позицию идёт только реально исполненное количество);
      - FILL с lots_executed=0 — противоречие: UNCERTAIN, НЕ терминальный
        FILL, НЕ отказ (дефект №8);
      - терминальный статус БЕЗ исполнения (достоверное подтверждение брокера)
        -> заявка закрыта в реестре, BUY по FIGI разблокируется;
      - статус недоступен/заявка не найдена -> НЕ отказ: заявка остаётся в
        реестре (UNCERTAIN-логика), новые BUY по FIGI блокированы;
      - PARTIALLYFILL или NEW старше ORDER_POLL_MAX_AGE_CYCLES -> сначала
        регистрация подтверждённых лотов, затем отмена остатка (дефект №1);
      - заявки с неизвестной стороной (side=None) опрашиваются: завершение
        без исполнения разрешает блокировку; признаки исполнения при
        неизвестной стороне остаются UNCERTAIN для оператора (дефект №4);
      - партия P0-2: терминальные заявки С ИСПОЛНЕНИЕМ (CANCELLED/EXPIRED с
        executed_lots>0, FILL при исполнении меньше запрошенного) остаются
        доступны для повторной сверки (order_ledger.open_orders) — поздний
        рост lots_executed после терминального статуса не теряется;
        повторная регистрация уже применённого исполнения без активной
        позиции запрещена («воскрешение» закрытой позиции).
    """
    for rec in list(ledger.open_orders(side="BUY")):
        order_id = rec["order_id"]
        figi = rec.get("figi") or ""
        ticker = rec.get("ticker") or figi or "?"

        state = await client.get_sandbox_order_state(order_id)
        if state is None:
            logging.warning(
                f"[poll] {ticker}: статус заявки {order_id} недоступен — заявка "
                f"остаётся в реестре (неопределённость, НЕ отказ)"
            )
            continue

        status = _status_name(getattr(state, "execution_report_status", None))
        le = int(getattr(state, "lots_executed", 0) or 0)

        # Достоверный отказ без исполнения (согласовано с владельцем)
        if status in ("REJECTED", "CANCELLED", "EXPIRED") and le <= 0:
            logging.warning(f"[poll] {ticker}: заявка {order_id} отклонена/отменена без исполнения — закрыта в реестре")
            ledger.record_status(order_id, status, executed_lots=le,
                                 reason="достоверный отказ без исполнения", source="poll")
            continue

        # Дефект №8: FILL без подтверждённых исполненных лотов — противоречие.
        # НЕ терминальный FILL и НЕ отказ: заявка остаётся UNCERTAIN в реестре
        # (блокирует BUY по FIGI), разрешается повторным запросом состояния.
        if status == "FILL" and le <= 0:
            logging.error(
                f"[poll] {ticker}: FILL, но lots_executed={le} — противоречие, "
                f"UNCERTAIN (заявка остаётся в реестре для повторного разрешения)"
            )
            client.uncertain_state = True
            ledger.record_uncertain(order_id, figi=figi, ticker=ticker,
                                    side="BUY", executed_lots=le,
                                    reason="FILL противоречие: lots_executed=0 при опросе",
                                    source="poll")
            continue

        # Партия P0-2: терминальная запись реестра при терминальном статусе
        # брокера и БЕЗ активной позиции. Исполнение, уже применённое к учёту
        # (executed_lots записи), повторно не регистрируется («воскрешение»
        # закрытой позиции запрещено). Поздний рост lots_executed сверх
        # учтённого — расхождение учёта с брокером: разрешается СВЕРКОЙ
        # (адопция по ДОСТОВЕРНОМУ снимку брокера показывает фактический
        # остаток), а не автоматической регистрацией по отчёту заявки.
        if (rec.get("status") in _TERMINAL_ORDER_STATUSES
                and status in _TERMINAL_ORDER_STATUSES
                and not monitor.has_active_position(figi)):
            recorded_le = int(rec.get("executed_lots") or 0)
            if le <= recorded_le:
                continue
            logging.error(
                f"[poll] {ticker}: поздний рост исполнения заявки {order_id} "
                f"({recorded_le} -> {le} лот.) при закрытой/отсутствующей позиции — "
                f"требуется сверка с брокером (адопция по снимку), "
                f"авторегистрация по отчёту заявки не выполняется"
            )
            client.uncertain_state = True
            continue

        if status == "FILL" or le > 0 or status == "PARTIALLYFILL":
            info = await client.get_instrument_info(figi) if figi else None
            lot = int(info.get("lot")) if info and info.get("lot") else 1
            exec_price = ExecutionMonitor._extract_price(state, le, lot)
            entry_commission = ExecutionMonitor._extract_commission(state)
            partial_like = status in ("PARTIALLYFILL", "NEW") or (status is None and le > 0)
            terminal_cancelled = status in ("CANCELLED", "EXPIRED")
            if partial_like:
                # Живой остаток (частичное исполнение или активная NEW): отмена по возрасту
                already_cancel = "cancel_requested" in (rec.get("reason") or "")
                age = _age_in_cycles(rec)
                need_cancel = status == "PARTIALLYFILL" or age >= ORDER_POLL_MAX_AGE_CYCLES
                if need_cancel and not already_cancel:
                    logging.warning(
                        f"[poll] {ticker}: заявка {order_id} жива "
                        f"({le} лот., возраст {age:.0f} циклов, status={status}) — отмена остатка"
                    )
                    # Дефект №1 (принцип): подтверждённое исполнение регистрируется
                    # ДО отмены остатка — отмена не должна потерять исполненные лоты.
                    if le > 0:
                        await register_executed_buy(
                            client, monitor, rm, ledger,
                            figi=figi, ticker=ticker, order_id=order_id,
                            trade_id=rec.get("trade_id") or "",
                            reason=rec.get("reason") or "",
                            signal_time=rec.get("created_at_utc") or rec.get("timestamp_utc") or "",
                            price_before_order=0.0,
                            order_send_time=rec.get("timestamp_utc") or "",
                            requested_lots=int(rec.get("requested_lots") or 0),
                            executed_lots=le, exec_price=exec_price, lot_size=lot,
                            entry_commission=entry_commission, partial=True, df=None,
                            source="poll", ledger_status=status or "PARTIALLYFILL",
                        )
                    cancel_resp = await client.cancel_order(order_id)
                    if cancel_resp is None:
                        ledger.record_uncertain(order_id, figi=figi, ticker=ticker,
                                                side="BUY", executed_lots=le,
                                                reason="cancel не подтверждён", source="poll")
                    else:
                        ledger.record_status(order_id, status or "PARTIALLYFILL",
                                             executed_lots=le,
                                             reason="cancel_requested: ожидание терминального статуса",
                                             source="poll")
                    continue
                if need_cancel and already_cancel:
                    # отменяли — ждём терминальный статус в следующих циклах
                    continue
                # Молодая NEW с исполнением: регистрируем исполненную часть сразу
                await register_executed_buy(
                    client, monitor, rm, ledger,
                    figi=figi, ticker=ticker, order_id=order_id,
                    trade_id=rec.get("trade_id") or "",
                    reason=rec.get("reason") or "",
                    signal_time=rec.get("created_at_utc") or rec.get("timestamp_utc") or "",
                    price_before_order=0.0,
                    order_send_time=rec.get("timestamp_utc") or "",
                    requested_lots=int(rec.get("requested_lots") or 0),
                    executed_lots=le, exec_price=exec_price, lot_size=lot,
                    entry_commission=entry_commission, partial=True, df=None,
                    source="poll", ledger_status=status or "PARTIALLYFILL",
                )
                continue

            if terminal_cancelled:
                # Дефект №2: CANCELLED/EXPIRED с исполненными лотами — это
                # отменённая/истёкшая заявка С ЧАСТИЧНЫМ ИСПОЛНЕНИЕМ, а не
                # «полное исполнение». Статус заявки и факт исполнения
                # учитываются раздельно: в позицию регистрируется только
                # реально исполненное количество (le), неисполненный остаток
                # НЕ попадает в позицию; повторный опрос не дублирует позицию
                # и не прибавляет уже учтённые лоты (идемпотентность
                # register_executed_buy + sync для RECOVERY — покрывает и
                # позднее исполнение после отмены).
                logging.warning(
                    f"[poll] {ticker}: заявка {order_id} {status} с частичным "
                    f"исполнением {le} лот — регистрирую исполненную часть "
                    f"(статус заявки сохраняется как {status})"
                )
                await register_executed_buy(
                    client, monitor, rm, ledger,
                    figi=figi, ticker=ticker, order_id=order_id,
                    trade_id=rec.get("trade_id") or "",
                    reason=rec.get("reason") or "",
                    signal_time=rec.get("created_at_utc") or rec.get("timestamp_utc") or "",
                    price_before_order=0.0,
                    order_send_time=rec.get("timestamp_utc") or "",
                    requested_lots=int(rec.get("requested_lots") or 0),
                    executed_lots=le, exec_price=exec_price, lot_size=lot,
                    entry_commission=entry_commission, partial=True, df=None,
                    source="poll", ledger_status=status,
                )
                continue

            # FILL: полная регистрация через единую точку
            await register_executed_buy(
                client, monitor, rm, ledger,
                figi=figi, ticker=ticker, order_id=order_id,
                trade_id=rec.get("trade_id") or "",
                reason=rec.get("reason") or "",
                signal_time=rec.get("created_at_utc") or rec.get("timestamp_utc") or "",
                price_before_order=0.0,
                order_send_time=rec.get("timestamp_utc") or "",
                requested_lots=int(rec.get("requested_lots") or 0),
                executed_lots=le, exec_price=exec_price, lot_size=lot,
                entry_commission=entry_commission, partial=False, df=None,
                source="poll", ledger_status="FILL",
            )
            continue

        # NEW без исполнения — заявка жива: наблюдение, при старении отмена остатка
        if status == "NEW":
            already_cancel = "cancel_requested" in (rec.get("reason") or "")
            age = _age_in_cycles(rec)
            if age >= ORDER_POLL_MAX_AGE_CYCLES and not already_cancel:
                logging.warning(
                    f"[poll] {ticker}: заявка {order_id} не исполняется "
                    f"(возраст {age:.0f} циклов) — отмена остатка"
                )
                cancel_resp = await client.cancel_order(order_id)
                if cancel_resp is None:
                    ledger.record_uncertain(order_id, figi=figi, ticker=ticker,
                                            side="BUY", executed_lots=le,
                                            reason="cancel не подтверждён", source="poll")
                else:
                    ledger.record_status(order_id, "NEW", executed_lots=le,
                                         reason="cancel_requested: ожидание терминального статуса",
                                         source="poll")
                continue
            ledger.record_status(order_id, "NEW", executed_lots=le,
                                 reason="активна, под наблюдением опроса", source="poll")
            continue

        # Неизвестный/нестандартный статус — не теряем заявку
        logging.warning(f"[poll] {ticker}: нестандартный статус {status!r} заявки {order_id} — остаётся в реестре")
        ledger.record_status(order_id, "NEW" if status is None else status, executed_lots=le,
                             reason=f"нестандартный статус {status!r}", source="poll")

    # Дефект №4: заявки с НЕИЗВЕСТНОЙ стороной (side=None, например из
    # стартовой сверки с брокером) тоже опрашиваются — иначе неопределённость
    # не разрешится никогда. Позицию из такой заявки автоматически НЕ
    # регистрируем (направление неизвестно); терминальное завершение БЕЗ
    # исполнения — достоверное разрешение (блокировка снимается); признаки
    # исполнения при неизвестной стороне остаются UNCERTAIN (решение — оператор).
    for rec in list(ledger.open_orders()):
        if rec.get("side") is not None:
            continue
        order_id = rec["order_id"]
        figi = rec.get("figi") or ""
        ticker = rec.get("ticker") or figi or "?"

        state = await client.get_sandbox_order_state(order_id)
        if state is None:
            # статус недоступен — остаётся в неопределённости, блокировка сохраняется
            continue

        status = _status_name(getattr(state, "execution_report_status", None))
        le = int(getattr(state, "lots_executed", 0) or 0)

        if status in ("REJECTED", "CANCELLED", "EXPIRED") and le <= 0:
            ledger.record_status(order_id, status, executed_lots=le,
                                 reason="достоверное завершение без исполнения "
                                        "(сторона неизвестна) — блокировка снята",
                                 source="poll_unknown")
            continue
        if status == "FILL" or le > 0 or status == "PARTIALLYFILL":
            logging.error(
                f"[poll] {ticker}: заявка {order_id} с НЕИЗВЕСТНОЙ стороной имеет "
                f"признаки исполнения (status={status}, lots_executed={le}) — "
                f"остаётся UNCERTAIN, требуется разрешение оператором; "
                f"BUY заблокированы, позиции продолжают мониториться"
            )
            client.uncertain_state = True
            ledger.record_uncertain(order_id, figi=figi, ticker=ticker, side=None,
                                    executed_lots=le,
                                    reason="исполнение при неизвестной стороне — "
                                           "авторегистрация запрещена, требуется оператор",
                                    source="poll_unknown")
            continue
        ledger.record_status(order_id, status or "NEW", executed_lots=le,
                             reason="активна (сторона неизвестна)", source="poll_unknown")


async def promote_recovery_positions(client: TinkoffClient, monitor: ExecutionMonitor, rm: RiskManager):
    """
    Восстановление цены входа для RECOVERY-позиций (этап 5).

    Если у позиции есть entry_order_id — запрашивается статус заявки; при
    ТЕРМИНАЛЬНОМ статусе заявки (партия P0-2: только FILL/CANCELLED/REJECTED/
    EXPIRED — пока заявка жива (NEW/PARTIALLYFILL), возможны дополнительные
    исполнения и промоция зафиксировала бы неполное количество) и появлении
    цены исполнения загружаются свечи, SL/TP рассчитываются штатной
    стратегией, позиция переводится в OPEN. Перед промоцией количество
    доуточняется по финальному lots_executed (sync_recovery_quantity).

    Позиция БЕЗ order_id (адоптированная из reconcile/старта) остаётся в
    RECOVERY под контролем оператора: цена не заменяется произвольной,
    автоматическое закрытие ЗАПРЕЩЕНО.
    """
    for pos in list(monitor.positions.values()):
        if pos.state != PositionState.RECOVERY:
            continue
        if not pos.entry_order_id:
            _promote_throttle["n"] += 1
            if _promote_throttle["n"] % 20 == 1:
                logging.warning(
                    f"[recovery] {pos.ticker}: RECOVERY без order_id "
                    f"(source={pos.recovery_source}) — цена входа не восстанавливается "
                    f"автоматически; закрытие только вручную оператором"
                )
            continue

        state = await client.get_sandbox_order_state(pos.entry_order_id)
        if state is None:
            continue

        # Партия P0-2: промоция ТОЛЬКО при терминальном статусе заявки.
        # Пока заявка жива (NEW/PARTIALLYFILL), возможны дополнительные
        # исполнения: количество продолжает синхронизироваться опросом/
        # reconcile, позиция остаётся в RECOVERY.
        status = _status_name(getattr(state, "execution_report_status", None))
        if status not in _TERMINAL_ORDER_STATUSES:
            _promote_throttle["alive"] += 1
            if _promote_throttle["alive"] % 20 == 1:
                logging.info(
                    f"[recovery] {pos.ticker}: заявка {pos.entry_order_id} ещё активна "
                    f"(status={status}) — возможны дополнительные исполнения, "
                    f"промоция отложена до терминального статуса"
                )
            continue

        broker_le = int(getattr(state, "lots_executed", 0) or 0)
        # Финальная синхронизация количества ПЕРЕД промоцией: лоты, исполненные
        # до терминального статуса (в т.ч. поздний рост отчёта), попадают в
        # позицию. Противоречие количества — оператор, промоция отложена.
        if broker_le > 0 and broker_le != pos.quantity:
            if not monitor.sync_recovery_quantity(pos, broker_le):
                continue

        le = broker_le or pos.executed_lots or pos.quantity
        exec_price = ExecutionMonitor._extract_price(state, le, pos.lot_size)
        if not exec_price or exec_price <= 0:
            continue

        df = await _build_candles_df(client, pos.figi)
        if df is None:
            logging.warning(f"[recovery] {pos.ticker}: цена восстановлена ({exec_price:.4f}), свечей мало — остаётся RECOVERY")
            continue

        sl_price, tp_price = rm.calculate_sl_tp(exec_price, pos.direction, df)
        if sl_price is None or tp_price is None:
            logging.warning(f"[recovery] {pos.ticker}: SL/TP не рассчитаны — остаётся RECOVERY")
            continue

        if monitor.promote_recovery(pos, exec_price, sl_price, tp_price, execution_price=exec_price):
            logging.warning(
                f"[recovery] {pos.ticker}: RECOVERY→OPEN entry={exec_price:.4f} "
                f"SL={sl_price:.4f} TP={tp_price:.4f} qty={pos.quantity}"
            )
            research_logger.log_event("POSITION_RECOVERY_RESOLVED", {
                "ticker": pos.ticker, "figi": pos.figi, "order_id": pos.entry_order_id,
                "exec_price": exec_price, "exec_lots": pos.quantity,
                "sl": sl_price, "tp": tp_price,
            })
            _save_state_checked(monitor, rm)
            await notify(
                f"✅ <b>{pos.ticker}</b>: RECOVERY разрешена\n"
                f"Цена входа: {exec_price:.4f}\nSL: {sl_price:.4f}\nTP: {tp_price:.4f}\n"
                f"Лотов: {pos.quantity} — позиция под штатным контролем."
            )


async def startup_recovery(client: TinkoffClient, monitor: ExecutionMonitor,
                           rm: RiskManager, ledger: OrderLedger):
    """
    Стартовая четырёхсторонняя сверка (этап 5, P1-1/P1-5):
    OrderLedger × state.json (уже загружен в monitor) × активные заявки
    брокера × позиции брокера.

    - заявка реестра без терминального статуса -> запрос статуса по order_id;
      исполнена -> регистрация позиции (RECOVERY при неполных данных);
      достоверный отказ без исполнения -> заявка закрыта (BUY разблокируется);
      статус недоступен/заявка не найдена -> UNCERTAIN (НЕ отказ), BUY по FIGI
      блокирован до разрешения;
    - активная заявка брокера без записи в реестре -> UNCERTAIN в реестре
      (неизвестная заявка), BUY по её FIGI блокирован;
    - позиция брокера без локального учёта -> RECOVERY (адопция).
    Торговых действий не выполняет — только восстановление учёта.
    """
    summary = {
        "positions_adopted": 0,
        "positions_unavailable": False,
        "unknown_broker_orders": 0,
        "orders_unavailable": False,
        "orders_executed": 0,
        "orders_resolved": 0,
        "orders_uncertain": 0,
        "orders_active": 0,
    }

    # 1. Нетерминальные заявки реестра: разрешение по статусу брокера
    #    (сначала заявки — чтобы связать позицию с order_id через единую точку)
    for rec in list(ledger.open_orders()):
        oid = rec["order_id"]
        side = rec.get("side")
        figi = rec.get("figi") or ""
        ticker = rec.get("ticker") or figi or "?"

        # Дефект №4: сторона неизвестна — автоматическая регистрация позиции
        # как BUY ЗАПРЕЩЕНА (нельзя разрешать неопределённость «по умолчанию»
        # в сторону покупки). Заявка остаётся UNCERTAIN: BUY по её FIGI (или
        # глобально, если FIGI неизвестен) блокированы до разрешения.
        if side is None:
            ledger.record_uncertain(
                oid, figi=figi, ticker=ticker, side=None,
                requested_lots=int(rec.get("requested_lots") or 0),
                executed_lots=int(rec.get("executed_lots") or 0),
                reason="сторона заявки неизвестна — авторегистрация запрещена, "
                       "требуется разрешение оператором",
                source="startup",
            )
            summary["orders_uncertain"] += 1
            continue

        state = await client.get_sandbox_order_state(oid)
        if state is None:
            ledger.record_uncertain(
                oid, figi=figi, ticker=ticker, side=side,
                requested_lots=int(rec.get("requested_lots") or 0),
                reason="статус при старте недоступен/заявка не найдена — отказ НЕ фиксируется",
                source="startup",
            )
            summary["orders_uncertain"] += 1
            continue

        status = _status_name(getattr(state, "execution_report_status", None))
        le = int(getattr(state, "lots_executed", 0) or 0)

        if status in ("REJECTED", "CANCELLED", "EXPIRED") and le <= 0:
            ledger.record_status(oid, status, executed_lots=le,
                                 reason="достоверный отказ без исполнения", source="startup")
            summary["orders_resolved"] += 1
            continue

        if status == "FILL" or le > 0 or status == "PARTIALLYFILL":
            if side == "SELL":
                ledger.record_status(oid, status or "SENT", executed_lots=le,
                                     reason="закрытие подтверждено брокером", source="startup")
                summary["orders_resolved"] += 1
                continue
            info = await client.get_instrument_info(figi) if figi else None
            lot = int(info.get("lot")) if info and info.get("lot") else 1
            exec_price = ExecutionMonitor._extract_price(state, le, lot)
            await register_executed_buy(
                client, monitor, rm, ledger,
                figi=figi, ticker=ticker, order_id=oid,
                trade_id=rec.get("trade_id") or "",
                reason=rec.get("reason") or "",
                signal_time=rec.get("timestamp_utc") or "",
                price_before_order=0.0,
                order_send_time=rec.get("timestamp_utc") or "",
                requested_lots=int(rec.get("requested_lots") or 0),
                executed_lots=le, exec_price=exec_price, lot_size=lot,
                entry_commission=ExecutionMonitor._extract_commission(state),
                partial=(status != "FILL"), df=None,
                source="startup_recovery",
                ledger_status=status or "PARTIALLYFILL",
            )
            summary["orders_executed"] += 1
            continue

        ledger.record_status(oid, status or "NEW", executed_lots=le,
                             reason="активна при старте — под наблюдением опроса", source="startup")
        summary["orders_active"] += 1

    # 2. Активные заявки брокера, неизвестные реестру
    orders = await client.get_sandbox_orders()
    if orders is None:
        summary["orders_unavailable"] = True
        client.uncertain_state = True
        logging.error("[startup] Список активных заявок недоступен.")
    else:
        for o in orders:
            oid = getattr(o, "order_id", None)
            if not oid or ledger.has_order(str(oid)):
                continue
            figi = getattr(o, "figi", None)
            raw_dir = getattr(o, "direction", None)
            side = None
            try:
                side = "BUY" if int(raw_dir) == int(OrderDirection.ORDER_DIRECTION_BUY) else "SELL"
            except (TypeError, ValueError):
                side = None
            ledger.record_uncertain(
                str(oid), figi=figi, side=side,
                requested_lots=int(getattr(o, "lots_requested", 0) or 0),
                executed_lots=int(getattr(o, "lots_executed", 0) or 0),
                reason="активная заявка брокера без записи в реестре", source="startup",
            )
            summary["unknown_broker_orders"] += 1
            logging.error(f"[startup] Неизвестная заявка брокера {oid} (figi={figi}) — UNCERTAIN")
        if summary["unknown_broker_orders"]:
            await notify(
                f"⚠️ При старте обнаружены заявки брокера без записи в реестре: "
                f"{summary['unknown_broker_orders']} — переведены в UNCERTAIN. "
                f"BUY по их FIGI заблокированы; заявки БЕЗ FIGI блокируют ВСЕ новые BUY "
                f"глобально до разрешения. Позиции продолжают мониториться."
            )

    # 3. Позиции брокера (адопция того, что не зарегистрировано через заявки)
    positions = await client.get_positions()
    if positions is None:
        summary["positions_unavailable"] = True
        client.uncertain_state = True
        logging.error("[startup] Портфель недостоверен — адопция позиций отложена до reconcile.")
    else:
        broker = {f: p for f, p in positions.items()
                  if not f.startswith(("RUB", "USD", "EUR"))}
        for figi, pdata in broker.items():
            if monitor.has_active_position(figi):
                continue
            if pdata.direction == "UNKNOWN":
                continue
            info = await client.get_instrument_info(figi)
            ticker = info.get("ticker") if info else figi
            direction = "BUY" if pdata.direction == "LONG" else "SELL"
            lot = int(info.get("lot")) if info and info.get("lot") else 1
            monitor.register_recovery(
                figi=figi, ticker=ticker, direction=direction,
                quantity=abs(pdata.lots), lot_size=lot,
                recovery_source="adopted",
            )
            summary["positions_adopted"] += 1
            logging.error(
                f"[startup] {ticker}: позиция брокера без локального учёта — RECOVERY "
                f"({abs(pdata.lots)} лот, {direction})"
            )
            research_logger.log_event("POSITION_RECOVERED", {
                "figi": figi, "ticker": ticker, "direction": direction,
                "lots": abs(pdata.lots), "source": "startup_recovery",
            })
            await notify(
                f"⚠️ <b>{ticker}</b>: при старте обнаружена позиция брокера без локального учёта\n"
                f"Зарегистрирована как RECOVERY: {abs(pdata.lots)} лот ({direction})\n"
                f"Новые BUY по инструменту заблокированы. Автозакрытие отключено."
            )

    research_logger.log_event("STARTUP_RECOVERY", summary)
    logging.info(f"[startup] Восстановление завершено: {summary}")


# ============================================================
# TRADING CYCLE
# ============================================================

async def trading_cycle(client: TinkoffClient, rm: RiskManager, sm: StrategyManager, monitor: ExecutionMonitor, ledger: Optional[OrderLedger] = None):
    global reconcile_done
    if ledger is None:
        ledger = ledger_ref

    # 1. Цены: TOP-10 + открытые позиции
    figi_set = set(p.figi for p in monitor.positions.values())
    figi_set.update(i['figi'] for i in top_instruments)
    figi_to_monitor = list(figi_set)
    prices = await client.get_last_prices(figi_to_monitor)

    if prices is None:
        logging.error("[cycle] prices=None — monitoring_error=True, мониторинг продолжается")
        monitor.monitoring_error = True
        prices = {}

    # 2. SL/TP
    to_close = await monitor.check_all(prices)
    for pos in to_close:
        price = prices.get(pos.figi)
        if price is None or price <= 0:
            logging.error(f"[cycle] {pos.ticker}: нет цены для закрытия — пропуск (UNCERTAIN)")
            pos.state = PositionState.UNCERTAIN
            client.uncertain_state = True
            continue
        await close_position(client, pos, price)

    # 3. Обработка закрытий и UNCERTAIN
    await process_closing(client, monitor)

    # 3.5 Этап 5: пошаговый контроль незавершённых BUY-заявок (не блокирует цикл)
    if ledger is not None:
        await poll_open_orders(client, monitor, rm, ledger)

    # 3.6 Этап 5: восстановление цены входа для RECOVERY-позиций
    if ledger is not None:
        await promote_recovery_positions(client, monitor, rm)

    # 4. Если uncertain — reconcile
    if client.is_uncertain():
        logging.warning("[cycle] uncertain_state=True — пробую reconcile.")
        await reconcile(client, monitor, ledger, rm)
        if client.is_uncertain():
            logging.warning("[cycle] reconcile не снял UNCERTAIN — новые входы запрещены.")
            _save_state_checked(monitor, rm)
            return

    # 5. Блокировки входов
    if not is_trading_enabled():
        logging.info("[cycle] Торговля выключена (нет trading.flag).")
        _save_state_checked(monitor, rm)
        return
    if not reconcile_done:
        logging.warning("[cycle] reconcile не завершён.")
        _save_state_checked(monitor, rm)
        return
    if monitor.monitoring_error:
        logging.warning("[cycle] monitoring_error=True — новые входы запрещены.")
        _save_state_checked(monitor, rm)
        return
    if state_save_failed:
        # Этап 5 (D6): незаписанное состояние не дополняется новыми рисками
        logging.error("[cycle] state.json не сохранён ранее — новые входы запрещены до успешного сохранения.")
        _save_state_checked(monitor, rm)
        return
    if ledger is not None and not ledger.is_reliable():
        # Дефект №5: недостоверный реестр (повреждение/ошибка чтения журнала) —
        # робот может «забыть» ранее созданные заявки. Новые BUY запрещены
        # до восстановления целостности ЖУРНАЛОМ ОПЕРАТОРА (журнал не
        # обрезается и не перезаписывается автоматически). Позиции
        # продолжают мониториться (SL/TP и закрытия работают).
        logging.error(
            "[cycle] Реестр заявок НЕДОСТОВЕРЕН — новые BUY запрещены до "
            f"восстановления целостности. Причина: {ledger.unreliable_reason}"
        )
        _save_state_checked(monitor, rm)
        return
    if ledger is not None and ledger.has_unknown_figi_open_orders():
        # Дефект №4: незавершённая заявка без FIGI — неизвестно, какой
        # инструмент она затрагивает. Новые BUY запрещены ГЛОБАЛЬНО до
        # разрешения неопределённости. Существующие позиции мониторятся.
        logging.error(
            "[cycle] Незавершённая заявка БЕЗ FIGI в реестре — новые BUY "
            "запрещены ГЛОБАЛЬНО до разрешения неопределённости."
        )
        _save_state_checked(monitor, rm)
        return

    # 6. Свежий портфель перед входами
    positions = await client.get_positions()
    if positions is None:
        logging.error("[cycle] Портфель недостоверен перед входом — новые входы запрещены.")
        client.uncertain_state = True
        reconcile_done = False
        _save_state_checked(monitor, rm)
        return

    # 7. Входы
    for inst in top_instruments:
        figi = inst['figi']
        price = prices.get(figi)
        if price is None or price <= 0:
            logging.info(f"{inst['ticker']}: нет цены, пропуск")
            continue
        pos_data = positions.get(figi)
        if (pos_data is not None and pos_data.lots != 0) or monitor.has_active_position(figi):
            logging.info(f"{inst['ticker']}: позиция уже открыта/в неопределённости, пропуск")
            continue
        if ledger is not None and ledger.figi_blocks_buy(figi):
            # Дефект №4: вход блокирует ЛЮБАЯ незавершённая/неопределённая
            # заявка по FIGI: незавершённая BUY, заявка с неизвестной стороной
            # (side=None) и UNCERTAIN любой стороны. Нельзя разрешать BUY
            # только потому, что сторона заявки неизвестна.
            logging.warning(f"{inst['ticker']}: в реестре есть незавершённая/неопределённая заявка по FIGI — вход заблокирован")
            continue

        df = await _build_candles_df(client, figi)
        if df is None:
            logging.info(f"{inst['ticker']}: мало свечей (<{config.MIN_CANDLES_FOR_SIGNAL}), пропуск")
            continue

        signal, reason = sm.get_signal(df, float(price), drop_incomplete=True)

        if signal == "HOLD":
            logging.info(f"{inst['ticker']}: HOLD ({reason})")
            _log_strategy_diagnostic(
                event_type="STRATEGY_HOLD", ticker=inst['ticker'], figi=figi,
                signal=signal, reason=reason, sm=sm, df=df, price=float(price),
            )
            continue

        if signal == "SELL":
            logging.info(f"{inst['ticker']}: SELL без позиции — шорт запрещён")
            _log_strategy_diagnostic(
                event_type="STRATEGY_SELL_IGNORED", ticker=inst['ticker'], figi=figi,
                signal=signal, reason=reason, sm=sm, df=df, price=float(price),
            )
            continue

        lots = await rm.calculate_position_size(figi, float(price), df)
        if lots <= 0:
            logging.info(f"{inst['ticker']}: размер позиции 0, пропуск")
            continue

        info = await client.get_instrument_info(figi)
        if not info:
            logging.error(f"{inst['ticker']}: instrument_info=None — пропуск")
            continue

        trade_id = uuid.uuid4().hex[:12]
        # Этап 5 (P0-1): order_id генерируется ДО записи SIGNAL и до сетевого вызова —
        # единый идентификатор намерения и заявки (контракт SDK 1.51.0 подтверждён)
        order_id = str(uuid.uuid4())
        order_send_time = datetime.now(timezone.utc).isoformat()
        research_logger.log_event("SIGNAL", {
            "trade_id": trade_id, "ticker": inst['ticker'], "figi": figi,
            "signal": signal, "reason": reason, "price_before_order": float(price),
            "requested_lots": lots, "lot_size": info['lot'],
            "order_id": order_id,
        })

        # Этап 5 (P0-2): INTENT в реестр ДО сетевого вызова.
        # Запись не удалась — заявка НЕ отправляется (fail-closed).
        if ledger is None:
            logging.error(
                f"{inst['ticker']}: реестр заявок недоступен — вход заблокирован (fail-closed)"
            )
            _save_state_checked(monitor, rm)
            continue
        intent_ok = ledger.record_intent(
            order_id, trade_id=trade_id, figi=figi, ticker=inst['ticker'],
            side="BUY", requested_lots=lots,
        )
        if not intent_ok:
            logging.error(
                f"{inst['ticker']}: INTENT не сохранён — заявка НЕ отправляется (fail-closed)"
            )
            research_logger.log_event("ORDER_INTENT_FAILED", {
                "trade_id": trade_id, "ticker": inst['ticker'], "figi": figi,
                "order_id": order_id, "requested_lots": lots,
            })
            _save_state_checked(monitor, rm)
            continue
        research_logger.log_event("ORDER_INTENT", {
            "trade_id": trade_id, "ticker": inst['ticker'], "figi": figi,
            "order_id": order_id, "requested_lots": lots,
        })

        result = await client.post_market_order(figi, lots, OrderDirection.ORDER_DIRECTION_BUY, order_id=order_id)

        sent_status_name = _status_name(getattr(result.state, "execution_report_status", None)) if result.state is not None else None
        sent_le = int(getattr(result.state, "lots_executed", 0) or 0) if result.state is not None else None

        if result.state is None:
            logging.error(
                f"{inst['ticker']}: state=None (order_id={result.order_id}) — UNCERTAIN. "
                f"Повторная отправка запрещена."
            )
            client.uncertain_state = True
            if ledger is not None:
                ledger.record_uncertain(
                    order_id, trade_id=trade_id, figi=figi, ticker=inst['ticker'],
                    side="BUY", requested_lots=lots,
                    reason="post: ответ не получен — заявка могла быть принята брокером",
                    source="post_market_order",
                    error=str(result.error) if result.error else None,
                )
            research_logger.log_event("BUY_UNKNOWN", {
                "trade_id": trade_id, "ticker": inst['ticker'], "figi": figi,
                "order_id": result.order_id,
            })
            _save_state_checked(monitor, rm)
            continue

        # Этап 5: SENT сразу после ответа (post-ответ уже содержит статус и исполнение)
        if ledger is not None:
            ledger.record_sent(
                order_id, trade_id=trade_id, figi=figi, ticker=inst['ticker'],
                side="BUY", requested_lots=lots, executed_lots=sent_le,
                status_hint=sent_status_name, source="post_response",
            )

        status = getattr(result.state, 'execution_report_status', None)
        order_id = result.order_id

        # --- PARTIALLYFILL (этап 5: без потери заявки/позиции) ---
        if status == OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_PARTIALLYFILL:
            logging.error(
                f"{inst['ticker']}: ЧАСТИЧНОЕ исполнение покупки — отмена остатка "
                f"(order_id={order_id})"
            )
            research_logger.log_event("PARTIAL_FILL", {"trade_id": trade_id, "order_id": order_id})
            client.uncertain_state = True

            cancel_resp = await client.cancel_order(order_id)
            if cancel_resp is None:
                logging.error(f"{inst['ticker']}: cancel_order вернул None — UNCERTAIN; заявка остаётся в реестре")
                if ledger is not None:
                    ledger.record_uncertain(order_id, trade_id=trade_id, figi=figi,
                                            ticker=inst['ticker'], side="BUY", requested_lots=lots,
                                            reason="cancel не подтверждён", source="partial_branch")
                _save_state_checked(monitor, rm)
                continue

            final_state = await client.get_sandbox_order_state(order_id)
            if final_state is None:
                logging.error(f"{inst['ticker']}: final state=None — UNCERTAIN; заявка остаётся в реестре")
                if ledger is not None:
                    ledger.record_uncertain(order_id, trade_id=trade_id, figi=figi,
                                            ticker=inst['ticker'], side="BUY", requested_lots=lots,
                                            reason="статус после отмены недоступен", source="partial_branch")
                _save_state_checked(monitor, rm)
                continue

            fresh_positions = await client.get_positions()
            if fresh_positions is None:
                # Снимок портфеля недостоверен. Но это НЕ отменяет подтверждённое
                # брокером исполнение (Дефект №1): если final_state показывает
                # исполненные лоты — регистрируем их; иначе заявка остаётся в
                # реестре (неопределённость) под опросом.
                fs_status_np = _status_name(getattr(final_state, "execution_report_status", None))
                le_fs = int(getattr(final_state, "lots_executed", 0) or 0)
                if le_fs > 0:
                    logging.warning(
                        f"{inst['ticker']}: снимок портфеля недоступен, но брокер подтвердил "
                        f"{le_fs} исполненных лот — регистрация исполнения (Дефект №1)"
                    )
                    exec_price = ExecutionMonitor._extract_price(final_state, le_fs, info['lot'])
                    await register_executed_buy(
                        client, monitor, rm, ledger,
                        figi=figi, ticker=inst['ticker'], order_id=order_id, trade_id=trade_id,
                        reason=reason, signal_time=order_send_time,
                        price_before_order=float(price), order_send_time=order_send_time,
                        requested_lots=lots, executed_lots=le_fs, exec_price=exec_price,
                        lot_size=info['lot'],
                        entry_commission=ExecutionMonitor._extract_commission(final_state),
                        partial=True, df=df,
                        source="partial_branch_no_snapshot",
                        ledger_status=fs_status_np or "PARTIALLYFILL",
                    )
                else:
                    logging.error(
                        f"{inst['ticker']}: fresh positions=None, исполнение не подтверждено — "
                        f"заявка остаётся в реестре (неопределённость)"
                    )
                _save_state_checked(monitor, rm)
                continue

            pos_data = fresh_positions.get(figi)
            if pos_data is None or pos_data.lots == 0:
                le_after = int(getattr(final_state, "lots_executed", 0) or 0)
                if le_after <= 0:
                    logging.warning(f"{inst['ticker']}: после отмены позиции нет, исполнение не подтверждено брокером — заявка закрыта в реестре")
                    if ledger is not None:
                        ledger.record_status(order_id, "CANCELLED", executed_lots=0,
                                             reason="остаток отменён; достоверный отказ без исполнения",
                                             source="partial_branch")
                    _save_state_checked(monitor, rm)
                    continue
                # Дефект №1: брокер подтвердил исполненные лоты, но свежий снимок
                # портфеля позицию пока НЕ показывает (задержка маппинга сделок).
                # Отсутствие позиции в снимке НЕ отменяет подтверждённое
                # исполнение: регистрируем ровно исполненное количество; при
                # недоступной цене — RECOVERY. Статус заявки в реестре —
                # фактический статус брокера (частичное исполнение отменённой
                # заявки, Дефект №2), а не «исполнение не подтвердилось».
                fs_status = _status_name(getattr(final_state, "execution_report_status", None))
                logging.warning(
                    f"{inst['ticker']}: остаток отменён, портфель пока не показывает позицию, "
                    f"но брокер подтвердил {le_after} исполненных лот — регистрация (Дефект №1)"
                )
                exec_price = ExecutionMonitor._extract_price(final_state, le_after, info['lot'])
                await register_executed_buy(
                    client, monitor, rm, ledger,
                    figi=figi, ticker=inst['ticker'], order_id=order_id, trade_id=trade_id,
                    reason=reason, signal_time=order_send_time,
                    price_before_order=float(price), order_send_time=order_send_time,
                    requested_lots=lots, executed_lots=le_after, exec_price=exec_price,
                    lot_size=info['lot'],
                    entry_commission=ExecutionMonitor._extract_commission(final_state),
                    partial=True, df=df, source="partial_branch_confirmed",
                    ledger_status=fs_status or "PARTIALLYFILL",
                )
                _save_state_checked(monitor, rm)
                continue

            le = pos_data.lots
            exec_price = ExecutionMonitor._extract_price(final_state, le, info['lot'])

            # Этап 5: единая точка регистрации — исполненная часть не теряется:
            # при exec_price=None/SL-TP недоступных позиция попадает в RECOVERY
            await register_executed_buy(
                client, monitor, rm, ledger,
                figi=figi, ticker=inst['ticker'], order_id=order_id, trade_id=trade_id,
                reason=reason, signal_time=order_send_time,
                price_before_order=float(price), order_send_time=order_send_time,
                requested_lots=lots, executed_lots=le, exec_price=exec_price,
                lot_size=info['lot'],
                entry_commission=ExecutionMonitor._extract_commission(final_state),
                partial=True, df=df, source="partial_branch", ledger_status="CANCELLED",
            )
            _save_state_checked(monitor, rm)
            continue

        # --- НЕ FILL (этап 4.1: проверка фактического исполнения; этап 5: без потери заявки) ---
        if status != OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_FILL:
            logging.warning(
                f"{inst['ticker']}: не исполнено ({status}) — проверка фактического "
                f"исполнения (order_id={order_id})"
            )
            research_logger.log_event("ORDER_NOT_FILLED", {
                "trade_id": trade_id, "ticker": inst['ticker'], "figi": figi,
                "order_id": order_id, "status": str(status),
                "requested_lots": lots,
            })

            if not order_id:
                logging.error(f"{inst['ticker']}: нет order_id — UNCERTAIN")
                client.uncertain_state = True
                _save_state_checked(monitor, rm)
                continue

            # Запросить фактическое состояние заявки
            state = await client.get_sandbox_order_state(order_id)
            if state is None:
                logging.error(
                    f"{inst['ticker']}: state=None после не-FILL (order_id={order_id}) — UNCERTAIN; "
                    f"заявка остаётся в реестре"
                )
                client.uncertain_state = True
                if ledger is not None:
                    ledger.record_uncertain(order_id, trade_id=trade_id, figi=figi,
                                            ticker=inst['ticker'], side="BUY", requested_lots=lots,
                                            reason="статус после не-FILL недоступен", source="not_fill_branch")
                _save_state_checked(monitor, rm)
                continue

            le = int(getattr(state, "lots_executed", 0) or 0)
            if le <= 0:
                # Этап 5 (D4): заявка ещё жива — НЕ выбрасываем из учёта:
                # остаётся в реестре, опрос продолжится; при старении остаток отменяется
                logging.warning(
                    f"{inst['ticker']}: lots_executed=0 — заявка активна, остаётся под наблюдением"
                )
                if ledger is not None:
                    ledger.record_status(order_id, _status_name(getattr(state, "execution_report_status", None)) or "NEW",
                                         executed_lots=0, source="not_fill_branch")
                _save_state_checked(monitor, rm)
                continue

            # Исполнено > 0 -> регистрация через единую точку (как при FILL)
            exec_price = ExecutionMonitor._extract_price(state, le, info['lot'])
            await register_executed_buy(
                client, monitor, rm, ledger,
                figi=figi, ticker=inst['ticker'], order_id=order_id, trade_id=trade_id,
                reason=reason, signal_time=order_send_time,
                price_before_order=float(price), order_send_time=order_send_time,
                requested_lots=lots, executed_lots=le, exec_price=exec_price,
                lot_size=info['lot'],
                entry_commission=ExecutionMonitor._extract_commission(state),
                partial=False, df=df, source="not_fill_branch",
                ledger_status=_status_name(getattr(state, "execution_report_status", None)) or "SENT",
            )
            _save_state_checked(monitor, rm)
            continue

        # --- FILL (этап 5: подтверждённое исполнение не может быть потеряно; D1 устранён) ---
        state = await client.get_sandbox_order_state(order_id) if order_id else None
        if state is None:
            # Ответ post_sandbox_order уже подтвердил FILL. Дефект D1(а) устранён:
            # регистрируем позицию по данным post-ответа (RECOVERY при неполных данных),
            # цена/SL-TP доуточняются опросом (promote_recovery_positions).
            logging.error(
                f"{inst['ticker']}: state=None после FILL — регистрация по данным post-ответа "
                f"(order_id={order_id})"
            )
            research_logger.log_event("ORDER_STATE_UNKNOWN", {
                "trade_id": trade_id, "ticker": inst['ticker'], "figi": figi,
                "order_id": order_id,
            })
            client.uncertain_state = True
            post_le = sent_le if sent_le else int(getattr(result.state, "lots_executed", 0) or 0)
            post_price = ExecutionMonitor._extract_price(result.state, post_le, info['lot']) if post_le > 0 else None
            await register_executed_buy(
                client, monitor, rm, ledger,
                figi=figi, ticker=inst['ticker'], order_id=order_id, trade_id=trade_id,
                reason=reason, signal_time=order_send_time,
                price_before_order=float(price), order_send_time=order_send_time,
                requested_lots=lots, executed_lots=post_le, exec_price=post_price,
                lot_size=info['lot'],
                entry_commission=ExecutionMonitor._extract_commission(result.state),
                partial=False, df=df, source="fill_branch_post_response", ledger_status="FILL",
            )
            _save_state_checked(monitor, rm)
            continue

        le = int(getattr(state, 'lots_executed', 0) or 0)
        if le <= 0:
            # FILL без исполнения — противоречие: заявку не теряем, опрос продолжится
            logging.error(f"{inst['ticker']}: FILL, но lots_executed={le} — UNCERTAIN (заявка в реестре)")
            client.uncertain_state = True
            if ledger is not None:
                ledger.record_uncertain(order_id, trade_id=trade_id, figi=figi,
                                        ticker=inst['ticker'], side="BUY", requested_lots=lots,
                                        reason="FILL противоречие: lots_executed=0", source="fill_branch")
            _save_state_checked(monitor, rm)
            continue

        exec_price = ExecutionMonitor._extract_price(state, le, info['lot'])

        # Единая точка регистрации: полные данные -> OPEN, неполные -> RECOVERY.
        # Ни один выход не оставляет исполнение без регистрации.
        await register_executed_buy(
            client, monitor, rm, ledger,
            figi=figi, ticker=inst['ticker'], order_id=order_id, trade_id=trade_id,
            reason=reason, signal_time=order_send_time,
            price_before_order=float(price), order_send_time=order_send_time,
            requested_lots=lots, executed_lots=le, exec_price=exec_price,
            lot_size=info['lot'],
            entry_commission=ExecutionMonitor._extract_commission(state),
            partial=False, df=df, source="fill_branch", ledger_status="FILL",
        )
        _save_state_checked(monitor, rm)


# ============================================================
# MAIN
# ============================================================

async def main():
    global telegram_bot, main_client, top_instruments, monitor_ref, rm_ref, ledger_ref

    config.validate()

    if config.ENABLE_TELEGRAM and TelegramBot:
        try:
            telegram_bot = TelegramBot(
                config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID,
                config.TELEGRAM_ALLOWED_USER_ID,
                {'start_trading': start_trading, 'stop_trading': stop_trading, 'get_status': get_status},
            )
            await asyncio.wait_for(telegram_bot.application.initialize(), timeout=20.0)
            await asyncio.wait_for(telegram_bot.application.start(), timeout=20.0)
            await asyncio.wait_for(telegram_bot.application.updater.start_polling(), timeout=20.0)
            await telegram_bot.register_commands()
            logging.info("Telegram-бот запущен (initialize → start → polling → commands).")
            await notify("🤖 Tsclp_v2.2_volatile запущен. Управление — через trading.flag.")
        except Exception as e:
            logging.warning(
                f"Telegram недоступен ({type(e).__name__}: {e!r}). "
                f"Робот работает БЕЗ уведомлений."
            )
            await _stop_telegram_bot()
        asyncio.create_task(_telegram_watchdog())
        logging.info("[tg-watchdog] Сторож запущен (60 сек, полный перезапуск при падении).")

    async with TinkoffClient(config.TINKOFF_API_TOKEN, config.USE_SANDBOX) as client:
        main_client = client
        rm = RiskManager(client)
        rm_ref = rm
        sm = StrategyManager()
        monitor = ExecutionMonitor(client)
        monitor_ref = monitor
        screener = Screener()

        # Этап 5: реестр заявок — до старта циклов (рядом с state.json)
        ledger = OrderLedger(LEDGER_FILE)
        ledger.load()
        ledger_ref = ledger

        # Дефект №5: недостоверный реестр (повреждение/ошибка чтения журнала).
        # Новые BUY запрещены до восстановления целостности оператором;
        # существующие позиции продолжают мониториться.
        if not ledger.is_reliable():
            _msg = (
                "⛔ Реестр заявок НЕДОСТОВЕРЕН (повреждение/ошибка чтения "
                f"{LEDGER_FILE}).\nНовые BUY ЗАПРЕЩЕНЫ до восстановления "
                f"целостности журнала.\nЖурнал не обрезался и не перезаписывался "
                f"(автовосстановление отключено — восстановление вручную).\n"
                f"Причина: {ledger.unreliable_reason}\n"
                f"Позиции продолжают мониториться."
            )
            logging.error(f"[main] {_msg}")
            research_logger.log_event("LEDGER_UNRELIABLE", {
                "reason": ledger.unreliable_reason, "file": LEDGER_FILE,
            })
            await notify(_msg)

        state_loaded = _load_state(monitor, rm)
        if not state_loaded:
            client.uncertain_state = True
            logging.warning(
                "[main] state.json отсутствует/повреждён — новые входы запрещены до сверки."
            )

        # Этап 5 (P1-1/P1-5): четырёхсторонняя стартовая сверка
        # (реестр × state.json × активные заявки брокера × позиции брокера).
        # Неизвестная позиция/заявка не исчезают из учёта: RECOVERY/UNCERTAIN
        # с блокировкой BUY по FIGI до разрешения.
        await startup_recovery(client, monitor, rm, ledger)

        instruments = await screener.select_top_volatile()
        if not instruments:
            client.uncertain_state = True
            msg = "⚠️ Скрининг не удался. Новые входы ЗАПРЕЩЕНЫ."
            logging.error(msg)
            research_logger.log_event("SCREEN_FAILED", {})
            await notify(msg)
            while True:
                await asyncio.sleep(60)

        top_instruments = instruments
        logging.info(f"[screener] Инструменты: {', '.join(i['ticker'] for i in instruments)}")

        await reconcile(client, monitor, ledger, rm)

        if not reconcile_done:
            logging.warning("[main] reconcile не завершён — новые входы запрещены.")
            client.uncertain_state = True

        while True:
            try:
                await trading_cycle(client, rm, sm, monitor, ledger)
            except Exception as e:
                logging.exception(f"Ошибка цикла: {type(e).__name__}: {e}")
                research_logger.log_event("CYCLE_ERROR", {"error": str(e)})
            await asyncio.sleep(config.TRADING_CYCLE_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Остановлено пользователем.")
    except Exception as e:
        logging.critical(f"Фатальная ошибка: {type(e).__name__}: {e}")
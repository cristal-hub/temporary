# order_ledger.py
# Tsclp_v2.2_volatile — персистентный реестр заявок (OrderLedger). НОВЫЙ МОДУЛЬ (P0-1/P0-2).
#
# НАЗНАЧЕНИЕ:
#   Устранить потерю информации о заявках между сигналом и регистрацией позиции
#   (дефекты D1/D3/D4 из расследования SELG/MVID/ETLN, main@a67ab4a).
#
#   Ключевой контракт: order_id генерируется ДО записи SIGNAL, запись INTENT
#   выполняется ДО сетевого вызова; если запись не удалась — заявка НЕ отправляется.
#
# ФОРМАТ: append-only JSONL (order_ledger.jsonl рядом с state.json).
#   Каждая строка — ОДИН переход состояния заявки (не перезапись): status, order_id,
#   trade_id, figi, ticker, side, requested_lots, executed_lots, lots_left,
#   avg_exec_price, reason, source, error.
#
# СТАТУСЫ:
#   INTENT        — намерение сохранено, заявка ещё не отправлялась
#   SENT          — post-ответ получен (state/response сохранён)
#   NEW           — брокер подтвердил активную заявку (не терминальный)
#   PARTIALLYFILL — брокер подтвердил частичное исполнение (не терминальный)
#   FILL          — полное исполнение (терминальный)
#   CANCELLED     — отменена (терминальный; возможны residual-исполнения: executed_lots>0)
#   REJECTED      — отклонена брокером (терминальный)
#   EXPIRED       — истекла (терминальный)
#   UNCERTAIN     — факт/объём исполнения неизвестны (потерян ответ, INTENT без SENT,
#                   заявка не найдена, статус недоступен). НЕ является отказом.
#
# ПРАВИЛО FAILED (согласовано с владельцем):
#   Отказ без исполнения (FAILED) фиксируется ТОЛЬКО при достоверном подтверждении
#   брокера: терминальный статус REJECTED/CANCELLED/EXPIRED с executed_lots == 0.
#   Ответы «заявка не найдена» сами по себе НЕ являются доказательством неотправки —
#   такие заявки переводятся в UNCERTAIN с блокировкой новых BUY по FIGI.
#
# ИДЕНТИФИКАТОРЫ: единственный идентификатор заявки — order_id (UUID, генерируется
#   локально и принимается брокером, контракт SDK 1.51.0 подтверждён аудитом).
#   Дополнительные идентификаторы не вводятся.
#
# ЦЕЛОСТНОСТЬ: append + flush + fsync.
#
# ИЗМЕНЕНИЯ (партия P0 — точечное исправление дефектов первой поставки):
#   Дефект №3: у записи реестра разделены immutable created_at_utc (время
#     создания INTENT — источник возраста заявки) и updated_at_utc (время
#     последнего перехода). Обновление статуса NEW больше НЕ сбрасывает возраст.
#     Возраст восстанавливается после перезапуска из журнала.
#   Дефект №5: повреждение журнала (ошибка чтения, повреждённая строка в
#     середине или хвосте) переводит реестр в ЯВНОЕ состояние недостоверности
#     (is_reliable() == False). Повреждённая строка не пропускается молча;
#     исходный журнал НЕ обрезается и НЕ перезаписывается; автоматическое
#     восстановление не выполняется. main.py запрещает новые BUY до
#     восстановления целостности (позиции продолжают мониториться).
#   Дефект №8: record_sent() не фиксирует терминальный FILL без проверенного
#     executed_lots > 0: противоречие записывается как UNCERTAIN (заявка
#     остаётся в open_orders, блокирует BUY, разрешается повторным запросом).
#   Дефект №4: figi_blocks_buy() — блокировка новых BUY по FIGI ЛЮБОЙ
#     незавершённой/неопределённой заявкой (BUY, side=None, UNCERTAIN);
#     has_unknown_figi_open_orders() — глобальная блокировка BUY при заявке
#     без FIGI.

import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

# Статусы заявок в реестре
STATUS_INTENT = "INTENT"
STATUS_SENT = "SENT"
STATUS_NEW = "NEW"
STATUS_PARTIALLYFILL = "PARTIALLYFILL"
STATUS_FILL = "FILL"
STATUS_CANCELLED = "CANCELLED"
STATUS_REJECTED = "REJECTED"
STATUS_EXPIRED = "EXPIRED"
STATUS_UNCERTAIN = "UNCERTAIN"

TERMINAL_STATUSES = {STATUS_FILL, STATUS_CANCELLED, STATUS_REJECTED, STATUS_EXPIRED}
NON_TERMINAL_STATUSES = {STATUS_INTENT, STATUS_SENT, STATUS_NEW, STATUS_PARTIALLYFILL, STATUS_UNCERTAIN}

# Терминальные статусы, которые при executed_lots == 0 означают достоверный отказ без исполнения
DEAD_WITHOUT_EXECUTION = {STATUS_REJECTED, STATUS_CANCELLED, STATUS_EXPIRED}

RECORD_FIELDS = [
    "timestamp_utc", "status", "order_id", "trade_id", "figi", "ticker",
    "side", "requested_lots", "executed_lots", "lots_left",
    "avg_exec_price", "reason", "source", "error",
    # Дефект №3: immutable время создания заявки (INTENT) и время последнего
    # перехода. created_at_utc НЕ обновляется при изменении статуса.
    "created_at_utc", "updated_at_utc",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrderLedger:
    """
    Append-only реестр переходов состояния заявок + индекс последних состояний.

    Файл — источник истины. Индекс (latest) восстанавливается при load().
    Все методы записи возвращают bool (успех записи на диск); вызывающий код
    обязан интерпретировать False как «заявка не отправляется / состояние не
    подтверждено» и не терять заявку (см. main.py).
    """

    def __init__(self, path: str):
        self.path = path
        # order_id -> последний переход (агрегированное состояние)
        self._latest: Dict[str, dict] = {}
        self._corrupt_tail_truncated = False
        # Дефект №5: реестр достоверен, пока не доказано обратное.
        # Повреждение журнала или ошибка чтения делают его НЕдостоверным:
        # main.py запрещает новые BUY до восстановления целостности.
        self._reliable = True
        self._unreliable_reason = ""

    # ------------------------------------------------------------------
    # Загрузка / целостность
    # ------------------------------------------------------------------

    def is_reliable(self) -> bool:
        """Дефект №5: False — журнал повреждён/нечитаем; новые BUY запрещены."""
        return self._reliable

    @property
    def unreliable_reason(self) -> str:
        return self._unreliable_reason

    def _mark_unreliable(self, reason: str) -> None:
        # Идемпотентно: первая причина сохраняется для диагностики.
        if not self._reliable:
            return
        self._reliable = False
        self._unreliable_reason = reason
        logging.error(
            f"[ledger] РЕЕСТР НЕДОСТОВЕРЕН: {reason}. Новые BUY запрещены "
            f"до восстановления целостности журнала (позиции продолжают "
            f"мониториться). Автоматическое восстановление не выполняется: "
            f"журнал не обрезается и не перезаписывается — вмешательство оператора."
        )

    def load(self) -> None:
        """
        Восстанавливает индекс из JSONL.

        Дефект №5: любое повреждение журнала (ошибка чтения, некорректная
        строка в середине или в хвосте) переводит реестр в явно
        недостоверное состояние (is_reliable() == False). Повреждённая
        строка не пропускается молча; исходный журнал НЕ обрезается и
        НЕ перезаписывается; автоматическое восстановление не выполняется
        (оно может потерять историю заявок). Читаемая часть загружается
        по максимуму (best effort) — для сверки с брокером, но торговые
        входы блокируются main.py до восстановления целостности.
        Отсутствие файла — валидный пустой реестр (заявок не было).
        """
        self._latest = {}
        self._corrupt_tail_truncated = False
        self._reliable = True
        self._unreliable_reason = ""
        if not os.path.isfile(self.path):
            logging.info(f"[ledger] {self.path} отсутствует — начинаю с пустого реестра.")
            return

        records = 0
        try:
            with open(self.path, "rb") as f:
                raw = f.read()
        except Exception as e:
            # Дефект №5: ошибка чтения — реестр недостоверен (НЕ пустой достоверный).
            self._mark_unreliable(
                f"ошибка чтения {self.path}: {type(e).__name__}: {e}"
            )
            return

        lines = raw.splitlines(keepends=True)
        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue
            rec = None
            try:
                rec = json.loads(line_stripped.decode("utf-8"))
                if not isinstance(rec, dict) or "status" not in rec or "order_id" not in rec:
                    raise ValueError("неверная структура записи")
            except Exception as e:
                # Дефект №5: повреждённая строка (в хвосте при обрыве append
                # или в середине) НЕ игнорируется молча и НЕ обрезается —
                # реестр помечается недостоверным, торговля входами запрещена.
                is_tail = line is lines[-1]
                place = "хвост" if is_tail else "середина"
                logging.error(
                    f"[ledger] Повреждённая строка в {self.path} "
                    f"({place}, {type(e).__name__}: {e}) — реестр недостоверен"
                )
                self._corrupt_tail_truncated = self._corrupt_tail_truncated or is_tail
                self._mark_unreliable(
                    f"повреждённая строка журнала ({place}): "
                    f"{type(e).__name__}: {e}"
                )
                continue
            oid = str(rec["order_id"])
            norm = self._normalize(rec)
            prev = self._latest.get(oid)
            if prev is not None and prev.get("created_at_utc"):
                # Дефект №3: created_at_utc неизменяемо — берём самое раннее
                # (первая запись заявки в журнале — обычно INTENT).
                norm["created_at_utc"] = prev["created_at_utc"]
            self._latest[oid] = norm
            records += 1

        logging.info(
            f"[ledger] Загружено переходов: {records}, заявок: {len(self._latest)}"
            + ("" if self._reliable else "; РЕЕСТР НЕДОСТОВЕРЕН — новые BUY запрещены")
            + "."
        )

    @staticmethod
    def _normalize(rec: dict) -> dict:
        out = {k: rec.get(k) for k in RECORD_FIELDS}
        out["order_id"] = str(rec.get("order_id"))
        out["status"] = str(rec.get("status"))
        # Дефект №3: created_at_utc — immutable время создания заявки.
        # Для записей старого формата (без created_at_utc) берём timestamp_utc
        # самой первой записи заявки (load() сохраняет самое раннее значение).
        ts = rec.get("timestamp_utc")
        out["created_at_utc"] = rec.get("created_at_utc") or ts
        out["updated_at_utc"] = rec.get("updated_at_utc") or ts
        return out

    # ------------------------------------------------------------------
    # Запись
    # ------------------------------------------------------------------

    def append(self, status: str, order_id: Optional[str], **fields) -> bool:
        """
        Дописывает один переход состояния. Возвращает True при успехе записи.
        При ошибке записи — False (вызывающий код НЕ отправляет заявку / НЕ
        считает переход выполненным).
        """
        record = {
            "timestamp_utc": utc_now_iso(),
            "status": status,
            "order_id": str(order_id) if order_id is not None else None,
            "trade_id": fields.get("trade_id"),
            "figi": fields.get("figi"),
            "ticker": fields.get("ticker"),
            "side": fields.get("side"),
            "requested_lots": fields.get("requested_lots"),
            "executed_lots": fields.get("executed_lots"),
            "lots_left": fields.get("lots_left"),
            "avg_exec_price": fields.get("avg_exec_price"),
            "reason": fields.get("reason", ""),
            "source": fields.get("source", ""),
            "error": fields.get("error"),
        }
        # Наследование идентификационных полей от предыдущего перехода:
        # каждый следующий статус остаётся привязан к заявке, даже если
        # вызывающий код не повторил figi/ticker/side (защита от потери связи)
        prev = self._latest.get(str(record["order_id"])) if record["order_id"] is not None else None
        if prev is not None:
            for k in ("trade_id", "figi", "ticker", "side", "requested_lots"):
                if record[k] is None:
                    record[k] = prev.get(k)
        # Дефект №3: created_at_utc неизменяем (наследуется от первого перехода
        # заявки), updated_at_utc — время текущего перехода.
        if prev is not None and prev.get("created_at_utc"):
            record["created_at_utc"] = prev["created_at_utc"]
        else:
            record["created_at_utc"] = record["timestamp_utc"]
        record["updated_at_utc"] = record["timestamp_utc"]
        line = json.dumps(record, ensure_ascii=False)
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            logging.error(
                f"[ledger] НЕ ЗАПИСАНО: {status} order_id={order_id}: {type(e).__name__}: {e}"
            )
            return False

        if record["order_id"] is not None:
            self._latest[record["order_id"]] = record
        return True

    def record_intent(self, order_id: str, *, trade_id: str, figi: str, ticker: str,
                      side: str, requested_lots: int, reason: str = "") -> bool:
        """INTENT до сетевого вызова. False -> заявка НЕ отправляется."""
        return self.append(STATUS_INTENT, order_id, trade_id=trade_id, figi=figi,
                           ticker=ticker, side=side, requested_lots=requested_lots,
                           reason=reason, source="pre_send")

    def record_sent(self, order_id: str, *, trade_id: str = None, figi: str = None,
                    ticker: str = None, side: str = None, requested_lots: int = None,
                    executed_lots: int = None, avg_exec_price: float = None,
                    status_hint: str = None, reason: str = "", source: str = "post_response",
                    error: str = None) -> bool:
        """
        SENT сразу после ответа post_sandbox_order.

        Дефект №8: FILL не считается окончательно подтверждённым без
        проверенного executed_lots > 0. Если ответ брокера содержит FILL,
        а количество исполненных лотов отсутствует или равно нулю — статус
        и количество противоречат друг другу: заявка записывается как
        UNCERTAIN (НЕ терминальный FILL и НЕ отказ): остаётся в
        open_orders(), блокирует новые BUY по FIGI и разрешается повторным
        запросом состояния у брокера после восстановления связи.
        """
        status = STATUS_SENT if status_hint is None else status_hint
        if status == STATUS_FILL:
            try:
                le_confirmed = executed_lots is not None and int(executed_lots) > 0
            except (TypeError, ValueError):
                le_confirmed = False
            if not le_confirmed:
                logging.error(
                    f"[ledger] FILL противоречие: executed_lots={executed_lots!r} — "
                    f"UNCERTAIN вместо терминального FILL (order_id={order_id})"
                )
                return self.append(
                    STATUS_UNCERTAIN, order_id, trade_id=trade_id, figi=figi,
                    ticker=ticker, side=side, requested_lots=requested_lots,
                    executed_lots=executed_lots,
                    reason="FILL противоречие: executed_lots не подтверждён (>0) — "
                           "требуется повторный запрос состояния у брокера",
                    source=source, error=error,
                )
        return self.append(status, order_id, trade_id=trade_id, figi=figi, ticker=ticker,
                           side=side, requested_lots=requested_lots,
                           executed_lots=executed_lots, avg_exec_price=avg_exec_price,
                           reason=reason, source=source, error=error)

    def record_status(self, order_id: str, status: str, *, trade_id: str = None,
                      figi: str = None, ticker: str = None, side: str = None,
                      requested_lots: int = None, executed_lots: int = None,
                      lots_left: int = None, avg_exec_price: float = None,
                      reason: str = "", source: str = "", error: str = None) -> bool:
        """Подтверждённый брокером переход (NEW/PARTIALLYFILL/FILL/CANCELLED/...)."""
        return self.append(status, order_id, trade_id=trade_id, figi=figi, ticker=ticker,
                           side=side, requested_lots=requested_lots,
                           executed_lots=executed_lots, lots_left=lots_left,
                           avg_exec_price=avg_exec_price, reason=reason, source=source,
                           error=error)

    def record_uncertain(self, order_id: str, *, trade_id: str = None, figi: str = None,
                         ticker: str = None, side: str = None, requested_lots: int = None,
                         executed_lots: int = None, reason: str = "", source: str = "",
                         error: str = None) -> bool:
        """Неопределённое состояние (НЕ отказ). Блокирует BUY по FIGI до разрешения."""
        return self.append(STATUS_UNCERTAIN, order_id, trade_id=trade_id, figi=figi,
                           ticker=ticker, side=side, requested_lots=requested_lots,
                           executed_lots=executed_lots, reason=reason, source=source,
                           error=error)

    # ------------------------------------------------------------------
    # Чтение / запросы
    # ------------------------------------------------------------------

    def latest(self, order_id: str) -> Optional[dict]:
        return self._latest.get(str(order_id))

    def has_order(self, order_id: str) -> bool:
        return str(order_id) in self._latest

    def latest_all(self) -> Dict[str, dict]:
        return dict(self._latest)

    def open_orders(self, side: Optional[str] = None) -> List[dict]:
        """Заявки в нетерминальном состоянии (INTENT/SENT/NEW/PARTIALLYFILL/UNCERTAIN)."""
        result = []
        for rec in self._latest.values():
            if rec["status"] in NON_TERMINAL_STATUSES:
                if side is None or rec.get("side") == side:
                    result.append(rec)
        return result

    def figi_has_open_order(self, figi: str, side: str = "BUY") -> bool:
        """Есть ли незавершённая заявка по FIGI строго данной стороны."""
        for rec in self._latest.values():
            if (rec.get("figi") == figi and rec.get("side") == side
                    and rec["status"] in NON_TERMINAL_STATUSES):
                return True
        return False

    def figi_blocks_buy(self, figi: str) -> bool:
        """
        Дефект №4: блокирует ли что-то новые BUY по данному FIGI.

        Блокируют ЛЮБЫЕ незавершённые заявки по FIGI:
          - незавершённая BUY (прежнее правило);
          - заявка с НЕИЗВЕСТНОЙ стороной (side=None): нельзя разрешать BUY
            только потому, что сторона заявки неизвестна;
          - UNCERTAIN любой стороны (направление/факт исполнения неизвестны).
        Терминальные заявки без исполнения блокировку снимают.
        """
        for rec in self._latest.values():
            if rec["status"] not in NON_TERMINAL_STATUSES:
                continue
            if rec.get("figi") != figi:
                continue
            if rec.get("side") == "BUY" or rec.get("side") is None:
                return True
            if rec["status"] == STATUS_UNCERTAIN:
                return True
        return False

    def has_unknown_figi_open_orders(self) -> bool:
        """
        Дефект №4: есть ли незавершённая заявка, у которой не определён FIGI.
        Такая заявка может затрагивать любой инструмент — до разрешения
        неопределённости новые BUY запрещены ГЛОБАЛЬНО (main.py).
        """
        for rec in self._latest.values():
            if rec["status"] in NON_TERMINAL_STATUSES and not rec.get("figi"):
                return True
        return False

    def figi_is_uncertain(self, figi: str, side: str = "BUY") -> bool:
        for rec in self._latest.values():
            if (rec.get("figi") == figi and rec.get("side") == side
                    and rec["status"] == STATUS_UNCERTAIN):
                return True
        return False

    def open_count(self, side: Optional[str] = None) -> int:
        return len(self.open_orders(side=side))

    def export_summary(self) -> List[dict]:
        """Краткий срез последних состояний (для state.json, диагностика)."""
        return [dict(rec) for rec in self._latest.values()]

# execution.py
# Tsclp_v2.2_volatile — мониторинг позиций.
#
# ИЗМЕНЕНИЯ (этап 2):
#   - TrackedPosition: entry_requested_lots, entry_applied_lots,
#     close_requested_lots, close_applied_lots (раздельный учёт BUY/SELL).
#   - quantity — каноническое количество лотов (остаток).
#   - _extract_price(state, lots_executed, lot_size) — цена за ШТУКУ:
#     total / (lots_executed * lot_size). None при некорректных данных.
#   - resolve_closing(): убран `lots_executed or pos.quantity`;
#     CLOSED только при lots_executed >= close_requested_lots
#     И брокерском остатке 0 (сверка в main.py).
#     Накопленный учёт: new = lots_executed - close_applied_lots;
#     при new < 0 или противоречии — UNCERTAIN, без самостоятельного
#     исправления quantity.
#     Статусы 2/3 с lots_executed > 0 -> UNCERTAIN (сверка в main.py).
#   - resolve_uncertain(pos, broker_positions) — новый (def, не async).
#     Сверка с брокерским портфелем (dict[str, PositionData]).
#     Учёт направления и количества. Неизвестное направление или
#     несоответствие количества -> UNCERTAIN.
#   - check_all(): monitoring_error (флаг ExecutionMonitor),
#     не client.uncertain_state. Сбрасывается в начале каждого вызова;
#     ставится True, если хотя бы у одной активной позиции нет цены.
#     Состояние позиции НЕ меняется.
#   - register(): защита от активного дубликата FIGI; валидация.
#   - export_state() / import_state(data) — для persistence (JSON-совместимый).
#   - monitoring_error — новый флаг.
#
# ИЗМЕНЕНИЯ (этап 5, партия P0 — устранение потери учёта заявок):
#   - PositionState.RECOVERY — НОВЫЙ статус: исполнение подтверждено (заявка
#     исполнена ИЛИ позиция обнаружена у брокера), но позиция ещё не может
#     управляться штатно (нет цены входа / нет SL/TP / не сохранён state).
#     RECOVERY НЕ закрывается автоматически; неизвестная цена входа НЕ заменяется
#     произвольной ценой. Все потребители (has_active_position, active_count,
#     check_all, reconcile, /status, persistence) поддерживают RECOVERY.
#   - TrackedPosition.recovery_source — причина RECOVERY (no_price/no_sl_tp/
#     adopted/save_failed) — для диагностики.
#   - register_recovery() — НОВАЯ единая точка регистрации подтверждённых
#     позиций с неполными данными (используется main.py из ветки FILL, опроса
#     заявок, reconcile-адопции и стартового восстановления).
#   - promote_recovery() — перевод RECOVERY -> OPEN при восстановлении цены
#     входа и расчёте SL/TP (расчёт SL/TP — через стратегию, без изменений).
#   - sync_recovery_quantity() — синхронизация количества RECOVERY-позиции
#     с ДОСТОВЕРНЫМ снимком брокера (адопция, направление не меняется).
#     (партия P0-3: управляет ТОЛЬКО остатком; счётчики BUY-исполнения
#     не подменяются остатком — см. record_recovery_entry_lots).
#   - check_all(): RECOVERY-позиции пропускаются без SL/TP-решений (SL/TP ещё
#     не заданы), monitoring_error для них не ставится.
#
# ИЗМЕНЕНИЯ (партия P0 — точечное исправление дефектов первой поставки):
#   Дефект №6: sync_recovery_quantity() хранит количество канонически
#     ПОЛОЖИТЕЛЬНЫМ (лоты), независимо от знака брокерского значения
#     (SHORT у брокера отрицателен). Знак, противоречащий направлению,
#     НЕ синхронизируется (отдаётся оператору/reconcile); broker_lots=0
#     НЕ закрывает RECOVERY автоматически.
#     resolve_uncertain(): сравнение количества с брокером — по abs(lots)
#     (знак уже проверен через направление) — SHORT больше не застревает
#     в вечном UNCERTAIN из-за знака.
#
# НЕ ИЗМЕНЕНО: PositionState (кроме добавления RECOVERY), get_position,
#
# ИЗМЕНЕНИЯ (партия P0-2 — поздние исполнения после перехода в OPEN):
#   apply_late_entry_lots() — применяет поздние дополнительные лоты входной
#   заявки к УЖЕ ОТКРЫТОЙ (OPEN) позиции: отчёт брокера может обновиться
#   после терминального статуса (CANCELLED/EXPIRED с поздним ростом
#   lots_executed). Правила: только для OPEN/LONG с совпадающей
#   entry-заявкой; рост — quantity += дельта, SL/TP НЕ пересчитываются
#   (абсолютные уровни стратегии не меняются); уменьшение/нечисловое
#   значение — противоречие, без изменений; CLOSING/UNCERTAIN — без
#   автоматических изменений (сверка/оператор); RECOVERY — через
#   sync_recovery_quantity().
#   has_active_position, active_count, имена остальных полей TrackedPosition.
#
# ИЗМЕНЕНИЯ (партия P0-3 — разделение «остаток позиции» и «исполнение заявки»):
#   Дефект: sync_recovery_quantity() использовалась для ДВУХ РАЗНЫХ величин:
#     (А) актуализация остатка позиции по ДОСТОВЕРНОМУ снимку брокерского
#         портфеля и (Б) повторная обработка накопительного lots_executed
#         старой BUY-заявки. lots_executed — накопительное исполнение
#         КОНКРЕТНОЙ заявки; pos.quantity — текущий остаток позиции у брокера.
#         Они расходятся, если оператор вручную продал/докупил лоты. Повторный
#         ответ старой BUY-заявки НЕ должен восстанавливать проданные лоты,
#         а устаревший ответ с меньшим lots_executed НЕ должен уменьшать
#         позицию.
#   - sync_recovery_quantity() — ТОЛЬКО достоверный снимок брокерского
#     портфеля: quantity := abs(broker_lots), увеличение И уменьшение
#     разрешены (функция НЕ монотонна). Накопительные счётчики BUY-исполнения
#     (entry_applied_lots/executed_lots) при уменьшении остатка НЕ изменяются
#     (не подменяются остатком позиции); при увеличении — увеличиваются до
#     нового значения (новые исполненные лоты учтены локально).
#   - record_recovery_entry_lots() — НОВЫЙ метод класса: повторная обработка
#     накопительного lots_executed BUY-заявки для RECOVERY-позиции. Сравнение
#     с уже учтённым исполнением (entry_applied_lots) и с уже виденным у этой
#     же заявки значением (already_seen_lots из реестра заявок):
#       рост — к quantity добавляется ТОЛЬКО дельта действительно новых лотов,
#         счётчики исполнения обновляются;
#       равенство — ничего не меняется (повторный ответ);
#       уменьшение — противоречие/устаревший ответ: quantity и счётчики
#         не изменяются (неопределённость и блокировку новых BUY выставляет
#         вызывающий код main.py существующей архитектурой).
#     Связь заявки с позицией проверяется по entry_order_id; исполнение
#     чужой заявки не применяется. Только RECOVERY, только BUY.
#   - Оборванный черновик record_recovery_entry_lots на уровне модуля
#     (вне класса, никем не вызывался) заменён полноценным методом класса.

import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional

import config


class PositionState(str, Enum):
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    UNCERTAIN = "UNCERTAIN"
    RECOVERY = "RECOVERY"   # этап 5: исполнение подтверждено, позиция временно неуправляема


# Активные состояния (участвуют во всех потребителях учёта)
ACTIVE_STATES = (PositionState.OPEN, PositionState.CLOSING, PositionState.UNCERTAIN, PositionState.RECOVERY)


@dataclass
class TrackedPosition:
    figi: str
    ticker: str
    direction: str
    entry_price: float
    quantity: int                       # каноническое количество лотов (остаток)
    sl_price: float
    tp_price: float
    lot_size: int = 1
    state: PositionState = PositionState.OPEN
    close_reason: Optional[str] = None
    close_order_id: Optional[str] = None
    close_requested_at: Optional[str] = None
    close_price_before_order: float = 0.0
    executed_lots: int = 0
    execution_price: Optional[float] = None
    trade_id: str = ""
    signal_reason: str = ""
    signal_time: str = ""
    price_before_order: float = 0.0
    order_send_time: str = ""
    partial: bool = False
    logged: bool = False
    actual_commission: Optional[float] = None
    entry_actual_commission: Optional[float] = None
    entry_order_id: Optional[str] = None
    # Раздельный учёт BUY
    entry_requested_lots: int = 0
    entry_applied_lots: int = 0
    # Раздельный учёт SELL
    close_requested_lots: int = 0
    close_applied_lots: int = 0
    # Этап 5: причина статуса RECOVERY (no_price / no_sl_tp / adopted / save_failed)
    recovery_source: Optional[str] = None

    def to_dict(self) -> dict:
        """Сериализация для persistence. JSON-совместимый dict."""
        d = asdict(self)
        d["state"] = self.state.value  # Enum -> str
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "TrackedPosition":
        """Десериализация. Неизвестные ключи игнорируются."""
        d = dict(data)
        if "state" in d and isinstance(d["state"], str):
            d["state"] = PositionState(d["state"])
        # Убираем ключи, которых нет в dataclass (защита от старых форматов)
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        d = {k: v for k, v in d.items() if k in allowed}
        return cls(**d)


class ExecutionMonitor:
    def __init__(self, client):
        self.client = client
        self.positions: Dict[str, TrackedPosition] = {}
        self.monitoring_error: bool = False

    def register(self, **kwargs) -> TrackedPosition:
        """
        Регистрирует позицию.
        Защита от активного дубликата по FIGI: если уже есть активная
        позиция (OPEN/CLOSING/UNCERTAIN) — не перезаписываем, логируем
        ошибку, возвращаем существующую.
        Валидация: quantity > 0, lot_size > 0, entry_price > 0, sl/tp > 0.
        """
        figi = kwargs.get("figi")
        if figi and self.has_active_position(figi):
            existing = self.positions[figi]
            logging.error(
                f"[execution] register: активная позиция по {figi} уже есть "
                f"(state={existing.state.value}). Перезапись запрещена."
            )
            return existing

        # Валидация (мягкая: не бросаем исключение, логируем и возвращаем как есть,
        # чтобы main.py мог решить; но для критических полей — проверяем).
        qty = kwargs.get("quantity")
        lot = kwargs.get("lot_size", 1)
        entry = kwargs.get("entry_price")
        sl = kwargs.get("sl_price")
        tp = kwargs.get("tp_price")
        if qty is not None and qty <= 0:
            logging.error(f"[execution] register: quantity={qty} <= 0")
        if lot is not None and lot <= 0:
            logging.error(f"[execution] register: lot_size={lot} <= 0")
        if entry is not None and entry <= 0:
            logging.error(f"[execution] register: entry_price={entry} <= 0")
        if sl is not None and sl <= 0:
            logging.error(f"[execution] register: sl_price={sl} <= 0")
        if tp is not None and tp <= 0:
            logging.error(f"[execution] register: tp_price={tp} <= 0")

        pos = TrackedPosition(**kwargs)
        self.positions[pos.figi] = pos
        if pos.state == PositionState.RECOVERY:
            # Этап 5: без форматирования цены — она может быть неизвестна (это НЕ ошибка)
            logging.warning(
                f"[execution] RECOVERY {pos.ticker} {pos.direction} qty={pos.quantity} "
                f"lot={pos.lot_size} order_id={pos.entry_order_id} "
                f"source={pos.recovery_source} — позиция подтверждена, цена входа/SL/TP неизвестны"
            )
        else:
            logging.info(
                f"[execution] OPEN {pos.ticker} {pos.direction} "
                f"entry={pos.entry_price:.4f} sl={pos.sl_price:.4f} tp={pos.tp_price:.4f} "
                f"qty={pos.quantity} lot={pos.lot_size} partial={pos.partial} "
                f"entry_req={pos.entry_requested_lots} entry_applied={pos.entry_applied_lots}"
            )
        return pos

    def register_recovery(
        self,
        figi: str,
        ticker: str,
        direction: str,
        quantity: int,
        lot_size: int,
        trade_id: str = "",
        order_id: Optional[str] = None,
        signal_reason: str = "",
        signal_time: str = "",
        price_before_order: float = 0.0,
        order_send_time: str = "",
        requested_lots: int = 0,
        partial: bool = False,
        entry_commission: Optional[float] = None,
        execution_price: Optional[float] = None,
        recovery_source: str = "adopted",
    ) -> TrackedPosition:
        """
        ЕДИНАЯ ТОЧКА регистрации подтверждённой позиции с неполными данными (этап 5).

        Исполнение подтверждено (заявка исполнена или позиция обнаружена у
        брокера по ДОСТОВЕРНОМУ снимку), но цена входа и/или SL/TP неизвестны.
        Позиция РЕГИСТРИРУЕТСЯ немедленно и не может быть потеряна.

        Защита от дубля: если по FIGI уже есть активная позиция — существующая
        НЕ перезаписывается (идемпотентность reconcile/recovery-путей).

        entry_price/sl/tp = 0.0 — явная отметка «данные неизвестны», а НЕ
        произвольная цена. Автоматическое закрытие RECOVERY ЗАПРЕЩЕНО.
        """
        if self.has_active_position(figi):
            existing = self.positions[figi]
            logging.warning(
                f"[execution] register_recovery: {figi} уже есть активная позиция "
                f"(state={existing.state.value}) — дубль не создаётся"
            )
            return existing

        pos = TrackedPosition(
            figi=figi,
            ticker=ticker,
            direction=direction,
            entry_price=0.0,          # 0.0 = неизвестно (НЕ произвольная цена)
            quantity=int(quantity),
            sl_price=0.0,
            tp_price=0.0,
            lot_size=int(lot_size) if lot_size else 1,
            state=PositionState.RECOVERY,
            executed_lots=int(quantity),
            trade_id=trade_id,
            signal_reason=signal_reason,
            signal_time=signal_time,
            price_before_order=price_before_order,
            order_send_time=order_send_time,
            partial=partial,
            entry_requested_lots=int(requested_lots) if requested_lots else int(quantity),
            entry_applied_lots=int(quantity),
            entry_order_id=order_id,
            entry_actual_commission=entry_commission,
            execution_price=execution_price,  # известная цена исполнения (НЕ entry_price)
            recovery_source=recovery_source,
        )
        self.positions[figi] = pos
        logging.warning(
            f"[execution] RECOVERY {pos.ticker} {pos.direction} qty={pos.quantity} "
            f"lot={pos.lot_size} order_id={order_id or '—'} source={recovery_source} "
            f"— исполнение подтверждено, цена входа/SL/TP неизвестны"
        )
        return pos

    def promote_recovery(
        self,
        pos: TrackedPosition,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        execution_price: Optional[float] = None,
    ) -> bool:
        """
        Перевод RECOVERY -> OPEN после восстановления цены входа и расчёта SL/TP.
        Только при подтверждённых данных: entry_price > 0, sl > 0, tp > 0.
        Расчёт SL/TP выполняет вызывающий код через rm.calculate_sl_tp (стратегия
        не меняется). Возвращает True при успехе.
        """
        if pos.state != PositionState.RECOVERY:
            return False
        if (not entry_price or entry_price <= 0
                or not sl_price or sl_price <= 0
                or not tp_price or tp_price <= 0):
            logging.error(
                f"[execution] promote_recovery {pos.ticker}: некорректные данные "
                f"(entry={entry_price}, sl={sl_price}, tp={tp_price}) — остаётся RECOVERY"
            )
            return False
        pos.entry_price = float(entry_price)
        pos.sl_price = float(sl_price)
        pos.tp_price = float(tp_price)
        pos.execution_price = float(execution_price) if execution_price else float(entry_price)
        pos.recovery_source = None
        pos.state = PositionState.OPEN
        logging.info(
            f"[execution] RECOVERY→OPEN {pos.ticker} entry={pos.entry_price:.4f} "
            f"sl={pos.sl_price:.4f} tp={pos.tp_price:.4f} qty={pos.quantity}"
        )
        return True

    def sync_recovery_quantity(self, pos: TrackedPosition, broker_lots: int) -> bool:
        """
        Синхронизирует количество RECOVERY-позиции с ДОСТОВЕРНЫМ снимком брокера.
        Вызывается только из reconcile при подтверждённом снимке (этап 5).
        Направление не меняется. Возвращает True при изменении.

        Дефект №6: количество хранится в ЕДИНОМ каноническом формате —
        ПОЛОЖИТЕЛЬНОЕ число лотов, сторона — отдельно (pos.direction).
        Знак брокерского значения (SHORT отрицателен) в pos.quantity
        НЕ переносится; двойного применения знака нет ни в синхронизации,
        ни в расчётах, ни при закрытии.
        Правила (партия P0-3 — разделение величин):
          - broker_lots == 0 — синхронизация НЕ выполняется, RECOVERY
            НЕ закрывается автоматически (решение — оператор/reconcile);
          - знак broker_lots противоречит направлению (LONG ожидает > 0,
            SHORT ожидает < 0) — синхронизация НЕ выполняется,
            расхождение отдаётся оператору;
          - pos.quantity = abs(broker_lots) — И увеличение, И уменьшение
            разрешены: достоверный снимок портфеля управляет текущим
            остатком (оператор мог продать часть позиции); функция
            НЕ делается монотонной;
          - накопительные счётчики BUY-исполнения
            (entry_applied_lots/executed_lots) при УМЕНЬШЕНИИ остатка
            НЕ изменяются (не подменяются остатком позиции: исполненные
            входные лоты не убывают); при УВЕЛИЧЕНИИ остатка — увеличиваются
            до abs(broker_lots) (новые исполненные лоты теперь учтены).
        НЕ используется для обработки lots_executed конкретной заявки —
        для этого существует record_recovery_entry_lots().
        """
        if pos.state != PositionState.RECOVERY:
            return False
        try:
            lots = int(broker_lots)
        except (TypeError, ValueError):
            logging.error(
                f"[execution] sync_recovery {pos.ticker}: некорректное "
                f"broker_lots={broker_lots!r} — синхронизация не выполнена"
            )
            return False
        if lots == 0:
            logging.error(
                f"[execution] sync_recovery {pos.ticker}: broker_lots=0 — "
                f"синхронизация не выполнена, автозакрытие RECOVERY запрещено "
                f"(решение за оператором/reconcile)"
            )
            return False
        expected_positive = (pos.direction == "BUY")  # BUY=LONG: у брокера > 0
        if (lots > 0) != expected_positive:
            logging.error(
                f"[execution] sync_recovery {pos.ticker}: знак broker_lots={lots} "
                f"противоречит направлению {pos.direction} — синхронизация не выполнена"
            )
            return False
        abs_lots = abs(lots)
        if abs_lots == pos.quantity:
            return False
        applied = int(pos.entry_applied_lots or 0)
        side_word = "SHORT" if lots < 0 else "LONG"
        if abs_lots < applied:
            # Остаток у брокера меньше учтённого исполнения BUY — часть лотов
            # продана (оператор/внешняя операция). Остаток следует достоверному
            # снимку; накопительное исполнение BUY НЕ уменьшается и НЕ
            # подменяется остатком позиции.
            logging.info(
                f"[execution] sync_recovery {pos.ticker}: lots брокера={lots} "
                f"({side_word}) < учтённого исполнения BUY {applied} — "
                f"остаток {pos.quantity} -> {abs_lots}, счётчики исполнения "
                f"сохранены ({applied})"
            )
            pos.quantity = abs_lots
            return True
        logging.info(
            f"[execution] sync_recovery {pos.ticker}: lots брокера={lots} "
            f"({side_word}), локально={pos.quantity} — "
            f"синхронизировано (каноническое количество {abs_lots})"
        )
        pos.quantity = abs_lots
        if abs_lots > applied:
            # Рост остатка = новые исполненные BUY-лоты, теперь учтённые локально.
            pos.executed_lots = abs_lots
            pos.entry_applied_lots = abs_lots
        return True

    def record_recovery_entry_lots(
        self, pos: TrackedPosition, executed_lots: int,
        order_id: Optional[str] = None,
        already_seen_lots: Optional[int] = None,
    ) -> bool:
        """
        Партия P0-3: повторная обработка накопительного lots_executed
        BUY-заявки для RECOVERY-позиции.

        ОТЛИЧАЕТСЯ от sync_recovery_quantity():
          sync_recovery_quantity — ДОСТОВЕРНЫЙ снимок брокерского портфеля:
            управляет текущим остатком pos.quantity (в обе стороны);
          record_recovery_entry_lots — накопительное исполнение КОНКРЕТНОЙ
            BUY-заявки (lots_executed от брокера): управляет ТОЛЬКО учётом
            исполненных входных лотов. lots_executed заявки и остаток позиции
            у брокера — разные величины: повторный ответ старой заявки НЕ
            восстанавливает проданные оператором лоты.

        Правила:
          - только RECOVERY-позиция направления BUY (входные заявки робота —
            BUY; SHORT-RECOVERY остаётся под контролем оператора/сверки);
          - связь заявки с позицией: order_id должен совпадать с
            entry_order_id (если оба известны) — исполнение ЧУЖОЙ заявки
            не применяется;
          - база сравнения = max(entry_applied_lots, already_seen_lots):
            учтённое накопительное исполнение этой заявки и значение, уже
            виденное у неё в реестре. Разница (seen - applied), если она
            есть, уже покрыта достоверной сверкой портфеля и повторно в
            позицию НЕ добавляется (иначе восстанавливались бы проданные
            лоты);
          - le > базы: к quantity добавляется ТОЛЬКО дельта действительно
            новых исполнений, счётчики (entry_applied_lots/executed_lots)
            обновляются на le;
          - le == базы: ничего не меняется (повторный ответ с тем же
            значением); если при этом le > entry_applied_lots — догоняются
            только счётчики (разрыв покрыт сверкой), quantity НЕ меняется;
          - le < базы: противоречие/устаревший ответ — quantity и счётчики
            НЕ изменяются (исполнение не убывает); неопределённость и
            блокировку новых BUY выставляет вызывающий код main.py
            существующей архитектурой (client.uncertain_state + реестр).
        Возвращает True, если состояние позиции изменилось.
        """
        if pos.state != PositionState.RECOVERY:
            logging.warning(
                f"[execution] record_recovery_entry {pos.ticker}: состояние "
                f"{pos.state.value} — обработка lots_executed входной заявки "
                f"применима только к RECOVERY (OPEN — apply_late_entry_lots)"
            )
            return False
        if pos.direction != "BUY":
            logging.error(
                f"[execution] record_recovery_entry {pos.ticker}: направление "
                f"{pos.direction} — накопительное исполнение BUY-заявки "
                f"неприменимо"
            )
            return False
        if order_id and pos.entry_order_id and order_id != pos.entry_order_id:
            logging.error(
                f"[execution] record_recovery_entry {pos.ticker}: заявка "
                f"{order_id} не совпадает с entry_order_id={pos.entry_order_id} "
                f"— исполнение чужой заявки не применяется"
            )
            return False
        try:
            le = int(executed_lots)
        except (TypeError, ValueError):
            logging.error(
                f"[execution] record_recovery_entry {pos.ticker}: некорректное "
                f"executed_lots={executed_lots!r}"
            )
            return False
        if le < 0:
            logging.error(
                f"[execution] record_recovery_entry {pos.ticker}: отрицательное "
                f"executed_lots={le} — не накопительное исполнение BUY"
            )
            return False

        applied = int(pos.entry_applied_lots or 0)
        baseline = applied
        if already_seen_lots is not None:
            try:
                seen = int(already_seen_lots)
            except (TypeError, ValueError):
                seen = 0
            if seen > baseline:
                baseline = seen

        if le < baseline:
            logging.error(
                f"[execution] record_recovery_entry {pos.ticker}: "
                f"lots_executed={le} < уже учтённых/виденных {baseline} "
                f"(applied={applied}) — противоречие (исполнение не убывает); "
                f"quantity={pos.quantity} и счётчики не изменены"
            )
            return False

        if le == baseline:
            if le == applied:
                # Повторный ответ с тем же значением — ничего не менять.
                return False
            # le уже видился у этой заявки (seen), но в позицию учтено меньше:
            # разрыв покрыт достоверной сверкой (проданные лоты НЕ
            # восстанавливаются) — догоняются только счётчики исполнения.
            pos.entry_applied_lots = le
            pos.executed_lots = le
            logging.warning(
                f"[execution] record_recovery_entry {pos.ticker}: "
                f"lots_executed={le} уже виден у заявки (учтено было "
                f"{applied}) — счётчики исполнения догнаны, остаток "
                f"quantity={pos.quantity} сохранён"
            )
            return True

        delta = le - baseline
        pos.quantity = pos.quantity + delta
        pos.entry_applied_lots = le
        pos.executed_lots = le
        logging.warning(
            f"[execution] record_recovery_entry {pos.ticker}: новые исполненные "
            f"лоты входной BUY-заявки +{delta} (итого исполнено {le}) — "
            f"quantity={pos.quantity}, счётчики обновлены"
        )
        return True

    def apply_late_entry_lots(self, pos: TrackedPosition, executed_lots: int,
                              order_id: Optional[str] = None) -> bool:
        """
        Партия P0-2: поздние дополнительные лоты входной заявки ПОСЛЕ перехода
        позиции в OPEN (отчёт брокера обновился после терминального статуса,
        например CANCELLED с поздним ростом lots_executed).

        Правила:
          - применяется ТОЛЬКО к OPEN-позиции LONG (direction="BUY") с
            совпадающей entry-заявкой (order_id == entry_order_id, если
            оба известны);
          - рост (executed_lots > entry_applied_lots): quantity += дельта,
            entry_applied_lots/executed_lots = executed_lots. SL/TP НЕ
            пересчитываются — это абсолютные уровни стратегии (параметры
            стратегии не меняются);
          - уменьшение противоречит неизменности исполнения — БЕЗ изменений,
            False (исполнение не убывает);
          - CLOSING/UNCERTAIN — БЕЗ автоматических изменений (решение —
            сверка/оператор); RECOVERY обрабатывается
            record_recovery_entry_lots() (партия P0-3: lots_executed
            входной заявки и остаток позиции — разные величины;
            остаток меняется только по достоверным снимкам брокера
            через sync_recovery_quantity).
        Возвращает True, если количество обновлено.
        """
        if pos.state != PositionState.OPEN:
            logging.warning(
                f"[execution] apply_late {pos.ticker}: состояние {pos.state.value} — "
                f"автоматическое изменение количества запрещено "
                f"(RECOVERY — record_recovery_entry_lots, "
                f"CLOSING/UNCERTAIN — сверка/оператор)"
            )
            return False
        if pos.direction != "BUY":
            logging.error(
                f"[execution] apply_late {pos.ticker}: направление {pos.direction} — "
                f"поздние лоты входной BUY-заявки неприменимы"
            )
            return False
        if order_id and pos.entry_order_id and order_id != pos.entry_order_id:
            logging.error(
                f"[execution] apply_late {pos.ticker}: заявка {order_id} не совпадает "
                f"с entry_order_id={pos.entry_order_id} — без изменений"
            )
            return False
        try:
            le = int(executed_lots)
        except (TypeError, ValueError):
            logging.error(
                f"[execution] apply_late {pos.ticker}: некорректное executed_lots={executed_lots!r}"
            )
            return False
        applied = int(pos.entry_applied_lots or 0)
        if le <= applied:
            if le < applied:
                logging.error(
                    f"[execution] apply_late {pos.ticker}: lots_executed={le} < уже "
                    f"применённых {applied} — противоречие (исполнение не убывает), "
                    f"без изменений"
                )
            return False
        delta = le - applied
        pos.quantity = pos.quantity + delta
        pos.entry_applied_lots = le
        pos.executed_lots = le
        logging.warning(
            f"[execution] {pos.ticker}: ПОЗДНЕЕ исполнение входной заявки "
            f"+{delta} лот. (итого исполнено {le}) — quantity={pos.quantity}, "
            f"SL/TP сохранены ({pos.sl_price:.4f}/{pos.tp_price:.4f})"
        )
        return True

    def get_position(self, figi: str) -> Optional[TrackedPosition]:
        return self.positions.get(figi)

    def has_active_position(self, figi: str) -> bool:
        p = self.positions.get(figi)
        return p is not None and p.state in ACTIVE_STATES

    async def check_all(self, prices: Dict[str, float]) -> List[TrackedPosition]:
        """
        Проверяет SL/TP для OPEN-позиций.
        monitoring_error сбрасывается в начале, ставится True, если хотя бы
        у одной активной позиции нет цены. Состояние позиции НЕ меняется.
        Возвращает список позиций, которые нужно закрыть (SL/TP).
        """
        to_close: List[TrackedPosition] = []
        self.monitoring_error = False

        if prices is None:
            logging.error("[execution] prices=None — monitoring_error=True")
            self.monitoring_error = True
            return to_close

        if not prices:
            logging.warning("[execution] prices={} — monitoring_error=True")
            self.monitoring_error = True
            return to_close

        for pos in list(self.positions.values()):
            if pos.state == PositionState.RECOVERY:
                # Этап 5: у RECOVERY нет SL/TP — решение по цене невозможно и ЗАПРЕЩЕНО.
                # Позиция не закрывается автоматически; фактом существования следит
                # reconcile по достоверному снимку брокера.
                logging.info(
                    f"[execution] {pos.ticker}: RECOVERY (source={pos.recovery_source}) — "
                    f"без SL/TP-решения, контроль существования через reconcile"
                )
                continue
            if pos.state != PositionState.OPEN:
                continue
            price = prices.get(pos.figi)
            if price is None or price <= 0:
                logging.warning(
                    f"[execution] {pos.ticker}: нет цены — monitoring_error=True, "
                    f"позиция без контроля (state={pos.state.value})"
                )
                self.monitoring_error = True
                continue
            if pos.direction == "BUY":
                if price <= pos.sl_price:
                    pos.state = PositionState.CLOSING
                    pos.close_reason = "SL"
                    pos.close_price_before_order = price
                    to_close.append(pos)
                elif price >= pos.tp_price:
                    pos.state = PositionState.CLOSING
                    pos.close_reason = "TP"
                    pos.close_price_before_order = price
                    to_close.append(pos)
        return to_close

    async def resolve_closing(self, pos: TrackedPosition):
        """
        Разрешает CLOSING-позицию по статусу заявки.
        Правила:
          - нет order_id -> UNCERTAIN;
          - state=None -> UNCERTAIN;
          - статус 1 (FILL): CLOSED только при lots_executed >= close_requested_lots.
            Иначе — частичное исполнение, накопленный учёт, UNCERTAIN (сверка в main.py).
          - статус 5 (PARTIALLYFILL): накопленный учёт, UNCERTAIN.
          - статусы 2/3 (REJECTED/CANCELLED): если lots_executed > 0 -> UNCERTAIN
            (сверка в main.py); иначе OPEN, close_order_id=None.
          - иначе -> UNCERTAIN.
        Накопленный учёт: new = lots_executed - close_applied_lots.
          Если new < 0 (уменьшение) — UNCERTAIN, без исправления quantity.
          Если new >= 0: close_applied_lots = lots_executed;
          quantity -= new (только для SELL-закрытия лонга).
        """
        if not pos.close_order_id:
            pos.state = PositionState.UNCERTAIN
            logging.error(f"[execution] {pos.ticker}: CLOSING без order_id → UNCERTAIN")
            return

        try:
            state = await self.client.get_sandbox_order_state(pos.close_order_id)
            if state is None:
                pos.state = PositionState.UNCERTAIN
                logging.error(f"[execution] {pos.ticker}: state=None → UNCERTAIN")
                return

            raw_status = getattr(state, "execution_report_status", None)
            try:
                status_int = int(raw_status) if raw_status is not None else -1
            except (TypeError, ValueError):
                status_int = -1

            lots_executed = int(getattr(state, "lots_executed", 0) or 0)
            actual_commission = self._extract_commission(state)

            # close_requested_lots: если не задано — используем quantity (для совместимости)
            requested = pos.close_requested_lots or pos.quantity

            logging.info(
                f"[resolve] {pos.ticker} order={pos.close_order_id} "
                f"raw_status={raw_status!r} status_int={status_int} "
                f"lots_executed={lots_executed} requested={requested} "
                f"qty={pos.quantity} close_applied={pos.close_applied_lots} "
                f"actual_commission={actual_commission}"
            )

            if status_int == 1:
                # FILL: CLOSED только при полном исполнении
                if lots_executed >= requested and requested > 0:
                    new = lots_executed - pos.close_applied_lots
                    if new < 0:
                        logging.error(
                            f"[execution] {pos.ticker}: lots_executed={lots_executed} "
                            f"< close_applied={pos.close_applied_lots} — противоречие, UNCERTAIN"
                        )
                        pos.state = PositionState.UNCERTAIN
                        return
                    # Накопленный учёт
                    pos.close_applied_lots = lots_executed
                    if new > 0 and pos.direction == "BUY":
                        pos.quantity = max(0, pos.quantity - new)
                    pos.executed_lots = lots_executed
                    pos.execution_price = self._extract_price(state, lots_executed, pos.lot_size)
                    pos.actual_commission = actual_commission
                    pos.state = PositionState.CLOSED
                    logging.info(f"[execution] CLOSED {pos.ticker} @ {pos.execution_price}")
                else:
                    # Частичное исполнение при статусе 1 (аномалия) — UNCERTAIN
                    logging.error(
                        f"[execution] {pos.ticker}: status=1, но lots_executed={lots_executed} "
                        f"< requested={requested} — UNCERTAIN"
                    )
                    pos.state = PositionState.UNCERTAIN

            elif status_int == 5:
                # PARTIALLYFILL: накопленный учёт, UNCERTAIN
                new = lots_executed - pos.close_applied_lots
                if new < 0:
                    logging.error(
                        f"[execution] {pos.ticker}: lots_executed={lots_executed} "
                        f"< close_applied={pos.close_applied_lots} — противоречие, UNCERTAIN"
                    )
                    pos.state = PositionState.UNCERTAIN
                    return
                pos.close_applied_lots = lots_executed
                if new > 0 and pos.direction == "BUY":
                    pos.quantity = max(0, pos.quantity - new)
                pos.executed_lots = lots_executed
                pos.actual_commission = actual_commission
                pos.state = PositionState.UNCERTAIN
                logging.warning(
                    f"[execution] {pos.ticker}: PARTIALLYFILL {lots_executed}/{requested} "
                    f"(учтено new={new}, qty={pos.quantity}) → UNCERTAIN"
                )

            elif status_int in (2, 3):
                if lots_executed > 0:
                    # Отмена/отклонение после частичного исполнения — UNCERTAIN
                    new = lots_executed - pos.close_applied_lots
                    if new >= 0:
                        pos.close_applied_lots = lots_executed
                        if new > 0 and pos.direction == "BUY":
                            pos.quantity = max(0, pos.quantity - new)
                    pos.state = PositionState.UNCERTAIN
                    logging.warning(
                        f"[execution] {pos.ticker}: status={status_int} с lots_executed={lots_executed} "
                        f"→ UNCERTAIN (сверка в main.py)"
                    )
                else:
                    pos.state = PositionState.OPEN
                    pos.close_order_id = None
                    logging.warning(
                        f"[execution] {pos.ticker}: закрытие отклонено/отменено (lots_executed=0) — снова OPEN"
                    )
            else:
                pos.state = PositionState.UNCERTAIN
                logging.warning(f"[execution] {pos.ticker}: статус {status_int} → UNCERTAIN")

        except Exception as e:
            logging.error(f"[execution] {pos.ticker}: ошибка проверки статуса: {type(e).__name__}: {e}")
            pos.state = PositionState.UNCERTAIN

    def resolve_uncertain(self, pos: TrackedPosition, broker_positions: Dict[str, "PositionData"]):
        """
        Разрешает UNCERTAIN-позицию сверкой с брокерским портфелем.
        broker_positions: dict[figi, PositionData] (из tinkoff_client.get_positions).
        Правила:
          - FIGI отсутствует у брокера: если портфель достоверный — CLOSED (quantity=0);
            но достоверность обеспечивает вызывающий код (main.py). Если портфель
            неполный — main.py не должен вызывать resolve_uncertain.
          - FIGI есть у брокера: сверить lots (лоты со знаком) с pos.quantity.
            Если lots != pos.quantity (с учётом направления) — UNCERTAIN.
            Если lots == pos.quantity — обновить quantity, state=OPEN.
          - Направление: если direction у брокера SHORT, а у нас BUY (LONG) — UNCERTAIN.
            Если direction == UNKNOWN — UNCERTAIN.
        Не async: портфель уже загружен вызывающим кодом.
        """
        figi = pos.figi
        broker = broker_positions.get(figi)

        if broker is None:
            # FIGI нет у брокера. Если портфель достоверный — считаем CLOSED.
            # Достоверность обеспечивает main.py (не вызывает при None/неполном).
            pos.quantity = 0
            pos.state = PositionState.CLOSED
            logging.info(
                f"[execution] resolve_uncertain {pos.ticker}: нет у брокера → CLOSED"
            )
            return

        if broker.direction == "UNKNOWN":
            logging.error(
                f"[execution] resolve_uncertain {pos.ticker}: direction=UNKNOWN → остаётся UNCERTAIN"
            )
            pos.state = PositionState.UNCERTAIN
            return

        # Проверка направления: у нас direction="BUY" (LONG), у брокера SHORT — расхождение
        our_side = "LONG" if pos.direction == "BUY" else "SHORT"
        if broker.direction != our_side:
            logging.error(
                f"[execution] resolve_uncertain {pos.ticker}: направление брокера={broker.direction}, "
                f"локально={our_side} — расхождение, остаётся UNCERTAIN"
            )
            pos.state = PositionState.UNCERTAIN
            return

        # Сверка количества (лоты).
        # Дефект №6: направление уже сверено выше (брокер = наша сторона),
        # поэтому сравниваем МОДУЛЬ количества: локально quantity хранится
        # канонически положительным, у брокера SHORT отрицателен.
        broker_lots_abs = abs(int(broker.lots))
        if broker_lots_abs != pos.quantity:
            logging.error(
                f"[execution] resolve_uncertain {pos.ticker}: lots брокера={broker.lots}, "
                f"локально={pos.quantity} — расхождение, остаётся UNCERTAIN"
            )
            pos.state = PositionState.UNCERTAIN
            return

        # Всё сходится
        pos.quantity = broker_lots_abs
        pos.state = PositionState.OPEN
        logging.info(
            f"[execution] resolve_uncertain {pos.ticker}: сверка OK "
            f"(lots={broker.lots}, qty={pos.quantity}) → OPEN"
        )

    @staticmethod
    def _extract_price(state, lots_executed: int, lot_size: int) -> Optional[float]:
        """
        Возвращает цену за ОДНУ БУМАГУ (штуку).
        executed_order_price = суммарная стоимость исполнения.
        price_per_share = total / (lots_executed * lot_size).
        None при некорректных данных (lots_executed <= 0, lot_size <= 0, нет цены).
        """
        try:
            ep = getattr(state, "executed_order_price", None)
            if ep is None:
                return None
            units = getattr(ep, "units", 0)
            nano = getattr(ep, "nano", 0)
            total = float(units) + float(nano) / 1e9
            if lots_executed > 0 and lot_size > 0:
                return total / (lots_executed * lot_size)
            return None
        except Exception:
            return None

    @staticmethod
    def _extract_commission(state) -> Optional[float]:
        try:
            ec = getattr(state, "executed_commission", None)
            if ec is None:
                return None
            units = getattr(ec, "units", 0)
            nano = getattr(ec, "nano", 0)
            value = abs(float(units) + float(nano) / 1e9)
            if value <= 0:
                return None
            return value
        except Exception:
            return None

    def active_count(self) -> int:
        return sum(
            1 for p in self.positions.values()
            if p.state in ACTIVE_STATES
        )

    def export_state(self) -> dict:
        """Сериализация всех позиций для persistence (JSON-совместимый dict)."""
        return {
            "positions": {figi: pos.to_dict() for figi, pos in self.positions.items()},
            "monitoring_error": self.monitoring_error,
        }

    def import_state(self, data: dict) -> None:
        """
        Восстановление позиций из persistence.
        Неизвестные ключи игнорируются. Некорректные записи пропускаются с логом.
        """
        if not isinstance(data, dict):
            logging.error("[execution] import_state: data не dict")
            return
        self.positions = {}
        positions = data.get("positions", {})
        if not isinstance(positions, dict):
            logging.error("[execution] import_state: positions не dict")
            return
        for figi, pos_data in positions.items():
            try:
                self.positions[figi] = TrackedPosition.from_dict(pos_data)
            except Exception as e:
                logging.error(
                    f"[execution] import_state: не удалось восстановить {figi}: "
                    f"{type(e).__name__}: {e}"
                )
        self.monitoring_error = bool(data.get("monitoring_error", False))
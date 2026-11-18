# tests/test_p0_batch2_late_execution.py
# Tsclp_v2.2_volatile — тесты партии P0-2: поздние исполнения и промоция RECOVERY.
#
# Соответствие дефект ↔ тест:
#   Дефект «преждевременный RECOVERY -> OPEN, пока заявка может получить
#       дополнительные исполнения»:
#       test_n1_no_premotion_while_order_alive
#       test_n1_promotion_only_at_terminal_syncs_final_quantity
#   Дефект «обновление количества, если поздние лоты поступили после OPEN»:
#       test_n2_open_position_late_lots_increase_no_double
#       test_n2_open_position_shrink_is_contradiction
#   Дефект «исполнение с пустым FIGI как обычная BUY-позиция»:
#       test_n3_unknown_figi_execution_not_registered_global_block
#       test_n3_register_guard_direct_returns_none
#   Дефект «позднее исполнение после терминального CANCELLED: сверка не
#       запускалась»:
#       test_ledger_open_orders_includes_pending_residual
#       test_n4_late_growth_after_terminal_cancelled_actually_repolled
#       test_n4_no_resurrection_after_position_closed
#   Интеграционный сценарий trading_cycle
#   (частичное исполнение -> отмена -> RECOVERY -> OPEN -> поздние лоты):
#       test_integration_trading_cycle_partial_cancel_recovery_open_late_lots
#
# Тесты проверяют ПОСЛЕДСТВИЯ: появилось ли количество в позиции, изменились
# ли SL/TP, создан ли дубликат, заблокированы ли новые BUY, запустилась ли
# повторная сверка, не «воскрешена» ли закрытая позиция.

from decimal import Decimal

import pytest

import main
from execution import ExecutionMonitor, PositionState
from tinkoff_client import PositionData

from tests.test_p0_defect_fixes import (
    FakeClient, FakeRM, FakeSM,
    fake_state, make_ledger, small_df, env,  # env — общий fixture изоляции
)
from tests.test_p0_defect_fixes import (
    FILL, REJECTED, CANCELLED, NEW, PARTIALLYFILL, EXPIRED, FIGI, TICKER,
)

ACTIVE = (PositionState.OPEN, PositionState.CLOSING,
          PositionState.UNCERTAIN, PositionState.RECOVERY)


def active_positions(monitor):
    return [p for p in monitor.positions.values() if p.state in ACTIVE]


# ============================================================
# Дефект: сверка после терминального CANCELLED не запускалась
# (механика order_ledger.open_orders)
# ============================================================

def test_ledger_open_orders_includes_pending_residual(tmp_path):
    """Терминальные заявки С ИСПОЛНЕНИЕМ остаются доступны для повторной
    сверки; терминальные без исполнения и полный FILL — закрыты."""
    ledger = make_ledger(tmp_path)
    # терминальная с исполнением — residual-сверка
    ledger.append("CANCELLED", "o1", figi=FIGI, side="BUY",
                  requested_lots=10, executed_lots=3)
    # терминальная без исполнения — достоверно разрешена
    ledger.append("CANCELLED", "o2", figi=FIGI, side="BUY",
                  requested_lots=10, executed_lots=0)
    # полный FILL — достоверно разрешён
    ledger.append("FILL", "o3", figi=FIGI, side="BUY",
                  requested_lots=5, executed_lots=5)
    # FILL с исполнением меньше запрошенного — рост ещё возможен (защитно)
    ledger.append("FILL", "o4", figi=FIGI, side="BUY",
                  requested_lots=5, executed_lots=2)
    # активная заявка — прежнее поведение
    ledger.append("NEW", "o5", figi=FIGI, side="BUY",
                  requested_lots=5, executed_lots=0)

    ids = {r["order_id"] for r in ledger.open_orders()}
    assert {"o1", "o4", "o5"} <= ids, "заявка с residual-исполнением недоступна для сверки"
    assert "o2" not in ids, "терминальная без исполнения не должна опрашиваться"
    assert "o3" not in ids, "полный FILL не должен опрашиваться"
    assert {r["order_id"] for r in ledger.open_orders(side="BUY")} == {"o1", "o4", "o5"}


# ============================================================
# Дефект: преждевременный RECOVERY -> OPEN
# ============================================================

@pytest.mark.asyncio
async def test_n1_no_premotion_while_order_alive(env):
    """RECOVERY не повышается до OPEN, пока входная заявка активна
    (PARTIALLYFILL): возможны дополнительные исполнения. Цена, уже видная
    у живой заявки, не является основанием для промоции."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={FIGI: PositionData(lots=2, direction="LONG",
                                                      raw_shares=Decimal(2))})
    oid = "oid-alive"
    monitor = ExecutionMonitor(client)
    pos = monitor.register_recovery(figi=FIGI, ticker=TICKER, direction="BUY",
                                    quantity=2, lot_size=1, order_id=oid,
                                    recovery_source="no_price")

    # у живой заявки УЖЕ есть цена исполнения — промоция всё равно запрещена
    client.order_states[oid] = fake_state(PARTIALLYFILL, lots_executed=2, price_units=200)
    await main.promote_recovery_positions(client, monitor, FakeRM())
    assert pos.state == PositionState.RECOVERY, \
        "преждевременный RECOVERY->OPEN при активной заявке (возможны доп. исполнения)"

    # статус недоступен — тоже без промоции
    client.order_states[oid] = None
    await main.promote_recovery_positions(client, monitor, FakeRM())
    assert pos.state == PositionState.RECOVERY


@pytest.mark.asyncio
async def test_n1_promotion_only_at_terminal_syncs_final_quantity(env):
    """При ТЕРМИНАЛЬНОМ статусе заявки промоция выполняется; перед промоцией
    количество доуточняется по финальному lots_executed (2 -> 5)."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    oid = "oid-terminal"
    monitor = ExecutionMonitor(client)
    pos = monitor.register_recovery(figi=FIGI, ticker=TICKER, direction="BUY",
                                    quantity=2, lot_size=1, order_id=oid,
                                    recovery_source="no_price")

    # финальный отчёт: CANCELLED, исполнено 5 (больше известного 2),
    # средняя цена 500 за 5 лотов = 100 за штуку
    client.order_states[oid] = fake_state(CANCELLED, lots_executed=5, price_units=500)
    await main.promote_recovery_positions(client, monitor, FakeRM(sl=95.0, tp=110.0))

    assert pos.state == PositionState.OPEN, "промоция не выполнена при терминальном статусе"
    assert pos.quantity == 5, "количество не доуточнено по финальному lots_executed"
    assert pos.entry_price == 100.0
    assert pos.sl_price == 95.0 and pos.tp_price == 110.0


# ============================================================
# Дефект: поздние лоты после перехода позиции в OPEN
# ============================================================

async def _open_position(env, ledger, client, oid, qty):
    monitor = ExecutionMonitor(client)
    pos = await main.register_executed_buy(
        client, monitor, FakeRM(sl=95.0, tp=110.0), ledger,
        figi=FIGI, ticker=TICKER, order_id=oid, trade_id="t-late",
        reason="unit", signal_time="now", price_before_order=100.0,
        order_send_time="now", requested_lots=10, executed_lots=qty,
        exec_price=100.0, lot_size=1, partial=False,
        df=small_df(), source="test", ledger_status="FILL",
    )
    return monitor, pos


@pytest.mark.asyncio
async def test_n2_open_position_late_lots_increase_no_double(env):
    """Поздние дополнительные лоты ПОСЛЕ перехода в OPEN: количество растёт
    точно на дельту, SL/TP не пересчитываются, повторный опрос не удваивает."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    oid = "oid-late-open"
    monitor, pos = await _open_position(env, ledger, client, oid, qty=2)
    assert pos.state == PositionState.OPEN and pos.quantity == 2
    entry_price = pos.entry_price
    sl, tp = pos.sl_price, pos.tp_price

    # поздний рост: отчёт брокера обновился после терминального статуса
    client.order_states[oid] = fake_state(CANCELLED, lots_executed=5, price_units=500)
    await main.poll_open_orders(client, monitor, FakeRM(), ledger)

    assert len(active_positions(monitor)) == 1, "дубликат позиции при позднем исполнении"
    pos = monitor.get_position(FIGI)
    assert pos.state == PositionState.OPEN
    assert pos.quantity == 5, "поздние лоты не применены к OPEN-позиции"
    assert pos.entry_applied_lots == 5
    assert pos.entry_price == entry_price, "цена входа изменилась"
    assert pos.sl_price == sl and pos.tp_price == tp, "SL/TP пересчитаны (стратегия не меняется)"

    # повторный опрос того же состояния — без повторного прибавления
    await main.poll_open_orders(client, monitor, FakeRM(), ledger)
    assert monitor.get_position(FIGI).quantity == 5, "лоты прибавлены повторно"
    assert len(active_positions(monitor)) == 1


@pytest.mark.asyncio
async def test_n2_open_position_shrink_is_contradiction(env):
    """Уменьшение lots_executed против уже применённого — противоречие:
    исполнение не убывает, количество не меняется."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    oid = "oid-shrink"
    monitor, pos = await _open_position(env, ledger, client, oid, qty=5)
    assert pos.quantity == 5 and pos.entry_applied_lots == 5

    client.order_states[oid] = fake_state(CANCELLED, lots_executed=3, price_units=300)
    await main.poll_open_orders(client, monitor, FakeRM(), ledger)
    assert monitor.get_position(FIGI).quantity == 5, "исполнение не должно убывать"
    assert monitor.get_position(FIGI).entry_applied_lots == 5
    assert len(active_positions(monitor)) == 1


# ============================================================
# Дефект: исполнение с пустым FIGI
# ============================================================

@pytest.mark.asyncio
async def test_n3_unknown_figi_execution_not_registered_global_block(env):
    """Подтверждённое исполнение с пустым FIGI НЕ регистрируется как обычная
    BUY-позиция: заявка остаётся UNCERTAIN, новые BUY запрещены ГЛОБАЛЬНО
    (в торговом цикле), существующие позиции продолжают мониториться."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    oid = "oid-nofigi"
    ledger.record_intent(oid, trade_id="t-nofigi", figi="", ticker=TICKER,
                         side="BUY", requested_lots=5)
    client.order_states[oid] = fake_state(FILL, lots_executed=4, price_units=400)
    monitor = ExecutionMonitor(client)
    monitor.register(figi="BBG00EXISTING", ticker="EXIST", direction="BUY",
                     entry_price=50.0, quantity=1, sl_price=49.0, tp_price=52.0,
                     lot_size=1, trade_id="t-exist", entry_requested_lots=1)

    await main.poll_open_orders(client, monitor, FakeRM(), ledger)

    # ПОСЛЕДСТВИЯ: позиции с пустым FIGI НЕТ
    assert len(monitor.positions) == 1, "исполнение с пустым FIGI зарегистрировано как позиция"
    assert monitor.get_position("") is None
    assert monitor.get_position(None) is None
    assert client.uncertain_state is True
    rec = ledger.latest(oid)
    assert rec["status"] == "UNCERTAIN", f"статус заявки: {rec['status']}"
    assert ledger.has_unknown_figi_open_orders() is True, "глобальная блокировка не активна"
    # существующая позиция продолжается мониториться
    assert monitor.get_position("BBG00EXISTING").state == PositionState.OPEN

    # глобальная блокировка действует в торговом цикле (даже после reconcile)
    main.ledger_ref = ledger
    rm, sm = FakeRM(), FakeSM("BUY")
    main.top_instruments.clear()
    main.top_instruments.extend([{"figi": FIGI, "ticker": TICKER}])
    client.prices_value = {FIGI: 100.0}
    await main.trading_cycle(client, rm, sm, monitor, ledger)
    assert sm.calls == 0, "новые BUY разрешены при заявке без FIGI"
    assert client.last_post is None


@pytest.mark.asyncio
async def test_n3_register_guard_direct_returns_none(env):
    """Прямой вызов единой точки регистрации с пустым FIGI не создаёт
    позицию и включает глобальную блокировку."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient()
    monitor = ExecutionMonitor(client)
    pos = await main.register_executed_buy(
        client, monitor, FakeRM(), ledger,
        figi="", ticker=TICKER, order_id="oid-nofigi-direct", trade_id="t-nf",
        reason="unit", signal_time="now", price_before_order=100.0,
        order_send_time="now", requested_lots=4, executed_lots=4,
        exec_price=100.0, lot_size=1, partial=False,
        df=small_df(), source="test",
    )
    assert pos is None, "позиция с пустым FIGI зарегистрирована"
    assert len(monitor.positions) == 0
    assert client.uncertain_state is True
    rec = ledger.latest("oid-nofigi-direct")
    assert rec is not None and rec["status"] == "UNCERTAIN"
    assert ledger.has_unknown_figi_open_orders() is True


# ============================================================
# Дефект: позднее исполнение после терминального CANCELLED —
# сверка реально запускается
# ============================================================

@pytest.mark.asyncio
async def test_n4_late_growth_after_terminal_cancelled_actually_repolled(env):
    """После регистрации исполнения по терминальному CANCELLED повторная
    сверка ПРОДОЛЖАЕТСЯ: поздний рост 3 -> 8 лотов обнаруживается опросом
    и применяется к RECOVERY-позиции без дубликатов и повторного счёта."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    oid = "oid-residual"
    ledger.record_intent(oid, trade_id="t-r", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=10)
    monitor = ExecutionMonitor(client)

    # фаза 1: терминальный CANCELLED с исполнением 3 -> RECOVERY qty=3
    client.order_states[oid] = fake_state(CANCELLED, lots_executed=3, price_units=300)
    await main.poll_open_orders(client, monitor, FakeRM(), ledger)
    pos = monitor.get_position(FIGI)
    assert pos is not None and pos.quantity == 3 and pos.state == PositionState.RECOVERY
    assert ledger.latest(oid)["status"] == "CANCELLED"

    # фаза 2: поздний рост исполнения до 8 — сверка обязана запуститься
    client.order_states[oid] = fake_state(CANCELLED, lots_executed=8, price_units=800)
    await main.poll_open_orders(client, monitor, FakeRM(), ledger)
    assert len(active_positions(monitor)) == 1, "дубликат при позднем исполнении после CANCELLED"
    pos = monitor.get_position(FIGI)
    assert pos.quantity == 8, "поздний рост после терминального CANCELLED не обнаружен сверкой"
    assert pos.state == PositionState.RECOVERY
    rec = ledger.latest(oid)
    assert rec["executed_lots"] == 8

    # фаза 3: состояние без изменений — учёт не меняется
    await main.poll_open_orders(client, monitor, FakeRM(), ledger)
    assert monitor.get_position(FIGI).quantity == 8
    assert len(active_positions(monitor)) == 1


@pytest.mark.asyncio
async def test_n4_no_resurrection_after_position_closed(env):
    """Закрытая позиция не «воскрешается» повторной регистрацией исполненного;
    поздний рост при закрытой позиции разрешается СВЕРКОЙ (адопция по
    достоверному снимку брокера показывает фактический остаток)."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    oid = "oid-closed"
    monitor, pos = await _open_position(env, ledger, client, oid, qty=3)
    assert pos.state == PositionState.OPEN and pos.quantity == 3
    pos.state = PositionState.CLOSED
    pos.quantity = 0

    # тот же отчёт брокера (3 == учтённым 3): повторная регистрация запрещена
    client.order_states[oid] = fake_state(CANCELLED, lots_executed=3, price_units=300)
    await main.poll_open_orders(client, monitor, FakeRM(), ledger)
    assert active_positions(monitor) == [], "закрытая позиция «воскрешена»"
    assert monitor.get_position(FIGI).state == PositionState.CLOSED

    # поздний рост (8 > 3): авторегистрация по отчёту заявки не выполняется,
    # включается неопределённость -> разрешается сверкой
    client.order_states[oid] = fake_state(CANCELLED, lots_executed=8, price_units=800)
    await main.poll_open_orders(client, monitor, FakeRM(), ledger)
    assert client.uncertain_state is True, "поздний рост при закрытой позиции не обнаружен"
    assert active_positions(monitor) == [], "позиция зарегистрирована по отчёту заявки вместо сверки"

    # сверка по ДОСТОВЕРНОМУ снимку брокера: фактический остаток 5 (8-3 продано)
    client.positions_value = {FIGI: PositionData(lots=5, direction="LONG",
                                                 raw_shares=Decimal(5))}
    await main.reconcile(client, monitor, ledger, FakeRM())
    adopted = monitor.get_position(FIGI)
    assert adopted is not None and adopted.state == PositionState.RECOVERY
    assert adopted.quantity == 5, "адопция не по фактическому остатку брокера"


# ============================================================
# Интеграция: trading_cycle
# частичное исполнение -> отмена -> RECOVERY -> OPEN -> поздние лоты
# ============================================================

@pytest.mark.asyncio
async def test_integration_trading_cycle_partial_cancel_recovery_open_late_lots(env):
    """Полная последовательность торгового цикла:
    1) BUY с частичным исполнением (2 из 5), отмена остатка, цена недоступна
       -> RECOVERY qty=2;
    2) заявка ещё жива (NEW с ценой) -> промоция НЕ выполняется;
    3) заявка терминальна (CANCELLED, цена доступна) -> RECOVERY -> OPEN;
    4) поздний рост исполнения 2 -> 5 -> количество OPEN-позиции растёт до 5,
       SL/TP сохранены, дубликатов нет."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM(sl=95.0, tp=110.0, size=5)
    sm = FakeSM("BUY")
    main.monitor_ref = monitor
    main.ledger_ref = ledger

    # --- Фаза 1: вход с частичным исполнением, отмена остатка, без цены ---
    main.top_instruments.clear()
    main.top_instruments.extend([{"figi": FIGI, "ticker": TICKER}])
    client.prices_value = {FIGI: 100.0}
    client.post_state = fake_state(PARTIALLYFILL, lots_executed=2)
    client.final_state = fake_state(CANCELLED, lots_executed=2, price_units=0)  # цены нет
    await main.trading_cycle(client, rm, sm, monitor, ledger)

    oid = client.last_post["order_id"]
    pos = monitor.get_position(FIGI)
    assert pos is not None, "исполнение потеряно"
    assert pos.state == PositionState.RECOVERY, "без цены позиция должна быть в RECOVERY"
    assert pos.quantity == 2
    assert pos.entry_order_id == oid
    rec = ledger.latest(oid)
    assert rec["status"] == "CANCELLED" and rec["executed_lots"] == 2

    # --- Фаза 2: заявка ещё жива (NEW с ценой) — промоции НЕТ ---
    client.positions_value = {FIGI: PositionData(lots=2, direction="LONG",
                                                 raw_shares=Decimal(2))}
    client.order_states[oid] = fake_state(NEW, lots_executed=2, price_units=200)
    await main.trading_cycle(client, rm, sm, monitor, ledger)
    pos = monitor.get_position(FIGI)
    assert pos.state == PositionState.RECOVERY, "промоция при живой заявке"
    assert pos.quantity == 2

    # --- Фаза 3: заявка терминальна, цена доступна — RECOVERY -> OPEN ---
    client.order_states[oid] = fake_state(CANCELLED, lots_executed=2, price_units=200)
    await main.trading_cycle(client, rm, sm, monitor, ledger)
    pos = monitor.get_position(FIGI)
    assert pos.state == PositionState.OPEN, "промоция не выполнена при терминальном статусе"
    assert pos.quantity == 2
    assert pos.entry_price == 100.0
    assert pos.sl_price == 95.0 and pos.tp_price == 110.0

    # --- Фаза 4: поздний рост исполнения 2 -> 5 (после OPEN) ---
    client.positions_value = {FIGI: PositionData(lots=5, direction="LONG",
                                                 raw_shares=Decimal(5))}
    client.order_states[oid] = fake_state(CANCELLED, lots_executed=5, price_units=500)
    client.last_post = None  # отслеживаем только НОВЫЕ входы фазы 4
    await main.trading_cycle(client, rm, sm, monitor, ledger)
    pos = monitor.get_position(FIGI)
    assert pos.state == PositionState.OPEN
    assert pos.quantity == 5, "поздние лоты не применены после OPEN"
    assert pos.entry_applied_lots == 5
    assert pos.entry_price == 100.0, "цена входа изменилась"
    assert pos.sl_price == 95.0 and pos.tp_price == 110.0, "SL/TP пересчитаны"
    assert len(active_positions(monitor)) == 1, "дубликат позиции"
    assert client.cancel_calls == [oid], "отмена остатка выполнена не один раз"
    rec = ledger.latest(oid)
    assert rec["status"] == "CANCELLED" and rec["executed_lots"] == 5
    # новых входов по занятому инструменту нет
    assert client.last_post is None, "новый BUY отправлен при уже открытой позиции"

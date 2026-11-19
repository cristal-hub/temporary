# tests/test_buy_registration_fixes.py
# Tsclp_v2.2_volatile — регрессионные тесты исправления потери первичной
# регистрации исполненных BUY (дефекты №1 и №2 аудита).
#
# Соответствие обязательным сценариям постановки (п.6):
#   1. Первичный BUY FILL (record_sent -> register_executed_buy создаёт
#      позицию):           test_s1_primary_buy_fill_registers_position
#                          test_s1b_primary_fill_without_candles_recovery
#   2. Повторный FILL (тот же cumulative отчёт не удваивает):
#                          test_s2_repeated_fill_no_duplicate
#   3. Рост cumulative N -> M (применяется только M-N):
#                          test_s3_cumulative_growth_applies_delta_only
#   4. Закрытая позиция (CLOSED + тот же entry_order_id не воскрешается,
#      в т.ч. после перезаписи CLOSED-записи и перезапуска):
#                          test_s4_closed_position_not_resurrected
#                          test_s4b_closed_archived_after_overwrite_no_resurrection
#   5. Новая позиция (поздний отчёт старой заявки A не изменяет новую
#      позицию B по тому же FIGI):
#                          test_s5_late_old_order_does_not_touch_new_position
#   6. Неизвестный trade_id (без подтверждённой принадлежности привязки
#      нет, исполнение не применяется):
#                          test_s6_unknown_trade_id_not_linked
#                          test_s6b_matching_trade_id_is_linked
#   7. Перезапуск (позиция восстанавливается с правильными FIGI,
#      количеством, ценой, entry_order_id):
#                          test_s7_restart_restores_position
#   8. Telegram (уведомление об открытии — после успешной регистрации,
#      а не после отправки заявки):
#                          test_s8_telegram_notify_after_registration
#                          test_s8b_no_notify_without_registration
#   9. Повторный запуск восстановления (повторная обработка учтённого
#      исполнения не создаёт дубликат; крах между record_sent и
#      регистрацией восстанавливается при перезапуске):
#                          test_s9_startup_recovery_idempotent
#                          test_s9b_crash_after_record_sent_restored_on_startup
#
# Дополнительный этап (целостность реестра и state.json):
#   A. Архив закрытых заявок БЕЗ лимита 200 (признаки старых закрытых
#      сделок не вытесняются, переживают перезапуск, самая старая заявка
#      по-прежнему блокирует «воскрешение»):
#                          test_stage_archive_closed_entry_order_ids_no_200_cap
#   B. Подтверждение регистрации в реестре ТОЛЬКО после сохранения
#      state.json (первичная регистрация и поздний рост):
#                          test_stage_no_registration_confirmation_when_state_save_failed
#                          test_stage_late_fill_confirmation_only_after_save
#   C. Несовпадение entry_order_id в активной RECOVERY — явный UNCERTAIN,
#      чужая заявка НЕ отмечается обработанной:
#                          test_stage_recovery_entry_order_mismatch_marks_uncertain
#                          test_stage_recovery_entry_order_mismatch_even_if_trade_id_matches
#                          test_stage_recovery_same_entry_order_id_still_applies_growth
#
# Тесты проверяют ПОСЛЕДСТВИЯ: итоговое состояние позиции (state, quantity,
# entry_applied_lots, entry_order_id) и сохранённое состояние
# (state.json: позиции + closed_entry_order_ids), а не только возврат
# функций.

import json

import pytest

import main
from execution import ExecutionMonitor, PositionState

from tests.test_p0_defect_fixes import (
    FakeClient, FakeRM, FakeSM,
    fake_state, make_ledger, small_df, env,  # env — общий fixture изоляции
)
from tests.test_p0_defect_fixes import (
    FILL, REJECTED, CANCELLED, NEW, PARTIALLYFILL, EXPIRED, FIGI, TICKER,
)

OTHER_FIGI = "BBG00OTHERF2"


class FakeRMState(FakeRM):
    """FakeRM с сериализацией риска (для перезапуска state.json в тестах)."""

    def import_state(self, state):
        return None


def open_kwargs(order_id, trade_id="t-1", executed=5, requested=5, price=100.0):
    return dict(
        figi=FIGI, ticker=TICKER, order_id=order_id, trade_id=trade_id,
        reason="unit", signal_time="now", price_before_order=price,
        order_send_time="now", requested_lots=requested, executed_lots=executed,
        exec_price=price, lot_size=1, partial=False,
        df=small_df(), source="test", ledger_status="FILL",
    )


def save_and_reload(monitor, rm):
    """Сохраняет state и загружает его в НОВЫЙ monitor (перезапуск)."""
    assert main._save_state_checked(monitor, rm)
    client = monitor.client
    m2 = ExecutionMonitor(client)
    assert main._load_state(m2, FakeRMState())
    return m2


# ============================================================
# Сценарий 1: первичный BUY FILL
# ============================================================

@pytest.mark.asyncio
async def test_s1_primary_buy_fill_registers_position(env):
    """Первичный BUY FILL: после record_sent(FILL, executed_lots=N) вызов
    register_executed_buy создаёт позицию (OPEN) с правильными количеством,
    ценой входа и entry_order_id; state.json сохранён."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM(sl=95.0, tp=110.0)
    oid = "oid-s1"
    ledger.record_intent(oid, trade_id="t-1", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=5)
    # брокер вернул FILL с 5 лотами -> trading_cycle пишет SENT/FILL
    ledger.record_sent(oid, trade_id="t-1", figi=FIGI, ticker=TICKER, side="BUY",
                       requested_lots=5, executed_lots=5, status_hint="FILL",
                       source="post_response")
    assert ledger.latest(oid)["status"] == "FILL"

    pos = await main.register_executed_buy(client, monitor, rm, ledger, **open_kwargs(oid))
    assert pos is not None, "первичная регистрация FILL заблокирована guard'ом"
    assert pos.state == PositionState.OPEN
    assert pos.quantity == 5
    assert pos.entry_price == 100.0
    assert pos.entry_order_id == oid
    assert pos.entry_applied_lots == 5
    assert pos.sl_price == 95.0 and pos.tp_price == 110.0
    assert rm.recorded == [FIGI]

    # состояние сохранено и восстанавливается
    m2 = save_and_reload(monitor, rm)
    p2 = m2.get_position(FIGI)
    assert p2 is not None and p2.state == PositionState.OPEN
    assert p2.quantity == 5 and p2.entry_order_id == oid and p2.entry_price == 100.0


@pytest.mark.asyncio
async def test_s1b_primary_fill_without_candles_recovery(env):
    """Первичный BUY FILL без свечей: позиция создаётся как RECOVERY
    (исполнение не теряется), количество и entry_order_id правильные."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM()
    oid = "oid-s1b"
    ledger.record_intent(oid, trade_id="t-1b", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=4)
    ledger.record_sent(oid, figi=FIGI, ticker=TICKER, side="BUY",
                       requested_lots=4, executed_lots=4, status_hint="FILL")
    kw = open_kwargs(oid, trade_id="t-1b", executed=4, requested=4)
    kw["df"] = None
    pos = await main.register_executed_buy(client, monitor, rm, ledger, **kw)
    assert pos is not None and pos.state == PositionState.RECOVERY
    assert pos.quantity == 4 and pos.entry_applied_lots == 4
    assert pos.entry_order_id == oid


# ============================================================
# Сценарий 2: повторный FILL
# ============================================================

@pytest.mark.asyncio
async def test_s2_repeated_fill_no_duplicate(env):
    """Повторный отчёт того же cumulative исполнения не создаёт дубликат и
    не удваивает количество (итоговое состояние идемпотентно)."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM()
    oid = "oid-s2"
    ledger.record_intent(oid, trade_id="t-2", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=5)
    ledger.record_sent(oid, figi=FIGI, side="BUY", requested_lots=5,
                       executed_lots=5, status_hint="FILL")
    kw = open_kwargs(oid, trade_id="t-2")
    pos1 = await main.register_executed_buy(client, monitor, rm, ledger, **kw)
    pos2 = await main.register_executed_buy(client, monitor, rm, ledger, **kw)
    assert pos1 is pos2, "создан второй объект позиции"
    assert len(monitor.positions) == 1
    assert monitor.get_position(FIGI).quantity == 5
    assert monitor.get_position(FIGI).entry_applied_lots == 5
    assert monitor.get_position(FIGI).state == PositionState.OPEN


# ============================================================
# Сценарий 3: рост cumulative
# ============================================================

@pytest.mark.asyncio
async def test_s3_cumulative_growth_applies_delta_only(env):
    """Рост cumulative executed_lots с N до M применяет только разницу M-N
    (повторный отчёт того же M ничего не добавляет)."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM()
    oid = "oid-s3"
    ledger.record_intent(oid, trade_id="t-3", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=10)
    pos = await main.register_executed_buy(
        client, monitor, rm, ledger, **open_kwargs(oid, trade_id="t-3",
                                                   executed=5, requested=10))
    assert pos.quantity == 5 and pos.entry_applied_lots == 5

    # рост до 8: поздний отчёт после терминального статуса
    client.order_states[oid] = fake_state(CANCELLED, lots_executed=8, price_units=800)
    await main.poll_open_orders(client, monitor, FakeRM(), ledger)
    assert monitor.get_position(FIGI).quantity == 8, "дельта 8-5 не применена"
    assert monitor.get_position(FIGI).entry_applied_lots == 8

    # повторный отчёт того же cumulative: ничего не добавляется
    await main.poll_open_orders(client, monitor, FakeRM(), ledger)
    assert monitor.get_position(FIGI).quantity == 8, "повторный отчёт удвоил дельту"


# ============================================================
# Сценарий 4: закрытая позиция не воскрешается
# ============================================================

@pytest.mark.asyncio
async def test_s4_closed_position_not_resurrected(env):
    """CLOSED-позиция с тем же entry_order_id: повторный отчёт исполнения
    не воскрешает позицию (возврат None, состояние CLOSED, количество
    сохранено); поздний рост — UNCERTAIN, не новая позиция."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM()
    oid = "oid-s4"
    pos = await main.register_executed_buy(client, monitor, rm, ledger,
                                           **open_kwargs(oid, trade_id="t-4"))
    pos.state = PositionState.CLOSED
    pos.quantity = 0

    # тот же отчёт брокера (5 == учтённым 5)
    res = await main.register_executed_buy(client, monitor, rm, ledger,
                                           **open_kwargs(oid, trade_id="t-4"))
    assert res is None, "закрытая позиция воскрешена повторной регистрацией"
    assert monitor.get_position(FIGI).state == PositionState.CLOSED
    assert monitor.get_position(FIGI).quantity == 0
    assert monitor.get_position(FIGI).entry_order_id == oid

    # поздний рост (9 > 5) при закрытой позиции: UNCERTAIN, не регистрация
    client.order_states[oid] = fake_state(CANCELLED, lots_executed=9, price_units=900)
    client.uncertain_state = False
    res2 = await main.register_executed_buy(client, monitor, rm, ledger,
                                            **open_kwargs(oid, trade_id="t-4",
                                                          executed=9, requested=10))
    assert res2 is None
    assert client.uncertain_state is True, "поздний рост при закрытой сделке не обнаружен"
    assert monitor.get_position(FIGI).state == PositionState.CLOSED


@pytest.mark.asyncio
async def test_s4b_closed_archived_after_overwrite_no_resurrection(env):
    """CLOSED-запись перезаписана новой позицией по тому же FIGI: признак
    закрытой сделки сохраняется (closed_entry_order_ids), переживает
    перезапуск, и поздний residual-отчёт старой заявки при перезапуске не
    создаёт фантомную позицию."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM()
    oid_old = "oid-s4-old"
    # сделка 1: куплено 5, применено, закрыто
    pos_old = await main.register_executed_buy(client, monitor, rm, ledger,
                                               **open_kwargs(oid_old, trade_id="t-4o"))
    assert pos_old.state == PositionState.OPEN and pos_old.quantity == 5
    pos_old.state = PositionState.CLOSED
    pos_old.quantity = 0
    main._save_state_checked(monitor, rm)

    # сделка 2: новая покупка перезаписывает CLOSED-запись
    oid_new = "oid-s4-new"
    client2 = FakeClient(positions={})
    monitor2 = ExecutionMonitor(client2)
    rm2 = FakeRM()
    ledger2 = make_ledger(env.tmp_path / "ledger2.jsonl")
    pos_new = await main.register_executed_buy(client2, monitor2, rm2, ledger2,
                                               **open_kwargs(oid_new, trade_id="t-4n",
                                                             executed=2, requested=2))
    assert pos_new.state == PositionState.OPEN and pos_new.quantity == 2
    main._save_state_checked(monitor2, rm2)
    assert monitor2.closed_entry_order_ids == []  # пока ни одна сделка не закрыта

    # перезапуск: state.json (от monitor с закрытой сделкой 1) + реестр
    m3 = save_and_reload(monitor, rm)
    assert m3.closed_entry_order_ids == [oid_old], "признак закрытой сделки потерян"

    # новая покупка в ПЕРЕЗАПУЩЕННОМ monitor перезаписывает CLOSED-запись:
    # признак закрытой сделки архивируется и не теряется
    pos_new2 = await main.register_executed_buy(
        FakeClient(positions={}), m3, FakeRMState(), ledger2,
        **open_kwargs(oid_new, trade_id="t-4n", executed=2, requested=2))
    assert pos_new2.state == PositionState.OPEN and pos_new2.quantity == 2
    assert oid_old in m3.closed_entry_order_ids, \
        "перезапись CLOSED-записи новой позицией потеряла признак закрытой сделки"
    main._save_state_checked(m3, FakeRMState())

    # вторая сделка тоже закрывается (продана), перезапуск: активных позиций
    # нет, но признаки обеих закрытых сделок сохранены
    m3.get_position(FIGI).state = PositionState.CLOSED
    m3.get_position(FIGI).quantity = 0
    main._save_state_checked(m3, FakeRMState())
    m5 = save_and_reload(m3, FakeRMState())
    assert set(m5.closed_entry_order_ids) == {oid_old, oid_new}

    # поздний residual-отчёт СТАРОЙ заявки при отсутствии активных позиций:
    # фантомная позиция не создаётся (признак закрытой сделки в реестре)
    client3 = FakeClient(positions={})
    m5.client = client3
    kw = open_kwargs(oid_old, trade_id="t-4o")
    kw["df"] = None
    res = await main.register_executed_buy(client3, m5, FakeRMState(), ledger, **kw)
    assert res is None, "поздний отчёт закрытой сделки создал новую позицию"
    p_after = m5.get_position(FIGI)
    assert p_after is not None and p_after.state == PositionState.CLOSED
    assert p_after.entry_order_id == oid_new, "фантомная позиция по старой заявке создана"
    assert client3.uncertain_state is False
    # повторный отчёт с тем же значением — детерминированный блок
    res2 = await main.register_executed_buy(client3, m5, FakeRMState(), ledger, **kw)
    assert res2 is None


# ============================================================
# Сценарий 5: поздний отчёт старой заявки не изменяет новую позицию
# ============================================================

@pytest.mark.asyncio
async def test_s5_late_old_order_does_not_touch_new_position(env):
    """Поздний отчёт старой заявки A не изменяет новую позицию B по тому же
    FIGI (ни OPEN, ни RECOVERY)."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM()
    oid_b = "oid-s5-B"
    pos_b = await main.register_executed_buy(client, monitor, rm, ledger,
                                             **open_kwargs(oid_b, trade_id="t-5B",
                                                           executed=3, requested=3))
    assert pos_b.state == PositionState.OPEN and pos_b.quantity == 3

    # поздний отчёт старой заявки A (другой order_id, другой trade_id)
    oid_a = "oid-s5-A"
    ledger.record_intent(oid_a, trade_id="t-5A", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=5)
    kw_a = open_kwargs(oid_a, trade_id="t-5A")
    kw_a["df"] = None
    res = await main.register_executed_buy(client, monitor, rm, ledger, **kw_a)
    assert res is pos_b, "создана вторая позиция по тому же FIGI"
    assert monitor.get_position(FIGI).quantity == 3, "чужое исполнение применено к позиции B"
    assert monitor.get_position(FIGI).entry_order_id == oid_b, "позиция B перепривязана к заявке A"
    assert monitor.get_position(FIGI).state == PositionState.OPEN
    assert len(monitor.positions) == 1

    # то же для RECOVERY-позиции B с заданным entry_order_id
    pos_b.state = PositionState.RECOVERY
    client.uncertain_state = False
    res2 = await main.register_executed_buy(client, monitor, rm, ledger, **kw_a)
    assert res2 is pos_b
    assert monitor.get_position(FIGI).quantity == 3, "исполнение A применено к RECOVERY B"
    assert monitor.get_position(FIGI).entry_order_id == oid_b


# ============================================================
# Сценарий 6: неизвестный trade_id — привязки нет
# ============================================================

@pytest.mark.asyncio
async def test_s6_unknown_trade_id_not_linked(env):
    """RECOVERY-позиция без trade_id (усыновлённая адопцией) и заявка с известным
    trade_id: привязки нет, исполнение к позиции не применяется,
    фиксируется неопределённость (client.uncertain_state + UNCERTAIN в
    реестре)."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM()
    # усыновлённая позиция: trade_id пуст, entry_order_id пуст
    pos = monitor.register_recovery(figi=FIGI, ticker=TICKER, direction="BUY",
                                    quantity=3, lot_size=1, recovery_source="adopted")
    oid_a = "oid-s6-A"
    ledger.record_intent(oid_a, trade_id="t-6A", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=5)
    client.uncertain_state = False
    kw = open_kwargs(oid_a, trade_id="t-6A")
    kw["df"] = None
    res = await main.register_executed_buy(client, monitor, rm, ledger, **kw)
    assert res is pos
    assert pos.entry_order_id is None, "привязка без подтверждённой принадлежности"
    assert pos.quantity == 3, "исполнение чужой заявки применено к позиции"
    assert pos.entry_applied_lots == 3
    assert client.uncertain_state is True, "неопределённость не зафиксирована"
    rec = ledger.latest(oid_a)
    assert rec["status"] == "UNCERTAIN", "принадлежность не подтверждена, а заявка закрыта как согласованная"

    # RECOVERY с известным, но другим trade_id — то же поведение
    pos2 = monitor.register_recovery(figi=OTHER_FIGI, ticker="OTHER", direction="BUY",
                                     quantity=2, lot_size=1, trade_id="t-6P",
                                     recovery_source="adopted")
    oid_c = "oid-s6-C"
    ledger.record_intent(oid_c, trade_id="t-6C", figi=OTHER_FIGI, ticker="OTHER",
                         side="BUY", requested_lots=4)
    kw_c = open_kwargs(oid_c, trade_id="t-6C")
    kw_c["df"] = None
    kw_c["figi"], kw_c["ticker"] = OTHER_FIGI, "OTHER"
    res2 = await main.register_executed_buy(client, monitor, rm, ledger, **kw_c)
    assert res2 is pos2
    assert pos2.entry_order_id is None
    assert pos2.quantity == 2
    assert client.uncertain_state is True


@pytest.mark.asyncio
async def test_s6b_matching_trade_id_is_linked(env):
    """Позитивный путь: RECOVERY-позиция с тем же trade_id привязывается к
    заявке (путь промоции открыт), исполнение применяется штатно."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM()
    pos = monitor.register_recovery(figi=FIGI, ticker=TICKER, direction="BUY",
                                    quantity=3, lot_size=1, trade_id="t-6ok",
                                    recovery_source="adopted")
    oid = "oid-s6ok"
    ledger.record_intent(oid, trade_id="t-6ok", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=5)
    client.uncertain_state = False
    kw = open_kwargs(oid, trade_id="t-6ok")
    kw["df"] = None
    res = await main.register_executed_buy(client, monitor, rm, ledger, **kw)
    assert res is pos
    assert pos.entry_order_id == oid, "привязка при совпадающем trade_id не выполнена"
    assert pos.quantity == 5 and pos.entry_applied_lots == 5, "штатное применение сломано"
    assert client.uncertain_state is False


# ============================================================
# Сценарий 7: перезапуск
# ============================================================

@pytest.mark.asyncio
async def test_s7_restart_restores_position(env):
    """После успешной регистрации BUY и перезапуска позиция восстанавливается
    с правильными FIGI, количеством, ценой и entry_order_id."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM()
    oid = "oid-s7"
    ledger.record_intent(oid, trade_id="t-7", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=6)
    ledger.record_sent(oid, figi=FIGI, side="BUY", requested_lots=6,
                       executed_lots=6, status_hint="FILL")
    pos = await main.register_executed_buy(
        client, monitor, rm, ledger,
        **open_kwargs(oid, trade_id="t-7", executed=6, requested=6, price=50.0))
    assert pos.state == PositionState.OPEN and pos.quantity == 6

    m2 = save_and_reload(monitor, rm)
    p2 = m2.get_position(FIGI)
    assert p2 is not None
    assert p2.figi == FIGI and p2.ticker == TICKER
    assert p2.direction == "BUY"
    assert p2.quantity == 6
    assert p2.entry_price == 50.0
    assert p2.entry_order_id == oid
    assert p2.entry_applied_lots == 6
    assert p2.state == PositionState.OPEN

    # стартовое восстановление не дублирует и не портит позицию
    client2 = FakeClient(positions={})
    client2.orders_list = []
    m2.client = client2
    await main.startup_recovery(client2, m2, rm, ledger)
    assert m2.get_position(FIGI).quantity == 6
    assert m2.get_position(FIGI).entry_order_id == oid
    assert len([p for p in m2.positions.values()
                if p.state in (PositionState.OPEN, PositionState.RECOVERY)]) == 1


# ============================================================
# Сценарий 8: Telegram-уведомление
# ============================================================

@pytest.mark.asyncio
async def test_s8_telegram_notify_after_registration(env, monkeypatch):
    """Уведомление об открытии отправляется ПОСЛЕ успешной регистрации
    исполненной покупки: в момент отправки позиция уже существует в учёте,
    текст соответствует зарегистрированной покупке."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM()
    oid = "oid-s8"
    ledger.record_intent(oid, trade_id="t-8", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=5)
    ledger.record_sent(oid, figi=FIGI, side="BUY", requested_lots=5,
                       executed_lots=5, status_hint="FILL")

    sent_texts = []

    async def spy_notify(text):
        # позиция должна быть зарегистрирована ДО отправки уведомления
        assert monitor.get_position(FIGI) is not None, \
            "уведомление отправлено до регистрации позиции"
        assert monitor.get_position(FIGI).quantity == 5
        sent_texts.append(text)

    monkeypatch.setattr(main, "notify", spy_notify)
    await main.register_executed_buy(client, monitor, rm, ledger, **open_kwargs(oid, trade_id="t-8"))

    assert len(sent_texts) == 1, f"ожидалось 1 уведомление об открытии, отправлено {len(sent_texts)}"
    assert "BUY" in sent_texts[0] and "5" in sent_texts[0]
    assert monitor.get_position(FIGI).state == PositionState.OPEN


@pytest.mark.asyncio
async def test_s8b_no_notify_without_registration(env, monkeypatch):
    """Отправка BUY-заявки (record_sent) сама по себе уведомление об открытии
    не создаёт: регистрация и уведомление — единый путь."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM()
    oid = "oid-s8b"
    ledger.record_intent(oid, trade_id="t-8b", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=5)
    ledger.record_sent(oid, figi=FIGI, side="BUY", requested_lots=5,
                       executed_lots=5, status_hint="FILL")

    sent = []

    async def spy_notify(text):
        sent.append(text)

    monkeypatch.setattr(main, "notify", spy_notify)
    # пока register_executed_buy не вызван — позиций и уведомлений нет
    assert monitor.get_position(FIGI) is None
    assert sent == []

    await main.register_executed_buy(client, monitor, rm, ledger, **open_kwargs(oid, trade_id="t-8b"))
    assert monitor.get_position(FIGI) is not None
    assert len(sent) == 1  # ровно одно уведомление об открытии


# ============================================================
# Сценарий 9: повторный запуск восстановления
# ============================================================

@pytest.mark.asyncio
async def test_s9_startup_recovery_idempotent(env):
    """Повторная обработка уже учтённого исполнения при рестарте не создаёт
    дубликат и не меняет количество позиции."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM()
    oid = "oid-s9"
    ledger.record_intent(oid, trade_id="t-9", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=5)
    ledger.record_sent(oid, figi=FIGI, side="BUY", requested_lots=5,
                       executed_lots=5, status_hint="FILL")
    pos = await main.register_executed_buy(client, monitor, rm, ledger,
                                           **open_kwargs(oid, trade_id="t-9"))
    assert pos.state == PositionState.OPEN and pos.quantity == 5

    client2 = FakeClient(positions={})
    client2.order_states[oid] = fake_state(FILL, lots_executed=5, price_units=500)
    monitor2 = ExecutionMonitor(client2)
    main._save_state_checked(monitor, rm)
    main._load_state(monitor2, rm)

    # первый рестарт
    await main.startup_recovery(client2, monitor2, rm, ledger)
    p = monitor2.get_position(FIGI)
    assert p is not None and p.quantity == 5
    assert p.entry_order_id == oid and p.entry_applied_lots == 5
    # второй рестарт (повторная сверка)
    await main.startup_recovery(client2, monitor2, rm, ledger)
    p2 = monitor2.get_position(FIGI)
    assert p2 is p, "повторный рестарт создал новый объект позиции"
    assert p2.quantity == 5 and p2.entry_applied_lots == 5
    active = [x for x in monitor2.positions.values()
              if x.state in (PositionState.OPEN, PositionState.RECOVERY)]
    assert len(active) == 1, "повторное восстановление создало дубликат"


@pytest.mark.asyncio
async def test_s9b_crash_after_record_sent_restored_on_startup(env):
    """Крах между record_sent(FILL) и регистрацией: при перезапуске
    startup_recovery доводит заявку до регистрации через единую точку
    (позиция с правильными FIGI/количеством/ценой/entry_order_id), а
    повторный рестарт не создаёт дубликат."""
    ledger = make_ledger(env.tmp_path)
    oid = "oid-s9b"
    ledger.record_intent(oid, trade_id="t-9b", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=5)
    # робот записал FILL и упал до регистрации: reason пуст (нет признака
    # применения), позиций в state нет
    ledger.record_sent(oid, figi=FIGI, ticker=TICKER, side="BUY",
                       requested_lots=5, executed_lots=5, status_hint="FILL",
                       source="post_response")

    client = FakeClient(positions={})
    client.order_states[oid] = fake_state(FILL, lots_executed=5, price_units=500)
    monitor = ExecutionMonitor(client)
    rm = FakeRM()

    await main.startup_recovery(client, monitor, rm, ledger)
    p = monitor.get_position(FIGI)
    assert p is not None, "исполненная заявка не восстановлена при перезапуске"
    assert p.quantity == 5 and p.entry_applied_lots == 5
    assert p.entry_order_id == oid
    assert p.execution_price == 100.0  # 500 / (5 лотов * 1) — цена исполнения сохранена
    # без свечей (startup_recovery не грузит их) позиция RECOVERY —
    # entry_price появится при штатной промоции promote_recovery_positions
    assert p.state == PositionState.RECOVERY

    # перезапуск: позиция сохранена, повторная сверка не дублирует
    m2 = save_and_reload(monitor, rm)
    client2 = FakeClient(positions={})
    client2.order_states[oid] = fake_state(FILL, lots_executed=5, price_units=500)
    m2.client = client2
    await main.startup_recovery(client2, m2, rm, ledger)
    p2 = m2.get_position(FIGI)
    assert p2 is not None and p2.quantity == 5
    assert p2.entry_order_id == oid
    active = [x for x in m2.positions.values()
              if x.state in (PositionState.OPEN, PositionState.RECOVERY)]
    assert len(active) == 1, "повторный рестарт создал дубликат позиции"


# ============================================================
# Дополнительные проверки сохранённого состояния
# ============================================================

def test_state_roundtrip_closed_entry_order_ids(env):
    """closed_entry_order_ids сериализуется в state.json и восстанавливается;
    старый state.json без ключа совместим (пустой список)."""
    client = FakeClient()
    monitor = ExecutionMonitor(client)
    monitor.register(figi=FIGI, ticker=TICKER, direction="BUY",
                     entry_price=100.0, quantity=5, sl_price=95.0, tp_price=110.0,
                     lot_size=1, trade_id="t-x", entry_order_id="oid-old",
                     entry_applied_lots=5)
    monitor.get_position(FIGI).state = PositionState.CLOSED
    monitor.register(figi=FIGI, ticker=TICKER, direction="BUY",
                     entry_price=110.0, quantity=2, sl_price=105.0, tp_price=120.0,
                     lot_size=1, trade_id="t-y", entry_order_id="oid-new")
    main._save_state_checked(monitor, FakeRM())
    raw = json.loads(open(main.STATE_FILE, "r", encoding="utf-8").read())
    assert raw["closed_entry_order_ids"] == ["oid-old"]

    rm = FakeRMState()
    m2 = ExecutionMonitor(client)
    assert main._load_state(m2, rm)
    assert m2.closed_entry_order_ids == ["oid-old"]

    # старый state.json (без ключа) — обратная совместимость
    legacy = dict(raw)
    legacy.pop("closed_entry_order_ids")
    with open(main.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(legacy, f, ensure_ascii=False)
    m3 = ExecutionMonitor(client)
    assert main._load_state(m3, rm)
    assert m3.closed_entry_order_ids == []
    assert m3.get_position(FIGI).entry_order_id == "oid-new"


# ============================================================
# Дополнительный этап A: архив закрытых заявок БЕЗ лимита 200
# ============================================================

@pytest.mark.asyncio
async def test_stage_archive_closed_entry_order_ids_no_200_cap(env):
    """Архив closed_entry_order_ids НЕ обрезается лимитом 200: после 250
    перезаписанных CLOSED-записей сохранены ВСЕ признаки, включая самые
    старые; архив целиком переживает перезапуск (state.json roundtrip), и
    самая старая закрытая заявка по-прежнему блокирует «воскрешение»."""
    client = FakeClient()
    monitor = ExecutionMonitor(client)
    n = 250
    for i in range(n):
        monitor.register(figi=FIGI, ticker=TICKER, direction="BUY",
                         entry_price=100.0 + i, quantity=1, sl_price=95.0,
                         tp_price=110.0, lot_size=1, trade_id=f"t-arch-{i}",
                         entry_order_id=f"oid-arch-{i}", entry_applied_lots=1)
        monitor.get_position(FIGI).state = PositionState.CLOSED
    assert len(monitor.closed_entry_order_ids) == n - 1
    # следующая регистрация архивирует последнюю CLOSED-запись
    monitor.register(figi=FIGI, ticker=TICKER, direction="BUY",
                     entry_price=999.0, quantity=1, sl_price=95.0, tp_price=110.0,
                     lot_size=1, trade_id="t-arch-final", entry_order_id="oid-arch-final")
    assert len(monitor.closed_entry_order_ids) == n, "архив обрезан лимитом"
    assert monitor.closed_entry_order_ids[0] == "oid-arch-0", \
        "самая старая запись вытеснена из архива"
    assert f"oid-arch-{n - 1}" in monitor.closed_entry_order_ids

    # архив целиком переносится через state.json (экспорт/импорт без обрезки)
    assert main._save_state_checked(monitor, FakeRM())
    m2 = ExecutionMonitor(client)
    assert main._load_state(m2, FakeRMState())
    assert m2.closed_entry_order_ids == monitor.closed_entry_order_ids, \
        "архив потерян/обрезан при перезапуске"

    # самая старая закрытая заявка блокирует «воскрешение» после 250 закрытий
    monitor.get_position(FIGI).state = PositionState.CLOSED
    monitor.get_position(FIGI).quantity = 0
    main._save_state_checked(monitor, FakeRM())
    m3 = save_and_reload(monitor, FakeRMState())
    ledger = make_ledger(env.tmp_path / "ledger_arch.jsonl")
    oid_old = "oid-arch-0"
    ledger.record_intent(oid_old, trade_id="t-arch-0", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=1)
    ledger.record_sent(oid_old, figi=FIGI, side="BUY", requested_lots=1,
                       executed_lots=1, status_hint="FILL")
    client3 = FakeClient(positions={})
    m3.client = client3
    kw = open_kwargs(oid_old, trade_id="t-arch-0", executed=1, requested=1)
    kw["df"] = None
    res = await main.register_executed_buy(client3, m3, FakeRMState(), ledger, **kw)
    assert res is None, "поздний отчёт самой старой закрытой заявки создал позицию"
    p_after = m3.get_position(FIGI)
    assert p_after is not None and p_after.state == PositionState.CLOSED
    assert p_after.entry_order_id == "oid-arch-final", "CLOSED-запись перезаписана фантомом"


# ============================================================
# Дополнительный этап B: подтверждение регистрации только после
# успешного сохранения state.json
# ============================================================

@pytest.mark.asyncio
async def test_stage_no_registration_confirmation_when_state_save_failed(env, monkeypatch):
    """state.json НЕ сохранён: подтверждение «позиция зарегистрирована» в
    реестр НЕ пишется (иначе startup_recovery при перезапуске пропустил бы
    заявку по reason-маркеру, и позиция была бы потеряна). Позиция в памяти
    не теряется; после восстановления сохранения перезапуск восстанавливает
    её, повторная регистрация идемпотентна."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM()
    oid = "oid-st2"
    ledger.record_intent(oid, trade_id="t-st2", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=5)
    ledger.record_sent(oid, figi=FIGI, side="BUY", requested_lots=5,
                       executed_lots=5, status_hint="FILL")

    real_save_state = main._save_state
    monkeypatch.setattr(main, "_save_state", lambda m, r: False)

    pos = await main.register_executed_buy(client, monitor, rm, ledger,
                                           **open_kwargs(oid, trade_id="t-st2"))
    # позиция создана в памяти (исполнение не потеряно), но НЕ сохранена
    assert pos is not None and pos.state == PositionState.OPEN and pos.quantity == 5
    assert main.state_save_failed is True
    # подтверждение регистрации НЕ записано: latest — FILL от record_sent,
    # ни в одной записи журнала нет маркера «позиция зарегистрирована»
    rec = ledger.latest(oid)
    assert rec["status"] == "FILL"
    assert "позиция зарегистрирована" not in str(rec.get("reason") or "")
    with open(ledger.path, "r", encoding="utf-8") as f:
        all_recs = [json.loads(x) for x in f if x.strip()]
    assert not any("позиция зарегистрирована" in str(r.get("reason") or "")
                   for r in all_recs), "подтверждение записано при несохранённом state.json"

    # сохранение восстановлено: перезапуск восстанавливает позицию,
    # повторное восстановление не дублирует и не портит её
    monkeypatch.setattr(main, "_save_state", real_save_state)
    m2 = save_and_reload(monitor, rm)
    p2 = m2.get_position(FIGI)
    assert p2 is not None and p2.quantity == 5 and p2.entry_order_id == oid

    client2 = FakeClient(positions={})
    client2.order_states[oid] = fake_state(FILL, lots_executed=5, price_units=500)
    m2.client = client2
    await main.startup_recovery(client2, m2, rm, ledger)
    p3 = m2.get_position(FIGI)
    assert p3 is not None and p3.quantity == 5 and p3.entry_order_id == oid
    assert len([p for p in m2.positions.values()
                if p.state in (PositionState.OPEN, PositionState.RECOVERY)]) == 1, \
        "повторное восстановление создало дубликат"


@pytest.mark.asyncio
async def test_stage_late_fill_confirmation_only_after_save(env, monkeypatch):
    """Поздний рост применён к позиции в памяти, но state.json НЕ сохранён:
    подтверждение «исполнение применено» НЕ пишется — вместо него
    UNCERTAIN-запись (заявка остаётся доступной опросу). После перезапуска
    state.json содержит последнюю УСПЕШНУЮ запись (5 лот.), и poll доводит
    позицию до 8 лотов — дельта не потеряна."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM()
    oid = "oid-stL"
    ledger.record_intent(oid, trade_id="t-stL", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=10)
    pos = await main.register_executed_buy(
        client, monitor, rm, ledger,
        **open_kwargs(oid, trade_id="t-stL", executed=5, requested=10))
    assert pos.state == PositionState.OPEN and pos.quantity == 5
    rec = ledger.latest(oid)
    assert rec["status"] == "FILL" and "позиция зарегистрирована" in str(rec.get("reason"))

    real_save_state = main._save_state
    monkeypatch.setattr(main, "_save_state", lambda m, r: False)
    client.uncertain_state = False
    res = await main.register_executed_buy(
        client, monitor, rm, ledger,
        **open_kwargs(oid, trade_id="t-stL", executed=8, requested=10))
    # дельта применена в памяти, но не подтверждена реестром
    assert res is pos
    assert pos.quantity == 8 and pos.entry_applied_lots == 8
    assert client.uncertain_state is True
    rec2 = ledger.latest(oid)
    assert rec2["status"] == "UNCERTAIN", \
        "подтверждение применения записано при несохранённом state.json"
    assert "state.json НЕ сохранён" in str(rec2.get("reason") or "")
    with open(ledger.path, "r", encoding="utf-8") as f:
        all_recs = [json.loads(x) for x in f if x.strip()]
    assert not any("позднее исполнение применено" in str(r.get("reason") or "")
                   for r in all_recs), "подтверждение применения записано при несохранённом state.json"

    # перезапуск: state.json — последняя УСПЕШНАЯ запись (5 лот.)
    m2 = ExecutionMonitor(client)
    assert main._load_state(m2, FakeRMState())
    p2 = m2.get_position(FIGI)
    assert p2.quantity == 5, "несохранённая дельта попала в state.json"

    # UNCERTAIN-заявка доступна опросу: poll доводит позицию до 8
    client2 = FakeClient(positions={})
    client2.order_states[oid] = fake_state(CANCELLED, lots_executed=8, price_units=800)
    m2.client = client2
    monkeypatch.setattr(main, "_save_state", real_save_state)
    await main.poll_open_orders(client2, m2, FakeRM(), ledger)
    p3 = m2.get_position(FIGI)
    assert p3.quantity == 8 and p3.entry_applied_lots == 8, \
        "дельта не восстановлена после перезапуска"
    main._save_state_checked(m2, FakeRMState())
    raw = json.loads(open(main.STATE_FILE, "r", encoding="utf-8").read())
    assert raw["positions"][FIGI]["quantity"] == 8, "восстановленная дельта не сохранена"


# ============================================================
# Дополнительный этап C: несовпадение entry_order_id в активной
# RECOVERY-позиции — явный UNCERTAIN, чужая заявка не «обработана»
# ============================================================

@pytest.mark.asyncio
async def test_stage_recovery_entry_order_mismatch_marks_uncertain(env):
    """Активная RECOVERY-позиция УЖЕ связана с заявкой B: отчёт чужой
    заявки A (тот же FIGI, другой order_id/trade_id) НЕ отмечается
    обработанным и НЕ применяется к позиции — явно фиксируется
    UNCERTAIN (client.uncertain_state + запись реестра)."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM()
    pos = monitor.register_recovery(figi=FIGI, ticker=TICKER, direction="BUY",
                                    quantity=5, lot_size=1, trade_id="t-stM",
                                    order_id="oid-stM-B", recovery_source="no_price")
    assert pos.entry_order_id == "oid-stM-B" and pos.entry_applied_lots == 5

    ledger.record_intent("oid-stM-A", trade_id="t-otherM", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=7)
    client.uncertain_state = False
    kw = open_kwargs("oid-stM-A", trade_id="t-otherM", executed=7, requested=7)
    kw["df"] = None
    res = await main.register_executed_buy(client, monitor, rm, ledger, **kw)
    assert res is pos
    # позиция не изменена и не перепривязана
    assert pos.quantity == 5 and pos.entry_applied_lots == 5
    assert pos.entry_order_id == "oid-stM-B"
    assert pos.trade_id == "t-stM"
    # чужая заявка явно помечена UNCERTAIN, а не «обработанной»
    assert client.uncertain_state is True
    rec_a = ledger.latest("oid-stM-A")
    assert rec_a["status"] == "UNCERTAIN", "чужая заявка помечена обработанной"
    assert "связана с другой входной заявкой" in str(rec_a.get("reason") or "")
    with open(ledger.path, "r", encoding="utf-8") as f:
        all_recs = [json.loads(x) for x in f if x.strip()]
    assert not any(r.get("order_id") == "oid-stM-A"
                   and r.get("status") in ("FILL", "CANCELLED", "REJECTED", "EXPIRED")
                   for r in all_recs), "чужая заявка закрыта терминальным статусом"
    # сохранённое состояние: позиция неизменна
    assert main._save_state_checked(monitor, FakeRM())
    raw = json.loads(open(main.STATE_FILE, "r", encoding="utf-8").read())
    p = raw["positions"][FIGI]
    assert p["quantity"] == 5 and p["entry_order_id"] == "oid-stM-B"
    assert p["state"] == "RECOVERY"


@pytest.mark.asyncio
async def test_stage_recovery_entry_order_mismatch_even_if_trade_id_matches(env):
    """RECOVERY-позиция уже связана с заявкой B; отчёт заявки A с ТЕМ ЖЕ
    trade_id, что у позиции, но другим order_id: перепривязки нет,
    исполнение не применяется, фиксируется UNCERTAIN (несовпадение
    entry_order_id — доказательства принадлежности заявки A недостаточно)."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM()
    pos = monitor.register_recovery(figi=FIGI, ticker=TICKER, direction="BUY",
                                    quantity=5, lot_size=1, trade_id="t-stN",
                                    order_id="oid-stN-B", recovery_source="no_price")
    ledger.record_intent("oid-stN-A", trade_id="t-stN", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=7)
    client.uncertain_state = False
    kw = open_kwargs("oid-stN-A", trade_id="t-stN", executed=7, requested=7)
    kw["df"] = None
    res = await main.register_executed_buy(client, monitor, rm, ledger, **kw)
    assert res is pos
    assert client.uncertain_state is True
    assert pos.entry_order_id == "oid-stN-B", "позиция перепривязана к чужой заявке"
    assert pos.quantity == 5 and pos.entry_applied_lots == 5
    assert ledger.latest("oid-stN-A")["status"] == "UNCERTAIN"


@pytest.mark.asyncio
async def test_stage_recovery_same_entry_order_id_still_applies_growth(env):
    """Контроль от перегиба: отчёт СВОЕЙ заявки (entry_order_id совпадает)
    продолжает штатно применять рост cumulative — новая проверка не
    блокирует легитимный путь и не фиксирует ложную неопределённость."""
    ledger = make_ledger(env.tmp_path)
    client = FakeClient(positions={})
    monitor = ExecutionMonitor(client)
    rm = FakeRM()
    pos = monitor.register_recovery(figi=FIGI, ticker=TICKER, direction="BUY",
                                    quantity=5, lot_size=1, trade_id="t-stG",
                                    order_id="oid-stG", recovery_source="no_price")
    ledger.record_intent("oid-stG", trade_id="t-stG", figi=FIGI, ticker=TICKER,
                         side="BUY", requested_lots=10)
    client.uncertain_state = False
    kw = open_kwargs("oid-stG", trade_id="t-stG", executed=8, requested=10)
    kw["df"] = None
    res = await main.register_executed_buy(client, monitor, rm, ledger, **kw)
    assert res is pos
    assert pos.quantity == 8 and pos.entry_applied_lots == 8, \
        "рост своей заявки заблокирован новой проверкой"
    assert client.uncertain_state is False, "ложная неопределённость для своей заявки"

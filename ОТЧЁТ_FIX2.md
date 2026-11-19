# ОТЧЁТ — ЭТАП BUY REGISTRATION FIX 2

**Репозиторий:** `cristal-hub/temporary` (origin: `cristal-hub/cristal-hub-Tsclp_v2.1_volatile_Samsung`), ветка `main`.
**Базовый коммит:** `afa18f0` (`Add files via upload`). Рабочее дерево включало изменения предыдущего этапа (main.py, execution.py, `tests/test_buy_registration_fixes.py`) — они не откатывались и не перезаписывались.
**Дата:** 2026-11-19.

---

## 0. Режим работы — подтверждение ограничений

- Торговый робот НЕ запускался (выполнялись только pytest-прогоны; точка входа `main()` не вызывалась).
- Реальные и sandbox-заявки НЕ отправлялись. К торговым методам T-Invest API обращений НЕТ: SDK `t_tech` в окружении отсутствует, тесты выполняются на заглушке `t_tech` (`scripts/stubs/`) и `FakeClient` — сетевых вызовов нет в принципе.
- Commit/push НЕ выполнялись: `git log` остался на `afa18f0`, `git status -sb` — без новых коммитов, `git stash list` пуст.
- Стратегия, сигналы, фильтры, риск-менеджмент, исполнение заявок (execution.py в этом этапе НЕ менялся), `close_all_positions.py` — без изменений (см. §3).
- `research_events.jsonl` — только штатные append-записи, см. §4.
- Все результаты тестов ниже — реальные прогоны pytest (python 3.12.16, pytest 9.0.2, pytest-asyncio 1.3.0). Статический анализ не выдавался за тестирование.

---

## 1. Дефект P1 — OPEN order_id mismatch

### 1.1. Как выглядел дефект (подтверждён на актуальном коде до правки)

В `main.py`, функция `register_executed_buy()`, ветка активной OPEN-позиции (`elif existing.state == PositionState.OPEN:`, до правки строки 1401–1404) любое исполнение по FIGI позиции передавалось в `monitor.apply_late_entry_lots(existing, executed_lots, order_id)` без проверки принадлежности на уровне main.py. `apply_late_entry_lots()` (execution.py) при несовпадении `order_id != pos.entry_order_id` возвращает `False` (лоты к позиции B корректно НЕ добавлялись), но дальше управление проваливалось в общий блок ведения реестра, где:

1. если запись заявки A в реестре была нетерминальной (INTENT/NEW/PARTIALLYFILL/SENT) — записывался **терминальный статус с ложным маркером успеха** `ledger.record_status(order_id, "FILL", reason="позиция уже зарегистрирована — идемпотентность")` (до правки строки 1524–1530): исполнение A к позиции НЕ применялось, но заявка A помечалась обработанной;
2. если запись A уже была терминальной (типовой случай позднего отчёта: FILL от `record_sent()`) — ни одна ветка не срабатывала: заявка A **молча считалась обработанной** только потому, что по FIGI есть OPEN-позиция; `uncertain_state` не выставлялся, UNCERTAIN-запись не создавалась, диагностика не писалась.

Оба поведения прямо запрещены ТЗ. Дополнительно: при пустом `entry_order_id` у OPEN-позиции `apply_late_entry_lots()` применяла лоты любой заявки («угадывание» вместо UNCERTAIN).

### 1.2. Точная функция и строки

- **До правки:** `register_executed_buy()` (main.py), ветка OPEN — строки 1401–1404; ложный маркер — ветка `elif rec is not None and rec["status"] not in _TERMINAL_ORDER_STATUSES:` строки 1524–1530.
- **После правки:** ветка OPEN — строки 1430–1457 (guard несовпадения — 1423–1452; вызов `apply_late_entry_lots` только для своей заявки — 1457).

### 1.3. Что изменено (только main.py, +52 строки в этой ветке)

В ветку `elif existing.state == PositionState.OPEN:` добавлен guard принадлежности, зеркальный ранее сделанной RECOVERY-ветке:

```python
if (order_id and (not existing.entry_order_id
                  or str(order_id) != str(existing.entry_order_id))):
    client.uncertain_state = True
    if ledger is not None:
        ledger.record_uncertain(
            order_id, trade_id=trade_id, figi=figi, ticker=ticker,
            side="BUY", requested_lots=requested_lots,
            executed_lots=int(executed_lots),
            reason=("order_id mismatch: отчёт заявки {A} (trade_id=…, N лот.) "
                    "не совпадает с входной заявкой {B} (trade_id=…) активной "
                    "OPEN-позиции — исполнение к позиции НЕ применено "
                    "(лоты/цена входа не изменены), успешный маркер не пишется; "
                    "требуется сверка/решение оператора"), ...)
    logging.error(...)          # диагностика: FIGI, старый order_id,
                                # entry_order_id, trade_id, количество
    _save_state_checked(monitor, rm)
    return existing
applied = monitor.apply_late_entry_lots(existing, executed_lots, order_id)
```

- исполнение чужой заявки НЕ применяется (лоты/цена входа позиции не меняются);
- успешный маркер (терминальный статус, «позиция уже зарегистрирована — идемпотентность») для заявки A НЕ пишется — `return existing` выполняется до блока ведения реестра;
- фиксируется `uncertain_state=True` + UNCERTAIN-запись с причиной, содержащей литерал `order_id mismatch`, и диагностикой (FIGI, order_id отчёта, текущий entry_order_id, оба trade_id, количество исполнения — reason + поля записи);
- новые BUY по FIGI блокируются **существующим** механизмом (`ledger.figi_blocks_buy()`, trading_cycle, без новых механизмов), глобально — `client.uncertain_state`;
- при неизвестном `entry_order_id` позиции — тот же UNCERTAIN («данных недостаточно — не угадывать»);
- совпадение trade_id НЕ разрешает применение при несовпадении order_id (проверки заявки и сделки согласованы);
- повторная обработка идемпотентна: лоты не удваиваются, повторных записей успеха нет, неопределённость не снимается (повторный UNCERTAIN-append в append-only журнале лишь подтверждает её).

### 1.4. Почему изменение не ломает правильный сценарий

- Первичная регистрация BUY: созданием позиции занимается ветка «нет активной позиции» — guard её не затрагивает (исполнение своей заявки при первом FILL регистрируется как прежде).
- Частичные исполнения и рост cumulative СВОЕЙ заявки: `order_id == entry_order_id` → guard пропускает → прежний путь `apply_late_entry_lots` + `entry_applied_lots` (доказано тестом D и ранее существовавшими s2/s3, P0-2/P0-4 тестами).
- Корректная RECOVERY (привязка по совпадению trade_id, рост своей заявки, промоция): код RECOVERY-веток не менялся (s6b, stage_recovery_same_entry_order_id_still_applies_growth проходят).
- Закрытие позиции и восстановление после перезапуска: путь startup_recovery/reconcile не менялся; guard срабатывает только при активной позиции и чужом order_id (s7/s9/s9b, test_stage_restart-тесты проходят).
- Интерфейсы функций не менялись; execution.py, order_ledger.py не тронуты.

### 1.5. Тесты, доказывающие исправление (tests/test_buy_registration_fix2.py)

- **Тест A** `test_a_old_order_a_vs_open_b` — старая заявка A vs OPEN B: количество/цена/entry_order_id B неизменны, A → UNCERTAIN с «order_id mismatch» + диагностикой, `uncertain_state=True`, нет терминальных записей и маркера «позиция уже зарегистрирована» для A, `figi_blocks_buy(FIGI)=True`; дополнительная фаза — тот же отчёт A с уже терминальной FILL-записью в реестре больше НЕ пропускается молча.
- **Тест B** `test_b_matching_trade_id_does_not_authorize` — совпадающий trade_id при разных order_id: лоты НЕ применяются, результат UNCERTAIN.
- **Тест C** `test_c_repeat_of_old_fill_stays_uncertain` — тройная повторная обработка: без двойного применения, без ложных маркеров, неопределённость сохраняется.
- **Тест D** `test_d_matching_order_id_still_applies` — контроль от перегиба: своя заявка применяется (+дельта), повтор идемпотентен, ложной неопределённости нет.

### 1.6. Результат тестов

```
tests/test_buy_registration_fix2.py  7 passed
мутационная проверка: на коде ДО правки падают ровно A, B, C (и F, G по P2);
D и E — контрольные, проходят до и после. Тесты не «вакуумные».
```

---

## 2. Дефект P2 — Telegram при неудачном сохранении state.json

### 2.1. Как выглядел дефект (подтверждён на актуальном коде до правки)

В `register_executed_buy()` результат `state_saved = _save_state_checked(monitor, rm)` (строка 1663 после правки) игнорировался при отправке уведомлений:

1. RECOVERY-сценарий: сообщение `«Позиция зарегистрирована как RECOVERY — контроль сохранён, автозакрытие отключено.»` отправлялось **безусловно** (до правки строки 1646–1651) — при `state_saved=False` пользователю сообщалось «контроль сохранён», хотя позиция существовала только в оперативной памяти;
2. первичная регистрация BUY (OPEN): уведомление об открытии отправлялось без предупреждения о несохранённом состоянии — создавало впечатление надёжной регистрации при `state_saved=False`.

### 2.2. Точная функция и строки

- **До правки:** `register_executed_buy()`, безусловный `await notify(...)` RECOVERY — строки 1646–1651; OPEN — 1678–1691.
- **После правки:** OPEN-уведомление — строки 1707–1742 (`if state_saved:` — 1729); RECOVERY-уведомление — строки 1747–1774 (`if state_saved:` — 1755).

### 2.3. Что изменено (только main.py)

- **RECOVERY, `state_saved=True`** — прежний текст без изменений («…контроль сохранён, автозакрытие отключено.»).
- **RECOVERY, `state_saved=False`** — отправляется отдельное критическое предупреждение (формулировка из ТЗ, с контекстом тикера/лотов):
  `⚠️ КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ: позиция RECOVERY обнаружена и зарегистрирована только в оперативной памяти. Сохранение state.json НЕ подтверждено. После перезапуска состояние может быть потеряно. Новые BUY заблокированы. Требуется проверить сохранение state.json и сверить позицию с брокером.`
  Утверждение «контроль сохранён» НЕ отправляется.
- **первичная регистрация BUY (OPEN), `state_saved=False`** — к сообщению об открытии добавляется то же критическое предупреждение («позиция зарегистрирована только в оперативной памяти… Новые BUY заблокированы…»); при `state_saved=True` текст прежний.
- Механизм `_save_state_checked()` НЕ менялся (по ТЗ): `STATE_SAVE_FAILED` в журнал событий, лог ошибки, блокировка новых входов через `state_save_failed` (trading_cycle, строка 2567) — как были. Подтверждение регистрации в ledger по-прежнему пишется только при `state_saved=True` (сделано предыдущим этапом, не тронуто).

### 2.4. Почему изменение не ломает правильный сценарий

- Успешный путь (state_saved=True) выдаёт те же сообщения, что и раньше (тест E — побайтно тот же текст RECOVERY).
- Неудачный путь не меняет учёт: позиция остаётся зарегистрированной в памяти, повторный разбор события при перезапуске обеспечен отсутствием преждевременного маркера в реестре (тест G).
- Другие уведомления (адопция reconcile, «RECOVERY разрешена», позднее исполнение) не содержали ложных утверждений о сохранении и не менялись.

### 2.5. Тесты, доказывающие исправление

- **Тест E** `test_e_recovery_notifies_saved_state` — при успешном сохранении обычное RECOVERY-уведомление, предупреждения нет.
- **Тест F** `test_f_recovery_notifies_unsaved_state` — при `save=False`: отправлено предупреждение («КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ», «только в оперативной памяти», «state.json НЕ подтверждено», «Новые BUY заблокированы»), «контроль сохранён» отсутствует, `state_save_failed=True` (новые BUY заблокированы), подтверждение регистрации в реестр не записано.
- **Тест G** `test_g_primary_buy_unsaved_state` — первичная BUY-регистрация при неудачном сохранении: уведомление содержит предупреждение и не сообщает о надёжном сохранении; в реестре нет преждевременного маркера «позиция зарегистрирована»; после восстановления сохранения перезапуск восстанавливает позицию (quantity/entry_order_id), заявка остаётся в `open_orders()` (повторный разбор события предусмотрен), `startup_recovery` не создаёт дубликат.

### 2.6. Результат тестов

```
tests/test_buy_registration_fix2.py  7 passed (A–G)
```

---

## 3. Изменённые и неизменённые файлы

### Изменены (этап FIX 2)

| Файл | Строк до/после | Diff этапа | SHA256 |
|---|---|---|---|
| `main.py` | 2960 → 3081 (+121) | ≈ +96 / −8 против предыдущего этапа; накопительно vs HEAD `afa18f0`: +475 / −41 | `c546af2e185d1fff37f5ad7c7182fecd67ca1c55846f6721f1288b5a76765193` |
| `tests/test_buy_registration_fix2.py` | 0 → 469 (новый) | новый файл, 7 тестов A–G | `75ffea906450f20c3997b43f4febc5593e016067a2146d37ce441dc982680e19` |

Характер правок main.py: два функциональных блока (guard OPEN-ветки; условные уведомления) + запись в changelog-шапке (строки 221–248). Файл не переписывался целиком — точечные правки.

### Не изменены в этом этапе

- `execution.py` (+57 vs HEAD — изменения ПРЕДЫДУЩЕГО этапа, в этом этапе не тронуто; numstat этапа — 0/0);
- `order_ledger.py` — **не менялся** (0/0 vs HEAD): требуемые для фиксов возможности (`record_uncertain`, UNCERTAIN-статус, `figi_blocks_buy`, append-only журнал) уже существуют, доказанной необходимости правок нет;
- `risk_manager.py`, `logger.py`, `strategy.py`, `screener.py`, `tinkoff_client.py`, `telegram_bot.py`, `config.py`, `close_all_positions.py`, `pay_in.py` — 0/0 vs HEAD;
- `tests/test_buy_registration_fixes.py` и остальные тесты — не менялись (несвязанные тесты не подгонялись под зелёный результат).

---

## 4. Сохранность данных

- `research_events.jsonl`: **не удалялся, не обрезался, не перезаписывался**. 4198 записей (>3000). `git diff --numstat` = 282 добавлено / 0 удалено (append-only; часть приращения — до этого этапа). Прогоны pytest штатно дописали события `TG_INCOMING`/`TRADING_START/STOP` из `tests/test_telegram_handlers.py` (этот существующий тест не перенаправляет EVENTS_FILE; поведение идентично в baseline-прогоне до правок и не связано с изменениями этапа).
- `order_ledger.jsonl`: не менялся этим этапом (mtime до начала работ; тесты используют копии реестра в tmp_path).
- `close_all_positions.py`: содержимое не менялось (0/0 vs HEAD).
- Commit/push не выполнялись; HEAD = `afa18f0`; робот и торговые заявки не запускались.

---

## 5. Прогоны тестов (реальные, python 3.12.16 / pytest 9.0.2)

**Baseline ДО правок (полный набор):** `15 failed, 276 passed` (10.30s).
**После правок (полный набор):** `15 failed, 283 passed` (10.82s).

Сравнение списков падений baseline↔after программно (json-отчёты pytest): **списки идентичны** — те же 15 тестов, новых падений 0, «исправленных» из числа известных 0:

1. `tests/test_execution.py::test_resolve_closing_fill_but_partial_becomes_uncertain`
2. `tests/test_execution.py::test_resolve_closing_rejected_with_partial_becomes_uncertain`
3. `tests/test_execution.py::test_resolve_closing_cancelled_with_partial_becomes_uncertain`
4. `tests/test_main.py::test_trading_cycle_not_fill_but_executed_creates_position`
5. `tests/test_p0_batch2_late_execution.py::test_n3_unknown_figi_execution_not_registered_global_block`
6. `tests/test_p0_defect_fixes.py::test_defect4_unknown_side_resolves_only_without_execution`
7. `tests/test_persistence.py::test_save_state_creates_file`
8. `tests/test_persistence.py::test_load_state_restores_positions_and_counts`
9. `tests/test_persistence.py::test_save_state_write_error_returns_false`
10. `tests/test_tinkoff_client.py::test_get_sandbox_max_lots_ok`
11. `tests/test_tinkoff_client.py::test_get_sandbox_max_lots_sell_none`
12. `tests/test_tinkoff_client.py::test_get_sandbox_max_lots_with_price`
13. `tests/test_trading_flag.py::test_is_trading_enabled_false`
14. `tests/test_trading_flag.py::test_stop_trading_does_not_touch_positions`
15. `tests/test_trading_flag.py::test_no_flag_blocks_new_entries`

Эти 15 известных падений НЕ исправлялись и НЕ маскировались; несвязанные тесты не менялись. Разница passed: 276 → 283 = ровно 7 новых тестов A–G.

**Новые тесты отдельно:** `tests/test_buy_registration_fix2.py — 7 passed`.
**Связанные файлы BUY/RECOVERY отдельно:** `test_buy_registration_fixes.py` + `test_p0_defect_fixes.py` + `test_p0_batch2_late_execution.py` + `test_execution.py` + `test_fail_closed.py` + `test_persistence.py` → `5 failed, 114 passed`, причём все 5 — из списка 15 известных (114 связанных тестов зелёные: первичная регистрация, частичные исполнения, повторные FILL, cumulative-рост, корректная RECOVERY, закрытие, восстановление после перезапуска).

**Мутационная проверка:** на копии main.py ДО правок новые тесты A, B, C, F, G падают (ловят дефект), контрольные D, E проходят. После правки — все 7 проходят.

---

## 6. Вердикт

**FIX VERIFIED**

- P1 (OPEN order_id mismatch): исправление в коде + тесты A/B/C/D реальными прогонами; мутационная проверка подтверждает, что тесты ловят именно дефект.
- P2 (Telegram при ошибке сохранения state.json): исправление в коде + тесты E/F/G реальными прогонами; та же мутационная проверка.
- Полный pytest: 15 известных падений — те же имена, что в baseline; новых падений нет; 283 passed.
- Сохранность данных и ограничения ТЗ соблюдены (см. §0 и §4).

---

## 7. Локальная замена

1. Заменить `main.py` в корне проекта файлом из поставки (полная версия, не diff).
2. Добавить `tests/test_buy_registration_fix2.py` в каталог `tests/` проекта (тесты принадлежат пакету `tests`, в корень не класть).
3. Перезапуск тестов: `python -m pytest tests -v` (ожидаемо: 283 passed / 15 known failures).
4. Никаких миграций state.json/ledger не требуется: формат state.json, поля позиций и реестра не менялись; журнал append-only совместим.

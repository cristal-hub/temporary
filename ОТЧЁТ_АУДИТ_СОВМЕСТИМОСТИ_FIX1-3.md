# ОТЧЁТ — ФИНАЛЬНЫЙ АУДИТ СОВМЕСТИМОСТИ FIX1–FIX3 С SAMSUNG

Дата аудита: 2026-11-20 (UTC). Режим: СТРОГО READ ONLY.

Проведённые операции: клонирование обоих репозиториев в изолированные каталоги аудита; чтение и сравнение файлов; запуск юнит-тестов с офлайн-заглушкой SDK в рабочих КОПИЯХ (не в клонах). Не выполнено (запрещено и не требовалось): изменение любых файлов репозиториев, commit/push/merge, запуск торгового робота, обращения к T-Invest API, отправка/отмена заявок, изменение `research_events.jsonl`, изменение `close_all_positions.py`, изменение стратегии/сигналов/фильтров/SL/TP/параметров риска.

## 1. ВЕРДИКТ

## A — МОЖНО ПЕРЕНОСИТЬ

Оба файла (`main.py`, `execution.py` из temporary) совместимы с текущими модулями Samsung без каких-либо изменений в остальных модулях. FIX1–FIX3 проверены независимо (код + строки + собственный тестовый прогон). Необходимость изменения других файлов отсутствует.

Условия переноса (не требуют изменений, это правила выбора файлов):

1. Переносить **только** `main.py` и `execution.py`. Файл `order_ledger.py` из temporary **не переносить**: в temporary лежит более старая редакция реестра (без per-order протокола применения), Samsung-версия новее, совместима и проверена (раздел 4, 7).
2. `close_all_positions.py`, `config.py`, `risk_manager.py`, `logger.py`, `strategy.py`, `screener.py`, `telegram_bot.py`, `tinkoff_client.py`, `state.json` — не трогать.
3. Тесты `test_buy_registration_fix2.py` и `test_buy_registration_fixes.py` класть в каталог `tests/` Samsung (не в корень) — иначе не собираются (раздел 9.2).
4. Откат на старый `main.py` безопасен: старый `_load_state` игнорирует новый ключ `closed_entry_order_ids` в `state.json` (проверено по коду, раздел 4.8).

---

## 2. Исходные состояния репозиториев

| Параметр | Samsung | temporary |
|---|---|---|
| URL | cristal-hub/cristal-hub-Tsclp_v2.1_volatile_Samsung | cristal-hub/temporary |
| HEAD | `afa18f0` (2026-11-19 14:28:03 +0300, «Add files via upload») | `64dbae8` (2026-11-19 20:42:31 +0300, «Add files via upload») |
| История | независимая (13+ коммитов) | независимая (общих коммитов нет) |
| Статус рабочего дерева | чистый | чистый |

Примечание: в ТЗ указаны 2648/3159/472/584 строк; фактические значения `wc -l` — 2647/3158/471/583 (разница = учёт последней строки без завершающего перевода строки, содержательно совпадает).

### 2.1 Комплектность

**Есть в Samsung, отсутствует в temporary (корень):** `config.py`, `risk_manager.py`, `logger.py`, `strategy.py`, `screener.py`, `telegram_bot.py`, `close_all_positions.py`, `tinkoff_client.py` (в temporary есть), каталог `tests/` (22 файла), `state.json`, `research_events.jsonl`, `requirements.txt`, диагностические скрипты.

**Есть в temporary, отсутствует в Samsung:** `main_FIX2_snapshot.py`, `test_buy_registration_fix2.py`, `test_buy_registration_fix2 (1).py` (байт-в-байт дубликат первого — артефакт повторной загрузки), `test_buy_registration_fix2_FIX2_snapshot.py`, `test_buy_registration_fixes.py`, отчёты (`ОТЧЁТ_BUY_РЕГИСТРАЦИЯ.md`, `ОТЧЁТ_FINAL.md`, `ОТЧЁТ_FIX2.md`, `ОТЧЁТ_FIX3.md`, `ОТЧЁТ_КОМПЛЕКТ_FIX3.md`). Дубликаты тестов в корне temporary (`test_main_helpers.py`, `test_p0_batch2_late_execution.py`, `test_p0_defect_fixes.py`, `test_tinkoff_client.py`, `test_tinkoff_client_currency.py`) байт-в-байт идентичны копиям в `tests/` Samsung (проверено `cmp`).

**Вывод по комплектности:** различия сами по себе не ошибка — temporary является «поставкой двух файлов + тестов + отчётов», а не полным дистрибутивом. Все отсутствующие в temporary модули есть в Samsung и совместимы (разделы 3–4).

### 2.2 Ключевые различия файлов

| Файл | Samsung | temporary | Различие |
|---|---|---|---|
| main.py | 2647 строк, `41a11757…` | 3158 строк, `9f3ad72c…` | только FIX1+FIX2+FIX3+«доп. этап» (раздел 5, 8) |
| execution.py | 471 строка, `d7250178…` | 583 строки, `409e8b0c…` | только tombstones (FIX1) + глубокие защиты (FIX3), чисто аддитивно |
| order_ledger.py | 411 строк, `cc6c5768…` (с per-order протоколом) | 482 строки, `aa21921e…` (более старая редакция, протокола нет) | РАЗНЫЕ редакции; temporary-версия не нужна и не рекомендуется к переносу |
| tinkoff_client.py | `e47b0803…` | `e47b0803…` | байт-в-байт идентичны |
| прочие общие файлы (`izmenenija*.diff`, `ОТЧЁТ_P0-3/4/5.md`, `РЕЗУЛЬТАТЫ_ТЕСТОВ*.txt`) | — | — | идентичны (проверено `cmp`) |

`main.py` и `execution.py` из temporary байт-в-байт совпадают с финальной поставкой предыдущего этапа (SHA256 совпадают с зафиксированными при поставке) — то, что проверялось ранее, и то, что лежит в temporary, есть один и тот же код.

---

## 3. Аудит A — импорты

### 3.1 Импорты temporary `main.py` (строки 279–305)

| Импорт | Символы | Модуль в Samsung | Совместимость |
|---|---|---|---|
| stdlib: `asyncio, csv, json, logging, os, uuid`, `datetime`, `typing` | — | Python 3.11+ (Samsung) | OK |
| `pandas` | `pd` | requirements.txt Samsung (`pandas>=2.0`) | OK |
| `t_tech.invest` | `CandleInterval, OrderDirection, OrderExecutionReportStatus` | внешний SDK `t-tech-investments==1.51.0` (requirements.txt Samsung); **список символов байт-в-байт совпадает с импортом старого Samsung main.py** — зависимость не меняется | OK |
| `config` | 11 атрибутов (3.1.1) | есть | OK (все атрибуты присутствуют) |
| `logger as research_logger` | `log_event, log_trade, TRADES_FILE, get_cumulative_net_pnl` | есть | OK (все 4 есть) |
| `order_ledger` | `OrderLedger, STATUS_FILL, STATUS_UNCERTAIN, utc_now_iso` | есть; `STATUS_FILL` — строка 16 Samsung order_ledger.py | OK |
| `risk_manager` | `RiskManager` | есть (класс, строка 36) | OK |
| `strategy` | `StrategyManager` | есть (класс, строка 30) | OK |
| `tinkoff_client` | `TinkoffClient, PositionData` | есть | OK |
| `execution` | `ExecutionMonitor, PositionState, ACTIVE_STATES` | переносится вместе (temporary execution.py содержит все три: класс 136, enum, `ACTIVE_STATES` строка 68) | OK |
| `screener` | `Screener` | есть (класс, строка 39) | OK |
| `telegram_bot` (лениво, внутри функции, строка 305) | `TelegramBot` | есть (класс, строка 23) | OK |

### 3.1.1 Атрибуты `config`, используемые temporary main.py

`TINKOFF_API_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ALLOWED_USER_ID`, `ENABLE_TELEGRAM`, `USE_SANDBOX`, `COMMISSION_PER_SIDE`, `MIN_CANDLES_FOR_SIGNAL`, `TRADING_CYCLE_SECONDS`, `CANDLES_LOOKBACK_DAYS`, `validate()` — все присутствуют в Samsung `config.py` (строки 12–140). Новых ключей config temporary main.py не требует.

### 3.2 Импорты temporary `execution.py` (строки 52–57)

`logging`, `dataclasses`, `enum`, `typing` (stdlib) + `import config` (присутствует; фактических обращений к атрибутам config из execution.py нет — импорт формальный). Внешних зависимостей нет, зависимость от `order_ledger` отсутствует (проверено).

### 3.3 Отсутствующие зависимости и несовпадения импортов

**Не обнаружены.** Единственная внешняя зависимость — SDK `t-tech-investments==1.51.0`, уже зафиксированная в `requirements.txt` Samsung; набор запрашиваемых символов не изменился относительно старого main.py. Для офлайн-прогонов тестов используется локальная перечислительная заглушка `t_tech` (только `CandleInterval`, `OrderDirection`, `OrderExecutionReportStatus`); см. раздел 10 (неопределённости).

---

## 4. Аудит B — совместимость интерфейсов

Проверены имена, количество, порядок и смысл аргументов, а также возвращаемые значения. Итог: **все обращения temporary main.py к модулям Samsung корректны**.

### 4.1 Таблица совместимости

| Вызов из temporary | Реализация в Samsung | Совместимость | Последствие несовместимости |
|---|---|---|---|
| `OrderLedger(LEDGER_FILE)` | `OrderLedger.__init__(path)` (order_ledger.py:52) | OK | — |
| `ledger.record_intent(order_id, trade_id=, figi=, ticker=, side=, requested_lots=, reason=)` | order_ledger.py:176, те же kwargs | OK | — |
| `ledger.record_sent(order_id, ..., status_hint=, executed_lots=, avg_exec_price=, ...)` | order_ledger.py:192 | OK | — |
| `ledger.record_status(order_id, status, trade_id=, figi=, ticker=, side=, requested_lots=, executed_lots=, lots_left=, avg_exec_price=, reason=, source=)` | order_ledger.py:211 | OK | — |
| `ledger.record_uncertain(order_id, trade_id=, figi=, ticker=, side=, requested_lots=, executed_lots=, reason=, source=)` | order_ledger.py:220 | OK | — |
| `ledger.latest(order_id)` / `latest_all()` / `has_order()` | order_ledger.py:359/366/363 | OK | — |
| `ledger.is_reliable` / `unreliable_reason` / `load()` | order_ledger.py:63/67/86 | OK | — |
| `ledger.open_orders(side=None)` / `open_count(side=None)` | order_ledger.py:383/404 | OK | — |
| `ledger.figi_blocks_buy(figi)` / `has_unknown_figi_open_orders()` / `figi_is_uncertain` | order_ledger.py:392/400/405 | OK | — |
| `ledger.export_summary()` (в `_save_state`, temporary main.py:668) | order_ledger.py:410 | OK | — |
| `ledger.STATUS_FILL` / `STATUS_UNCERTAIN` / `utc_now_iso()` | order_ledger.py:16/20/34 | OK | — |
| `RiskManager(client)`; `rm.calculate_position_size(figi, last_price, ...)`; `rm.calculate_sl_tp(entry_price, direction, ...)`; `rm.record_trade(figi)`; `rm.export_state()/import_state(dict)` | risk_manager.py:36/87/233/261/49/56 | OK | — |
| `client.uncertain_state` (атрибут, 46 обращений); `client.is_uncertain()` | tinkoff_client.py:124 + метод | OK | — |
| `client.post_market_order(figi, lots, OrderDirection.ORDER_DIRECTION_BUY, order_id=order_id)` | tinkoff_client.py:488 `post_market_order(self, figi, quantity, direction, order_id: Optional[str] = None)` | OK (в т.ч. локальная генерация order_id) | — |
| `client.get_sandbox_order_state(order_id)` (в т.ч. во 2-м проходе startup_recovery) | tinkoff_client.py:515 | OK | — |
| `client.get_sandbox_orders()` / `get_positions()` / `get_instrument_info(figi)` / `get_last_prices(...)` / `get_historical_candles(figi, days, interval=...)` / `cancel_order(order_id)` | tinkoff_client.py (все присутствуют) | OK | — |
| `ExecutionMonitor(client)`; `monitor.register(**kwargs)`; `register_recovery(...)`; `get_position(figi)`; `has_active_position(figi)`; `positions`; `monitoring_error`; `apply_late_entry_lots(pos, executed_lots, order_id)`; `record_recovery_entry_lots(pos, lots, order_id, already_seen_lots)`; `promote_recovery(pos, ...)`; `sync_recovery_quantity`; `resolve_closing/resolve_uncertain`; `check_all`; `closed_entry_order_ids`; `export_state()/import_state(dict)`; статические `_extract_price/_extract_commission` | temporary execution.py (переносится вместе): 136/152/181/.../289/245/208/.../539/554 — все 18 членов на месте | OK (самосогласовано; см. также 4.7) | — |
| `research_logger.log_event(type, payload)` (32 вызова); `log_trade(dict)`; `TRADES_FILE`; `get_cumulative_net_pnl()` | logger.py:60/80/16/95 | OK | — |
| `TelegramBot(token, chat_id, allowed_user_id, app_controls)` (main.py:516, 3060); `bot.application.initialize()/start()/updater.start_polling()`; `bot.register_commands()`; `bot.send_message(text)` (в `notify()`, main.py:478–486) | telegram_bot.py:25 (та же сигнатура), `register_commands` :58, `send_message` :109 | OK | — |
| `Screener()`; `screener.select_top_volatile(force=False)` | screener.py:39/56 | OK | — |
| `StrategyManager()`; `sm.get_signal(...)` | strategy.py:31/177 | OK | — |

### 4.2 `ExecutionMonitor`: конструктор, методы, сериализация

Конструктор `ExecutionMonitor(client)` совпадает со старым (execution.py:136). Сериализация: `export_state()` добавляет ключ `closed_entry_order_ids` (execution.py:539–551), формат `positions` (через `TrackedPosition.to_dict()`) не изменился (diff execution.py не содержит изменений `to_dict`/`asdict`). `import_state()` восстанавливает `closed_entry_order_ids` с дефолтом `[]` (execution.py:554–562).

### 4.3 Telegram: функции отправки

`notify()` в temporary main.py (478–486) — локальная обёртка над `telegram_bot.send_message(text)`; сигнатура и поведение совпадают с Samsung. Новых функций Telegram не требуется.

### 4.4 `state.json` — формат и совместимость сохранения/загрузки

Ключи, которые пишет temporary `_save_state` (main.py:649–673): `positions`, **`closed_entry_order_ids` (новый)**, `monitoring_error`, `risk`, `open_orders`. Текущий state.json Samsung имеет ключи `positions`, `monitoring_error`, `risk`, `open_orders` (проверено чтением файла).

- Прямая совместимость: temporary `_load_state` (693–727) читает `closed_entry_order_ids` с дефолтом `[]` (main.py:713) → старый state.json Samsung загружается без ошибок. Проверено эмпирически: полный набор тестов, включая persistence, отработал в конфигурации «Samsung-модули + новые main/execution».
- Обратная совместимость (откат): старый `_load_state` Samsung (main.py:577–598) читает только `positions/monitoring_error/risk` и молча игнорирует неизвестный ключ → после отката state.json, записанный новым main.py, загружается без ошибок (признак закрытых заявок при откате просто не используется).

### 4.5 `RiskManager` — аргументы и возвращаемые значения

Вызовы совпадают сигнатурно; `rm.export_state()` записывается в state.json как и прежде (`risk`); состав risk-блока не менялся (проверено на текущем state.json Samsung: `daily_trade_counts`, `last_reset_date` — рантайм-формат определяется неизменённым risk_manager.py).

### 4.6 Независимость `close_all_positions.py` и `preflight_check.py`

`close_all_positions.py` импортирует только `config`, `tinkoff_client`, `t_tech.invest` (строки 23–32) — от main/execution не зависит, замена не требует его изменения. `preflight_check.py` использует `main._load_state`, `main.reconcile`, `main.reconcile_done`, `ExecutionMonitor(client)` — все присутствуют в temporary main.py (693/729/398) и execution.py (136).

### 4.7 Обратная совместимость нового execution.py со старыми потребителями

Легаси-тест Samsung корня `test_execution.py` (10 тестов, старый API ExecutionMonitor) прошёл **10/10** с новым execution.py (и 10/10 со старым — контроль). `TrackedPosition`, `PositionState`, `ACTIVE_STATES` — экспортируются как прежде.

### 4.8 Итог B

Несовместимостей не найдено. Таблица заполнена фактическими сигнатурами; аргументы проверены по числу, порядку и смыслу (включая `status_hint`, `already_seen_lots`, `order_id=None`-дефолты).

---

## 5. Аудит C — независимая проверка FIX1–FIX3

Нумерация строк — по файлам temporary HEAD `64dbae8`. Вердикты по каждому пункту: фактическая логика сверена с кодом и подтверждена собственным тестовым прогоном (раздел 9). Все номера функций/строк проверены grep-ом по фактическим файлам.

**1. Первичная регистрация BUY не блокируется только из-за наличия FILL в ledger — PASS**
- Где: main.py, `register_executed_buy()` (строка 1114), guard первичной регистрации 1222–1267.
- Логика: `rec_pre = ledger.latest(order_id)` (1222); блокировка выполняется ТОЛЬКО при положительном доказательстве применения: `evidence_applied` (1231–1235: есть запись позиции по FIGI с `entry_order_id == order_id` и `entry_applied_lots >= executed_lots`) или `evidence_closed_entry` (1236–1239: `entry_order_id` совпадает или `order_id in monitor.closed_entry_order_ids`, строка 1238). Наличие терминального FILL в реестре при отсутствии позиций доказательством НЕ считается → первичная регистрация разрешена. Прежний бездоказательный guard (`executed_lots <= recorded_le → return None`) удалён.
- Риск: не обнаружен. Тесты: `test_buy_registration_fixes.py` `test_s1`, `test_s1b`, `test_s2`, `test_s3` (мой прогон: 21 passed).

**2. Повторное применение одних и тех же лотов исключено — PASS**
- Где: execution.py `apply_late_entry_lots()` 289–319 (курсор: `applied = int(pos.entry_applied_lots or 0)` — 314; `if le <= applied: return False` — 315–316; прирост только дельты `pos.quantity += le - applied` — 317–318); `record_recovery_entry_lots()` 245–286 (та же курсорная логика, `applied` — 272, + `already_seen_lots`); main.py guard `evidence_applied` 1231–1235.
- Риск: не обнаружен. Тесты: `test_s2`, `test_s3`, `test_d`, `test_h5`, пакет P0-4 (в полном прогоне).

**3. RECOVERY не присваивает исполнение старого ордера A новой позиции B — PASS**
- Где: main.py 1341–1383 (ветка RECOVERY без entry_order_id). Привязка ТОЛЬКО при `ownership_confirmed = bool(existing.trade_id) and bool(trade_id) and str(existing.trade_id) == str(trade_id)` (1351–1354). Иначе: `client.uncertain_state = True` + `ledger.record_uncertain(...)` + `_save_state_checked` + `return existing` (1360–1383) — исполнение не применяется, заявка не помечается обработанной.
- Риск: не обнаружен. Тесты: `test_s6`, `test_s6b`, `test_b`, `test_c`, `test_h6`.

**4. Сверяется trade_id — PASS**
- Где: main.py 1351–1354 (разрешающий случай: оба trade_id известны и совпадают — единственное условие привязки RECOVERY); в OPEN-ветке trade_id участвует только как диагностика (1534–1545), совпадение trade_id при несовпадении order_id применением НЕ разрешает (комментарий и реализация 1520–1557; тест `test_b` прямо проверяет «совпадающий trade_id не авторизует»).
- Риск: не обнаружен.

**5. Сверяется entry_order_id — PASS**
- Где: main.py OPEN-ветка 1529–1557: `if (order_id and (not existing.entry_order_id or str(order_id) != str(existing.entry_order_id)))` → uncertain_state + UNCERTAIN-запись «order_id mismatch» + `_save_state_checked` + return (лоты не применяются, маркер не пишется). RECOVERY с уже заданным entry_order_id: 1399–1416 — несовпадение → uncertain_state + UNCERTAIN + return; даже совпадение trade_id перепривязку не выполняет.
- Риск: не обнаружен. Тесты: `test_a`, `test_h6`, `test_stage_recovery_entry_order_mismatch_marks_uncertain`, `test_stage_recovery_entry_order_mismatch_even_if_trade_id_matches`.

**6. Отсутствующий/пустой/пробельный order_id блокирует применение лотов — PASS**
- Где: main.py guard FIX3 1310–1339: `if not str(order_id or "").strip():` (1310) → `client.uncertain_state = True` (1311); `research_logger.log_event("BUY_EXEC_NO_ORDER_ID", {...})` (1312–1327, поля figi/ticker/trade_id/order_id_raw/executed_lots/requested_lots/position_state/entry_order_id/entry_applied_lots/position_quantity/note); `logging.error` (1328–1337); `_save_state_checked(monitor, rm)` (1338); `return existing` (1339). Лоты/цена/маркеры не изменяются; запись в order_ledger НЕ создаётся (фиктивные ID запрещены). Покрывает None/""/пробелы и для OPEN-, и для RECOVERY-веток (guard стоит выше обеих).
- Риск: не обнаружен. Тесты: `test_h1`–`test_h3`, `test_i3`, `test_i4`.

**7. Deep guards присутствуют в `apply_late_entry_lots()` и `record_recovery_entry_lots()` — PASS**
- Где: execution.py 254–268 (`record_recovery_entry_lots`: `if not str(order_id or "").strip(): logging.error(...); return False`) и 299–313 (`apply_late_entry_lots`: то же). Отклонение немедленное, позиция не изменяется; чужой order_id отклоняется как и прежде (255/300 далее: `if order_id and pos.entry_order_id and order_id != pos.entry_order_id: return False`).
- Риск: не обнаружен. Тесты: `test_i1`, `test_i2` (прямые вызовы без ID + свой/чужой ID).

**8. Закрытые entry order IDs сохраняются как tombstones и не теряются из-за FIFO — PASS**
- Где: execution.py `_archive_closed_entry_order()` 142–149 (без лимита, вызовы при перезаписи CLOSED-записи: 161 в `register`, 191 в `register_recovery`); `export_state()` 539–551 (архив + все текущие CLOSED-записи, дедуп, без обрезки); `import_state()` 554–562 (полный импорт, дефолт `[]` для старого state.json); main.py `_save_state` 652–659 и `_load_state` 710–713 (ключ `closed_entry_order_ids` в state.json).
- Примечание: FIFO-лимит 200 существовал только в промежуточных версиях; ни в Samsung HEAD, ни в финальном комплекте лимита нет — проверено grep `MAX_CLOSED` (0 совпадений в обоих файлах).
- Риск: не обнаружен. Тесты: `test_s4b`, `test_state_roundtrip_closed_entry_order_ids`, `test_stage_archive_closed_entry_order_ids_no_200_cap`.

**9. Startup recovery обрабатывает терминальные FILL без маркера регистрации — PASS**
- Где: main.py `startup_recovery()` (2342), второй проход 2443–2501: перебор `ledger.latest_all()`; фильтр `side == "BUY"`, `status == STATUS_FILL`, `executed_lots > 0` (2462–2464); пропуск записей с reason-маркерами «позиция зарегистрирована» / «исполнение применено» (2465–2468); при доступности — актуализация лотов/цены через `client.get_sandbox_order_state(oid)` + `ExecutionMonitor._extract_price/_extract_commission` (2473–2482), иначе данные из записи реестра; вызов `register_executed_buy(..., source="startup_recovery_fill", ledger_status="FILL")` (2485–2500).
- Риски (не блокирующие): (а) фильтр по reason — строковая эвристика по русским маркерам; ошибку второго уровня ловит evidence-guard п.1 (двойная защита); (б) при недоступности брокерского статуса цена берётся из `avg_exec_price` записи `record_sent` (может быть None при экзотических сбоях — в этом случае позиция становится RECOVERY (`df=None`) и цена уточняется промоцией, что соответствует безопасному поведению `test_s1b`).
- Тесты: `test_s9`, `test_s9b`.

**10. Маркер регистрации ставится только после успешного сохранения состояния — PASS**
- Где: main.py 1580–1600: `state_saved_after_apply = _save_state_checked(monitor, rm)` (1585) выполняется ДО записи подтверждения; запись `ledger.record_status(...)` только в ветке `elif state_saved_after_apply:` (1599); при неудаче — `client.uncertain_state = True` + `ledger.record_uncertain(...)` + `logging.error` (1618–1643) — дельта остаётся доступной повторному применению. Финальное подтверждение регистрации: 1864–1875 — `if state_saved: ledger.record_status(...)` иначе только лог ошибки.
- Тесты: `test_stage_late_fill_confirmation_only_after_save`, `test_stage_no_registration_confirmation_when_state_save_failed`, `test_g`.

**11. Telegram не сообщает об успешном открытии до успешного сохранения состояния — PASS**
- Где: main.py 1793–1815: текст уведомления готовится (`open_msg`), отправка — `if state_saved: await notify(open_msg)` (1806–1807), иначе `notify(open_msg + "КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ ...")` (1808–1815).
- Тесты: `test_g` (первичная BUY + неудачное сохранение), `test_s8`, `test_s8b`.

**12. Ошибка сохранения переводит систему в безопасное состояние без ложного уведомления — PASS**
- Где: `_save_state_checked()` main.py 675–691: при неудаче — событие `STATE_SAVE_FAILED` в research_events.jsonl, `state_save_failed = True` (блокирует новые входы), лог; RECOVERY-уведомление при `state_saved=False` — критическое предупреждение вместо «контроль сохранён» (1832–1855). Дополнительно FIX3-ветка выставляет `uncertain_state=True` (1311). Система остаётся fail-closed: незаписанное состояние не дополняется новыми рисками.
- Тесты: `test_f`, `test_g`, `test_i5` (ошибка `_save_state_checked()` — без ложного подтверждения).

### 5.1 Итог аудита C

12/12 PASS. Единственные замечания — документационные (см. 7.2, п. 3) и эвристика reason-маркеров (п. 9), компенсированная дубирующим guard'ом.

---

## 6. Аудит D — сохранение торговой логики

Метод: прочитан полный diff Samsung→temporary по обоим переносимым файлам (main.py: 731 строк diff, 11 хунков; execution.py: 176 строк, 7 хунков); все хунки классифицированы. Другие модули не переносятся и не изменяются.

### 6.1 Классификация всех хунков main.py

| Хунк (строки temporary) | Содержимое | Категория |
|---|---|---|
| 147–155 | 2 переформулированные строки комментария шапки («подтверждённое безопасное разрешение неопределённостей реестра», «порядок вызовов trading_cycle/reconcile/poll_open_orders») | комментарии, без функциональности |
| 157–270 | changelog FIX1/FIX2/FIX3 в шапке | комментарии |
| 297 | импорт `STATUS_FILL` | FIX1 (второй проход startup) |
| 649–673 | `_save_state`: ключ `closed_entry_order_ids` | FIX1 (tombstones) |
| 693–727 | `_load_state`: восстановление `closed_entry_order_ids` с дефолтом | FIX1 (tombstones) |
| 1198–1267 | переписанный guard первичной регистрации (evidence-based) | FIX1 |
| 1281–1416 | FIX3 guard (1310–1339) + привязка RECOVERY по trade_id (1341–1383) + RECOVERY mismatch (1399–1416) | FIX3 + FIX1/доп. этап |
| 1505–1557 | OPEN order_id mismatch → UNCERTAIN | FIX2 |
| 1580–1650 | сохранение до подтверждения реестра; UNCERTAIN при неудаче сохранения | доп. этап (FIX-линия) |
| 1781–1815 | уведомление первичной BUY с учётом state_saved | FIX2 |
| 1821–1855 | RECOVERY-уведомление с учётом state_saved | FIX2 |
| 1864–1880 | подтверждение регистрации в реестр только при state_saved | доп. этап |
| 2442–2501 | второй проход startup_recovery (терминальные FILL) | FIX1 |

**Хунков, относящихся к стратегии/входам/выходам/рискам, нет.**

### 6.2 Проверка неизменности торговых параметров (фактический код)

- Вызовы стратегии/скринера/риска не менялись: `sm.get_signal(...)`, `screener.select_top_volatile(...)`, `rm.calculate_position_size(...)`, `rm.calculate_sl_tp(...)`, `rm.record_trade(...)` — те же имена и аргументы в temporary (grep по фактическим вызовам, раздел 4.1).
- Условия входа/выхода, LONG/SHORT-ограничения, фильтры инструментов, расчёт SL/TP, риск на сделку, лимиты экспозиции, ограничения числа сделок, параметры сканирования — реализованы в `strategy.py`, `screener.py`, `risk_manager.py`, `config.py`, которые **не переносятся** и остаются в Samsung без изменений.
- `trading_cycle` (2584): сохранены порядок вызовов, фильтр `trading.flag`, запрет новых входов при `uncertain_state`/`state_save_failed` (эти механизмы только используются, не изменялись — подтверждено diff-ом: в торговом цикле изменений нет).
- Все поведенческие изменения FIX-линии локализованы в обработке исполнений BUY (регистрация/привязка/подтверждения/уведомления) и переводят систему только в более консервативное состояние (fail-closed: больше UNCERTAIN-блокировок, ни одного разблокирования без доказательств). Пути SELL/закрытия (`process_closing`, `register_executed_sell`, `poll_open_orders`) — без изменений (diff отсутствует).

### 6.3 Вывод D

Стратегия, сигналы, фильтры, SL/TP, расчёт лотов, риск-модель, экспозиция, лимиты сделок, параметры сканирования — **не изменены**. Все изменения кода относятся к FIX1–FIX3 и сопутствующим защитам целостности (tombstones, save-before-confirm, второй проход startup), что соответствует согласованным этапам. Изменений, не относящихся к FIX1–FIX3, в исполняемом коде **нет** (кроме двух строк комментариев, см. 7.2).

---

## 7. Изменения temporary относительно Samsung вне FIX1–FIX3

### 7.1 Функциональные изменения кода — отсутствуют

Полный diff main.py: 267 добавленных строк кода + 28 удалённых — все относятся к FIX1/FIX2/FIX3/доп. этапу (классификация в 6.1). execution.py: +39 строк кода, 0 удалённых — tombstones + deep guards. Комментарии: +284/+71 строк (changelog и пояснения).

### 7.2 Нефункциональные (документационные) расхождения

1. main.py 147–155: две строки комментария шапки переформулированы (смысл не изменён).
2. Заголовок temporary main.py содержит changelog трёх этапов FIX — это документация, отсутствующая в Samsung main.py (самих исправлений в Samsung нет).
3. **Устаревшая фраза**: в шапке FIX3 main.py (строка ~127) сказано «execution.py не менялся: безопасность гарантирована на уровне вызывающего кода». Для финального комплекта это не соответствует действительности — execution.py содержит глубокие защиты FIX3 (задокументированы в собственной шапке execution.py, строки 32–54). Влияние на поведение отсутствует; рекомендуется при переносе ничего не править (в ТЗ правки запрещены), а учесть при следующей согласованной редакции документации.

### 7.3 Лишние файлы в temporary (не нужны для переноса)

- `order_ledger.py` (temporary) — более старая редакция реестра (без per-order протокола `record_observation`/`_applications`/`_prepared`/`_committed`, присутствующих в Samsung-версии 411 строк). Перенос ухудшил бы реестр. НЕ переносить.
- `main_FIX2_snapshot.py`, `test_buy_registration_fix2_FIX2_snapshot.py` — базовые снимки для regression-сверки, в рантайме не используются.
- `test_buy_registration_fix2 (1).py` — байт-в-байт дубликат `test_buy_registration_fix2.py` (артефакт загрузки).
- Копии 5 тестов в корне temporary — идентичны файлам в `tests/` Samsung.
- Отчёты (*.md) — документация.

---

## 8. Аудит E — тесты

### 8.1 Фактическое содержимое `test_buy_registration_fix2.py` (temporary, 1037 строк, `12526db7…`)

Файл содержит **18 тестов** в трёх группах:

| Группа | Тесты | Что проверяет |
|---|---|---|
| FIX2 A–G (строки 107–417+) | `test_a_old_order_a_vs_open_b`, `test_b_matching_trade_id_does_not_authorize`, `test_c_repeat_of_old_fill_stays_uncertain`, `test_d_matching_order_id_still_applies`, `test_e_recovery_notifies_saved_state`, `test_f_recovery_notifies_unsaved_state`, `test_g_primary_buy_unsaved_state` | mismatch order_id при OPEN; trade_id не авторизует; идемпотентность; корректные Telegram-уведомления при успехе/неудаче сохранения |
| FIX3 H1–H6 (строки 568–725+) | `test_h1_order_id_none_not_applied`, `test_h2_order_id_empty_not_applied`, `test_h3_order_id_blank_not_applied`, `test_h4_repeat_no_order_id_idempotent`, `test_h5_matching_order_id_still_applies`, `test_h6_foreign_order_still_blocked` | исполнение без order_id (None/""/пробелы) — лоты не применяются, UNCERTAIN, идемпотентность; свой ID — штатная дельта; чужой ID — блокировка |
| FIX3 доп. I1–I5 (строки 798–988+) | `test_i1_execution_apply_late_rejects_missing_order_id`, `test_i2_execution_record_recovery_rejects_missing_order_id`, `test_i3_recovery_branch_no_order_id_not_applied`, `test_i4_uncertain_state_survives_restart`, `test_i5_save_failure_no_false_confirmation` | глубокие защиты execution.py прямыми вызовами; RECOVERY-ветка main; персистентность uncertain_state; отсутствие ложного подтверждения при ошибке сохранения |

### 8.2 Почему файл лежит в корне temporary, хотя внутри указан путь `tests/…`

Первая строка файла — `# tests/test_buy_registration_fix2.py`; на строках 55 и 59 стоят импорты `from tests.test_p0_defect_fixes import (…)` и `from tests.test_p0_defect_fixes import FILL, FIGI, TICKER`. Файл создан как член пакета `tests/`, но в temporary загружен через веб-интерфейс GitHub в плоскую структуру корня. Проверка факта: запуск из корня temporary — **ошибка коллекции** (`ERROR test_buy_registration_fix2.py … no tests collected`, ImportError на пакет `tests`, воспроизведено мной). В Samsung каталог `tests/` существует, `tests/test_p0_defect_fixes.py` идентичен (проверено `cmp`), поэтому файл должен размещаться в `tests/` Samsung — при этом всё собирается (проверено моим прогоном).

### 8.3 Импорты и зависимости теста

`pytest`, `pytest-asyncio`, `logger`, `main`, `execution` (`ExecutionMonitor, PositionState`), `tinkoff_client.PositionData`, фикстуры/константы из `tests.test_p0_defect_fixes` (`FakeClient`, `FakeRM`, `make_ledger`, `FILL`, `FIGI`, `TICKER`, изолирующая фикстура `env`: tmp-пути STATE_FILE/EVENTS_FILE, `reconcile_done=True`, `state_save_failed=False`). Косвенные зависимости через `import main`: `pandas`, `pandas-ta-classic`, `python-telegram-bot`, `config`, `order_ledger`, `risk_manager`, `strategy`, `screener`, `t_tech.invest`.

### 8.4 Можно ли запускать с текущей структурой Samsung — ДА (проверено)

Конфигурация запуска (воспроизведена мной): копия Samsung + `main.py`/`execution.py` из temporary + `test_buy_registration_fix2.py` и `test_buy_registration_fixes.py` в `tests/` + офлайн-заглушка `t_tech` в PYTHONPATH. Команда `python -m pytest tests -q`. Результат — 8.5.

### 8.5 Разбор заявленных результатов: 294 passed / 15 failed — ПОДТВЕРЖДЕНО СОБСТВЕННЫМ ПРОГОНОМ

| Конфигурация | Кто запускал | Результат |
|---|---|---|
| Копия Samsung + 2 новых файла + 2 тест-файла в tests/ (эквивалент заявленной) | **этот аудит, независимо** | **294 passed / 15 failed** |
| Чистая копия Samsung (без overlay) — контроль | **этот аудит, независимо** | **255 passed / 15 failed** |
| Заявлено в ОТЧЁТ_КОМПЛЕКТ_FIX3.md (temporary) | заявка GLM | 294 passed / 15 failed (+5 = I1–I5 к предыдущим 289) |
| Заявленный baseline FIX1+2 | заявка GLM | 283 passed / 15 failed |

Арифметика сходится точно: 255 (чистый Samsung) + 21 (FIX1-регрессия `test_buy_registration_fixes.py`) + 7 (FIX2 A–G) + 6 (H1–H6) + 5 (I1–I5) = 294. Все 39 новых тестов прошли в моём прогоне; заявленные «passed» подтверждены, мне не пришлось принимать их на веру.

### 8.6 Сопоставление 15 ошибок с baseline: те же тесты, те же причины — ПОДТВЕРЖДЕНО

Список 15 ошибок **поимённо идентичен** в двух моих прогонах (чистый Samsung vs overlay) — формальное `diff` списков FAILED пуст. Значит, все 15 ошибок **унаследованы от самого Samsung и не созданы FIX1–FIX3**.

| # | Тест (15 failed, одинаково в обеих конфигурациях) | Причина (по фактическому выводу) |
|---|---|---|
| 1–3 | `test_execution.py::test_resolve_closing_{fill,rejected,cancelled}_with_partial_becomes_uncertain` | тест ожидает перевод позиции в UNCERTAIN при частичном исполнении закрытия (`assert <OPEN> == <UNCERTAIN>`, test_execution.py:311); текущий код (и старый, и новый) так не делает — унаследованное расхождение ожиданий и реализации |
| 4 | `test_main.py::test_trading_cycle_not_fill_but_executed_creates_position` | `assert None is not None` (test_main.py:226) — унаследованное |
| 5 | `test_p0_batch2_late_execution.py::test_n3_unknown_figi_execution_not_registered_global_block` | `assert False is True` (строка 258) — унаследованное |
| 6 | `test_p0_defect_fixes.py::test_defect4_unknown_side_resolves_only_without_execution` | `assert 'UNCERTAIN' == 'CANCELLED'` (строка 570) — унаследованное |
| 7–9 | `test_persistence.py::test_save_state_creates_file / test_load_state_restores_positions_and_counts / test_save_state_write_error_returns_false` | порядок-зависимые: **изолированно проходят (9/9)**, падают только в полном прогоне — дефект изоляции тестов, унаследованный |
| 10–12 | `test_tinkoff_client.py::test_get_sandbox_max_lots_{ok,sell_none,with_price}` | `TypeError: GetMaxLotsRequest() takes no arguments` — офлайн-заглушка SDK не реализует request-объект; на реальном SDK 1.51.0 результат, вероятно, иной (не проверяемо без SDK/сети); падают одинаково в обеих конфигурациях |
| 13–15 | `test_trading_flag.py::test_is_trading_enabled_false / test_stop_trading_does_not_touch_positions / test_no_flag_blocks_new_entries` | порядок-зависимые: **изолированно проходят (7/7)** — дефект изоляции тестов, унаследованный |

Заявленная в отчётах temporary цепочка baseline (276/15 → 283/15 → 289/15 → 294/15 при неизменном наборе из 15) согласуется с моими двумя независимыми точками: те же 15 присутствуют уже на чистом Samsung.

### 8.7 Не называй тесты пройденными, если не запускал — соблюдено

Все статусы «passed» в этом отчёте — из моего собственного прогона (два полных прогона + точечные изолированные прогоны). Заявленные ранее результаты сопоставлены и воспроизведены, а не приняты на веру.

### 8.8 Что нужно для запуска (безопасные заглушки)

- Зависимости: `pytest>=7`, `pytest-asyncio`, `pandas>=2.0`, `pandas-ta-classic`, `python-telegram-bot>=20,<22` (из requirements.txt Samsung; все установлены в песочнице аудита).
- SDK: `t-tech-investments==1.51.0` (боевой вариант) **или** офлайн-перечислительная заглушка `t_tech` (только `CandleInterval`, `OrderDirection`, `OrderExecutionReportStatus` + минимальные обёртки), как использовано в этом аудите и в предыдущих этапах.
- Сетевых вызовов нет: тесты построены на `FakeClient`/`FakeRM`; заглушка не выполняет сетевых операций; робот, API, торговые методы не запускались.

---

## 9. Разница размеров main.py/execution.py и достаточность двух файлов (раздел 5 ТЗ)

### 9.1 Количественная оценка

| Файл | Было (Samsung) | Стало (temporary) | Diff | Код | Комментарии | Пустые |
|---|---|---|---|---|---|---|
| main.py | 2647 | 3158 | +552 / −41 (net +511) | +267 / −28 | +284 / −13 | +1 |
| execution.py | 471 | 583 | +112 / −0 (net +112) | +39 / −0 | +71 / −2 | +2 |

Прирост размеров объясняется полностью: (а) changelog-комментарии шапок (+~130 строк main.py, +48 execution.py), (б) поясняющие inline-комментарии защит, (в) собственно код FIX1–FIX3 и сопутствующих защит (перечень исчерпывающий — раздел 6.1 и 7.1).

### 9.2 Являются ли изменения необходимой частью исправлений — ДА

Каждый функциональный блок входит в согласованную FIX-линию: evidence-guard первичной регистрации (FIX1), tombstones без лимита (доп. этап FIX1), привязка RECOVERY по trade_id и явная неопределённость при несовпадении (FIX1/доп. этап), OPEN order_id mismatch (FIX2), Telegram при ошибке сохранения (FIX2), guard «исполнение без order_id» (FIX3), глубокие защиты execution.py (FIX3), save-before-confirm (доп. этап), второй проход startup_recovery (FIX1). Никаких посторонних изменений кода не обнаружено.

### 9.3 Требуют ли изменения переноса других файлов — НЕТ

- Все обращения к модулям Samsung совместимы (раздел 4) — включая `STATUS_FILL`, который присутствует в Samsung order_ledger.
- `execution.py` не зависит от `order_ledger`; его новые члены (`closed_entry_order_ids`, deep guards) самодостаточны.
- `close_all_positions.py` от main/execution не зависит; `preflight_check.py` использует только сохранившиеся API.
- Торговые модули (`config`, `risk_manager`, `strategy`, `screener`, `telegram_bot`, `logger`) не переносятся и новых требований к ним нет.

### 9.4 Можно ли использовать только два новых файла с остальными Samsung-модулями — ДА

Подтверждено тремя независимыми доказательствами: (1) сигнатурная сверка всех вызовов (раздел 4); (2) полный тестовый прогон «Samsung-модули + 2 новых файла» — 294 passed, те же 15 унаследованных отказов, ни одной новой ошибки (раздел 8.5–8.6); (3) легаси-тест старого API `test_execution.py` (корень Samsung) — 10/10 с новым execution.py.

### 9.5 Особый случай: order_ledger.py в temporary

В temporary лежит **другая** редакция реестра (482 строки, шапка «НОВЫЙ МОДУЛЬ (P0-1/P0-2)», без per-order протокола применения `record_observation`/`_applications`/`_prepared`/`_committed`, которые есть в Samsung-версии 411 строк). Это не ошибка поставки, но переносить его не нужно и не следует: Samsung-версия новее, временная main.py с ней проверена (294 passed). Обе редакции содержат весь API, необходимый temporary main.py; приоритет — Samsung-версия (она была в проверенном прогоне и используется остальными Samsung-артефактами).

---

## 10. SHA256 проверенных файлов

### 10.1 Samsung HEAD afa18f0

| Файл | SHA256 |
|---|---|
| main.py | `41a117570bcc61b87c2e5873b934f2bd4e0e05458220ff916cbc69cde2f2149a` |
| execution.py | `d7250178317d4acc53202944325fb93f58d4f02900cbbb418464564f9d2fdb5b` |
| order_ledger.py | `cc6c5768079afb4501779790ce3b5983c9f157bf5d9cf275060504235eb82d3d` |
| tinkoff_client.py | `e47b0803b41d82713166602f6bc489eee265b38cb9a4343c95efd87ac7491533` |
| config.py | `20f5c022888c15145f5b47d89ef706aeec8fc4f6a7f9f35753ac215d85d1e7d3` |
| risk_manager.py | `d1c72c2ee09dc877803661c25028442413ffc13b2b9168efa59f99255e5982b8` |
| logger.py | `1e8a4f7ba1d8908dded39132b730cae4615fc91171f89f441f9a85fb8aa6b2dc` |
| strategy.py | `43906866a226aabae0fff266dde77d88345ae27f87b0948689929aa37171537f` |
| screener.py | `6db12eb1da4434fba2bb9997a676b86b7431b67bb0f9ae1e85b56db11d8341c9` |
| telegram_bot.py | `8a98ab4c5b5fe5070813e3ac4bf0f57f592c0d9ce5483df826ad1f2cd1ca1f6e` |
| close_all_positions.py | `a0864e2425f732ff20503210f42e38830c5d273da4907b383b57af047e38108a` |
| state.json | `2564179dbf8d5e5900ff5d887c5b4594ec999ece6b656bc393492db640135cf5` |

### 10.2 temporary HEAD 64dbae8

| Файл | SHA256 |
|---|---|
| **main.py (к переносу)** | `9f3ad72c3aa6fd4733f68cf0d7ff5e667f40c1b6db680dcd003e7cdd76df6dd5` |
| **execution.py (к переносу)** | `409e8b0c05e29e6e798ab0888cb49120b13687bf0765531d8e3907fdd50f754e` |
| order_ledger.py (НЕ переносить) | `aa21921ecc4aa043db244952134ef422f33fd80c2363979703971c8114b25af7` |
| tinkoff_client.py (идентичен Samsung) | `e47b0803b41d82713166602f6bc489eee265b38cb9a4343c95efd87ac7491533` |
| test_buy_registration_fix2.py | `12526db78b5213a7205971113567bc34b545651cdef4a850fc7e764d11a54a3d` |
| test_buy_registration_fix2 (1).py (дубликат) | `12526db78b5213a7205971113567bc34b545651cdef4a850fc7e764d11a54a3d` |
| test_buy_registration_fixes.py | `2c917989640cbdfdcb78cdfaabb7478a75c3c61452f571865f4ae591d842fd07` |
| test_buy_registration_fix2_FIX2_snapshot.py | `75ffea906450f20c3997b43f4febc5593e016067a2146d37ce441dc982680e19` |
| main_FIX2_snapshot.py | `c546af2e185d1fff37f5ad7c7182fecd67ca1c55846f6721f1288b5a76765193` |
| test_p0_defect_fixes.py (идентичен tests/ Samsung) | `b26878c445d235e4555390b30031b750340784ebe44fddd8623eaa0c6900216e` |
| test_p0_batch2_late_execution.py (идентичен) | `c93720403c3776ecbd8d88008dbbfdd74342463b30df5e3383840ae1d0897044` |
| test_main_helpers.py (идентичен) | `b7dde19eb5bc5b6b72d9c5f038b1ce0c1819860cbc576fbebf26c8d0aedb2e0b` |
| test_tinkoff_client.py (идентичен) | `d6134b266d63534517320854656f982ba5abe6498c9e865b291413893aa539be` |
| test_tinkoff_client_currency.py (идентичен) | `9699463b44baef37516ec513e45bde1edd5eea60e919ac8d84fbb9da46faff5a` |

Контроль целостности поставки: temporary main.py/execution.py байт-в-байт совпадают с файлами, зафиксированными при финальной поставке предыдущего этапа (SHA256 совпадают).

---

## 11. Список файлов, которые в конечном итоге потребуется переносить

**Обязательно (2 файла):**

1. `temporary/main.py` → Samsung `main.py` (`9f3ad72c…`, 3158 строк)
2. `temporary/execution.py` → Samsung `execution.py` (`409e8b0c…`, 583 строки)

**Опционально, только для регрессионного контроля (в рантайме не используются):**

3. `temporary/test_buy_registration_fix2.py` → Samsung `tests/test_buy_registration_fix2.py`
4. `temporary/test_buy_registration_fixes.py` → Samsung `tests/test_buy_registration_fixes.py`

**Не переносить:**

- `temporary/order_ledger.py` (старее Samsung-версии; Samsung-версия остаётся) — иначе деградация реестра.
- `close_all_positions.py` — запрещено изменять и не требуется.
- `main_FIX2_snapshot.py`, `test_*_FIX2_snapshot.py`, `test_buy_registration_fix2 (1).py`, корневые дубликаты тестов, отчёты — артефакты/документация.

**Никакие другие файлы Samsung не изменяются.**

---

## 12. Оставшиеся неопределённости

1. **Реальный SDK не проверен в песочнице аудита.** Прогоны выполнены с офлайн-заглушкой `t_tech` (только перечисления; `GetMaxLotsRequest` не реализован). Из-за этого 3 теста `get_sandbox_max_lots_*` падают в обеих конфигурациях; на реальном SDK 1.51.0 их поведение, вероятно, иное. Набор импортируемых символов SDK идентичен старому main.py, поэтому риск совместимости с SDK минимален, но контрольный прогон полного пакета на машине с реальным SDK (как в РЕЗУЛЬТАТЫ_ТЕСТОВ.txt: Python 3.12, t-tech-investments 1.51.0) перед переносом — разумная предосторожность. На поведение FIX-логики это не влияет: FIX-тесты (39) не зависят от SDK-объектов.
2. **Корневые причины 6 порядок-зависимых отказов не расследованы** (persistence ×3, trading_flag ×3 — изолированно проходят). Они унаследованы, идентичны в обеих конфигурациях и вне объёма настоящего аудита (аудит не исправляет найденные проблемы).
3. **6 содержательных унаследованных отказов** (execution ×3, main ×1, p0_batch2 ×1, p0_defect ×1) — расхождения ожиданий старых тестов с текущей реализацией; существуют на чистом Samsung; не являются регрессией переноса. Их устранение — отдельное решение владельца.
4. **Устаревшая строка документации** в шапке FIX3 temporary main.py («execution.py не менялся») — doc-only, поведение не затрагивает (раздел 7.2, п. 3).
5. **temporary/order_ledger.py** не прогонялся в паре с temporary main.py (в этом нет необходимости; рекомендация — оставить Samsung-версию, раздел 9.5).
6. **Боевое поведение** не проверялось принципиально (запрещено): вывод о безопасности экстраполируется из кода, сигнатур и офлайн-тестов с FakeClient. Второй проход startup_recovery при старте обращается к `get_sandbox_order_state`/`get_instrument_info` — существующие методы, только песочница; при недоступности брокера ветка деградирует к данным реестра (безопасно).
7. В temporary корневой запуск `pytest tests` невозможен (нет каталога tests/ и модулей-зависимостей) — это ожидаемо: temporary — поставка файлов, а не рабочий дистрибутив.

---

## 13. Подтверждение соблюдения режима READ ONLY

- Клоны Samsung и temporary получены только на чтение; в них не вносилось ни одного изменения (`git status` чистый; тестовые прогоны выполнены в отдельных копиях `ws_new`/`ws_base`, артефакты — только в них).
- Не выполнялось: commit/push/merge/ветки, запуск робота, подключения к T-Invest API, отправка/отмена/изменение заявок, изменения `research_events.jsonl` (файлы клонов не тронуты), изменения `close_all_positions.py`, изменения стратегии/сигналов/фильтров/SL/TP/параметров риска.
- Найденные проблемы (раздел 12) не исправлялись — по требованию ТЗ.



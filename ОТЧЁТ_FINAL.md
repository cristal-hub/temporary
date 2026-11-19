# ОТЧЁТ — ФИНАЛЬНЫЙ `main.py` (FIX 1 + FIX 2 + FIX 3)

Репозиторий: `https://github.com/cristal-hub/temporary.git`, ветка `main`,
рабочее дерево `/home/z/my-project/repo/`. Режим: только код + unit-тесты на
локальных заглушках (без сети, без T-Invest API, без заявок, без запуска
робота, без commit/push).

---

## 1. Полный готовый файл `main.py`

- Финальный файл: `download/main.py` (байт-в-байт идентичен рабочему
  `repo/main.py`, 3158 строк).
- Снимок FIX 2 (использован как baseline-эталон) сохранён отдельно:
  `download/main_FIX2_snapshot.py` (3081 строка).
- `python -m py_compile` — успешно (файл синтаксически целостен).

## 2. SHA256

| Файл | SHA256 | Статус |
|---|---|---|
| **`main.py` (финальный, FIX 1+2+3)** | `9f3ad72c3aa6fd4733f68cf0d7ff5e667f40c1b6db680dcd003e7cdd76df6dd5` | итоговый deliverable |
| `main_FIX2_snapshot.py` | `c546af2e185d1fff37f5ad7c7182fecd67ca1c55846f6721f1288b5a76765193` | baseline-эталон (сохранён) |
| `execution.py` | `2235c42579344475be6af3181d48c0376da1be939aa88fa17fa81303dea3727a` | не менялся (= снимок FIX 2) |
| `order_ledger.py` | `cc6c5768079afb4501779790ce3b5983c9f157bf5d9cf275060504235eb82d3d` | не менялся |
| `close_all_positions.py` | `a0864e2425f732ff20503210f42e38830c5d273da4907b383b57af047e38108a` | не менялся |
| `tests/test_buy_registration_fix2.py` | `eb2d86e430ca54d3258aaba86fa7644dea286fc9177f5a9f7134becf3cb45153` | не менялся (= отчёт FIX 3) |
| `research_events.jsonl` | префикс 4305 строк = `cdf17ae96de53a30…` | append-only (см. п.6) |

## 3. Фактически проверенные тесты и результаты

Среда: локальные заглушки `t_tech` (только перечисления SDK, без сети),
FakeClient/FakeRM; публичные зависимости `pandas-ta-classic`,
`python-telegram-bot`. Все запуски из корня репозитория с
`PYTHONPATH=<stubs>`.

| # | Команда | Результат |
|---|---|---|
| 1 | `python -m pytest tests/test_buy_registration_fix2.py -q` | **13 passed** (7 тестов FIX 2 A–G + 6 тестов FIX 3 H1–H6) |
| 2 | `python -m pytest tests/test_buy_registration_fixes.py -q` | **21 passed** (регрессия FIX 1) |
| 3 | `python -m pytest tests -q` (полный пакет) | **289 passed / 15 failed** |
| 4 | Baseline-контроль: снимок FIX 2 в изолированной копии, та же команда | **283 passed / 15 failed** |
| 5 | Дополнительный скрипт вне репозитория `scripts/verify_fix3_recovery_branch.py` | **RECOVERY-ветка ЗАКРЫТА, OPEN-ветка ЗАКРЫТА** (25 проверок OK) |

### Соответствие тестов пунктам проверки ТЗ

- **П.5 (None / "" / пробельный order_id — позиция не меняется):**
  `test_h1_order_id_none_not_applied`, `test_h2_order_id_empty_not_applied`,
  `test_h3_order_id_blank_not_applied` — **passed**. Проверяются фактические
  состояния: `quantity=3`, `entry_applied_lots=3`, `entry_price=100.0`,
  `entry_order_id` не изменён, второй позиции нет, `uncertain_state=True`,
  `is_uncertain()=True`, в реестре ни одной новой записи (фиктивных ID нет),
  1 диагностическое событие `BUY_EXEC_NO_ORDER_ID` с `order_id_raw`,
  `position_state`, `entry_applied_lots`, `position_quantity`, причиной в
  `note`.
- **П.6 (повторный вызов — позиция не меняется):**
  `test_h4_repeat_no_order_id_idempotent` — **passed** (повтор: quantity/
  счётчики/цена/привязка/state unchanged, `uncertain_state=True`
  сохраняется, реестр не изменён). Подтверждено скриптом п.5 для
  RECOVERY-ветки (повтор: позиция и реестр не изменены).
- **П.7 (свой order_id работает, чужой блокируется):**
  `test_h5_matching_order_id_still_applies` — **passed** (дельта +2
  применена: quantity 3→5, `entry_applied_lots=5`, `entry_price=100.0`,
  ложной неопределённости нет, FILL «позднее исполнение применено»,
  штатное уведомление, повтор идемпотентен);
  `test_h6_foreign_order_still_blocked` — **passed** (лоты чужой заявки A
  не применены к позиции B, перепривязки нет, A → UNCERTAIN «order_id
  mismatch», терминального статуса и «позиция уже зарегистрирована» нет —
  защита FIX 2 действует).

## 4. Сравнение с базовым прогоном

- Зафиксированный baseline (FIX 1 + FIX 2): **283 passed / 15 failed**.
- Финальный код (FIX 1 + FIX 2 + FIX 3): **289 passed / 15 failed**.
- Прирост **ровно +6 passed** = 6 новых тестов H1–H6. **Новых падений нет.**
- Списки 15 падений baseline-копии и финального кода выгружены и сравнены
  построчно по имени теста: **множества идентичны** (`diff` пуст). Все 15 —
  ранее известные: `test_execution` ×3 (resolve_closing),
  `test_main` ×1 (trading_cycle), `test_p0_batch2` ×1 (N3),
  `test_p0_defect_fixes` ×1 (defect4), `test_persistence` ×3,
  `test_tinkoff_client` ×3 (max_lots), `test_trading_flag` ×3.

## 5. Краткий перечень изменений (относительно снимка FIX 2)

**В `main.py` — только добавления (77 строк), ни одной удалённой или
изменённой строки FIX 1 / FIX 2 (проверено diff):**

1. Строки **250–270** — changelog-блок «ИЗМЕНЕНИЯ (этап BUY REGISTRATION
   FIX 3 …)» (комментарий-документация).
2. Строки **1284–1339** — guard в `register_executed_buy()` внутри ветки
   `if monitor.has_active_position(figi):`, размещён ДО обеих веток:
   - **1310** `if not str(order_id or "").strip():` — срабатывает при
     `order_id=None`, `""`, `"   "` (любое значение без непробельных
     символов);
   - **1311** `client.uncertain_state = True` — новые BUY запрещены
     глобально до устранения неопределённости;
   - **1312–1327** `research_logger.log_event("BUY_EXEC_NO_ORDER_ID", …)` —
     диагностика в `research_events.jsonl` (append-only): figi, ticker,
     trade_id, `order_id_raw` (причина отсутствия ID), executed/requested
     лоты, состояние позиции (state, entry_order_id, entry_applied_lots,
     quantity), source, note;
   - **1328–1337** `logging.error` с полной диагностикой;
   - **1338** `_save_state_checked(monitor, rm)` — сохранение состояния;
   - **1339** `return existing` — лоты НЕ применены (quantity/entry_price/
     entry_applied_lots не изменены), успешный маркер регистрации НЕ
     пишется, фиктивный order_id НЕ создаётся (в guard-ветке реестр не
     вызывается вовсе — `record_uncertain()` адресуется order_id).
   Guard стоит до RECOVERY-привязки (1349) и до веток
   `record_recovery_entry_lots()` (1453) и `apply_late_entry_lots()` (1563)
   — проверка отсутствующего order_id выполняется до вызова ОБОИХ методов,
   для ОБЕИХ веток: OPEN и RECOVERY.

**Другие файлы не менялись вовсе** (см. п.6).

## 6. Подтверждение: остальные файлы не менялись

- `execution.py`, `order_ledger.py`, `close_all_positions.py` — SHA256
  совпадают со снимком FIX 2 и значениями отчёта FIX 3 (таблица в п.2);
  `close_all_positions.py` дополнительно `git diff HEAD` — 0 строк.
- Тесты (`tests/test_buy_registration_fix2.py`,
  `tests/test_buy_registration_fixes.py`) — не менялись (SHA256 = значению
  отчёта FIX 3).
- `research_events.jsonl` — **append-only подтверждён побайтово**: первые
  4305 строк (финальное состояние на отчёт FIX 3) имеют SHA256
  `cdf17ae96de53a30…` — идентичен зафиксированному; текущий файл 4375
  строк (+70 — только аппенды событий тестовых прогонов TG_INCOMING/
  RECONCILE_OK/TRADING_START и т.п., timestamp последнего 17:16:21 —
  прогон pytest). Ни одна строка не удалена, не изменена, файл не
  обрезался и не перезаписывался.
- `state.json` — моей сессией не трогался (mtime до начала сессии; тесты
  изолируют STATE_FILE в tmp).
- Торговая стратегия, фильтры, риск-менеджмент, параметры торговли — не
  изменялись (в diff только guard + комментарий).
- Робот не запускался, T-Invest API не вызывался, заявки не отправлялись
  и не отменялись, торговых операций нет, `git commit`/`git push` не
  выполнялись (HEAD — `afa18f0`, как до сессии).

## 7. Проверка «FIX 3 закрывает обе ветки: OPEN и RECOVERY»

**Структурно:** единственная точка встречи исполнения с активной позицией —
`register_executed_buy()`; guard (1310) стоит до веток RECOVERY (1422+) и
OPEN (1507+). Оба целевых метода вызываются только в защищённых местах:
`apply_late_entry_lots()` — строка 1563 (после guard), 
`record_recovery_entry_lots()` — строка 1453 (после guard) и строка 2279
(`promote_recovery_positions`, вызывается только при непустом
`pos.entry_order_id` — проверка на строке 2193, ID берётся из собственного
хранения позиции, не из внешнего отчёта).

**Эмпирически** (`scripts/verify_fix3_recovery_branch.py`, вне репозитория,
файлы репозитория не менялись): RECOVERY-позиция + `order_id=None` — 18/18
проверок OK (позиция/счётчики/привязка/state не изменены,
`uncertain_state=True`, `is_uncertain()=True`, реестр не тронут,
`BUY_EXEC_NO_ORDER_ID` с `position_state=RECOVERY`, повтор идемпотентен);
OPEN-позиция + `order_id=None` — 7/7 OK (зеркально, `position_state=OPEN`).

## 8. Критерий принятия

**Ни одно исполнение BUY без достоверного `order_id` не может увеличить
количество уже открытой позиции** — доказано H1–H4 + скриптом п.7 для обеих
веток (OPEN и RECOVERY). Штатная регистрация с корректным собственным
`order_id` работает — H5. Защита от чужого `order_id` (FIX 2) действует —
H6. FIX 1 и FIX 2 сохранены без регрессий (diff: только добавления; тесты
FIX 1: 21 passed, FIX 2: 7 passed). Несовместимостей FIX 1/2/3 не
обнаружено.

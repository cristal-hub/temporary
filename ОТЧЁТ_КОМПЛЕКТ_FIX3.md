# ОТЧЁТ — ФИНАЛЬНЫЙ СОГЛАСОВАННЫЙ КОМПЛЕКТ FIX 3 ДЛЯ SAMSUNG

Репозиторий-источник: `https://github.com/cristal-hub/temporary.git`, ветка
`main`, рабочее дерево `/home/z/my-project/repo/`. Целевой проект переноса:
`Tsclp_v2.1_volatile_Samsung`. Режим: только код + unit-тесты на локальных
заглушках (`t_tech` stub, FakeClient) — без сети, без T-Invest API, без
заявок, без запуска робота, без commit/push.

**Комплект готов: несовместимостей не обнаружено, все тесты проходят.**

---

## 1. Полные файлы комплекта

Папка переноса: `download/samsung_fix3_package/`
(`main.py`, `execution.py`, `tests/test_buy_registration_fix2.py`,
`SHA256SUMS.txt`). Те же файлы обновлены в корне `download/`.

| Файл | Строк | SHA256 |
|---|---|---|
| `main.py` (FIX 1+2+3) | 3158 | `9f3ad72c3aa6fd4733f68cf0d7ff5e667f40c1b6db680dcd003e7cdd76df6dd5` |
| `execution.py` (FIX 1+2+3, защита в глубину) | 583 | `409e8b0c05e29e6e798ab0888cb49120b13687bf0765531d8e3907fdd50f754e` |
| `tests/test_buy_registration_fix2.py` (A–G + H1–H6 + I1–I5) | 977 | `12526db78b5213a7205971113567bc34b545651cdef4a850fc7e764d11a54a3d` |

`python -m py_compile` — успешно для всех трёх файлов.

## 2. Таблица изменённых файлов (этап «согласованный комплект»)

| Файл | Изменение | Причина |
|---|---|---|
| `execution.py` | **+55 строк, 0 удалений** относительно FIX 2-снимка: changelog (28–51), guard `record_recovery_entry_lots` (248–262), guard `apply_late_entry_lots` (292–307) | ТЗ п.2: отклонение пустого/missing `order_id` на уровне самого модуля (защита в глубину) |
| `tests/test_buy_registration_fix2.py` | **+216 строк**: импорты (`Decimal`, `PositionData`), helper `_journal_all_safe`, `_make_recovery_b`, тесты I1–I5 | ТЗ п.3: прямые вызовы execution.py, RECOVERY-ветка, перезапуск, ошибка сохранения |
| `main.py` | **не менялся** (SHA256 = предыдущей поставке FIX 3) | FIX 1+2+3 уже в файле; проверка п.5 ТЗ выполнена |
| `order_ledger.py` | **не менялся** — необходимости нет: неопределённость при отсутствии order_id фиксируется `client.uncertain_state` + диагностическим событием (фиктивный ID в реестр писать запрещено, поэтому `record_uncertain()` не вызывается — как и согласовано на этапе FIX 3); совместимость реестра не нарушена | ТЗ п.1 |
| `close_all_positions.py` | **не менялся** (SHA256 `a0864e24…` = снимку FIX 2, `git diff HEAD` — 0 строк) | запрет ТЗ п.4 |
| `research_events.jsonl` | append-only подтверждён побайтово: первые 4305 строк = `cdf17ae96de53a30…` (зафиксированный префикс), текущий файл 4445 строк — только аппенды тестовых прогонов | запрет ТЗ п.4 |

Снимки предыдущих поставок сохранены для трассировки:
`download/execution_FIX2_snapshot.py` (`2235c425…`),
`download/test_buy_registration_fix2_FIX2_snapshot.py` (A–G),
`download/main_FIX2_snapshot.py` (`c546af2e…`).

## 3. Изменения `execution.py` (детально)

1. **`apply_late_entry_lots()` (строки 292–307)**: после проверки
   state/direction добавлен guard
   `if not str(order_id or "").strip(): return False` — исполнение с
   `order_id=None` / `""` / пробелами отклоняется целиком, позиция не
   изменяется (quantity/entry_price/entry_applied_lots/entry_order_id не
   трогаются); запись `logging.error` с диагностикой (figi, ticker,
   entry_order_id, executed_lots).
2. **`record_recovery_entry_lots()` (строки 248–262)**: идентичный guard.
3. **Чужой `order_id`**: отклоняется прежней проверкой
   `if order_id and pos.entry_order_id and order_id != pos.entry_order_id:
   return False` (обе ветки сохранены).
4. **Собственный корректный `order_id`**: штатное применение дельты —
   без изменений.
5. **Совместимость** (проверено программно по FIX 2-снимку): 20 методов
   ExecutionMonitor — 0 потеряно, сигнатуры (параметры/порядок/дефолты)
   совпадают все; TrackedPosition — 33 поля, совпадают; PositionState —
   совпадает. Дифф против FIX 2-снимка: 0 удалений.
6. Сигнатуры методов НЕ менялись — все текущие вызовы совместимы:
   `main.py` 1453/1563 (после guard `BUY_EXEC_NO_ORDER_ID`, order_id
   непустой), `main.py` 2279 (`pos.entry_order_id` непустой — проверка
   2193), прямые вызовы в `tests/test_p0_batch2_late_execution.py` (4
   вызова с непустыми собственными ID — проходят).

## 4. Результаты тестов

Команда запуска (из корня репозитория):
`PYTHONPATH=<stubs> python -m pytest <цель> -q`.

### 4.1 Обязательные тесты ТЗ п.3

| Пункт ТЗ | Тест | Результат |
|---|---|---|
| H1: OPEN, `order_id=None` | `test_h1_order_id_none_not_applied` | **passed** |
| H2: OPEN, пустая строка | `test_h2_order_id_empty_not_applied` | **passed** |
| H3: OPEN, пробелы | `test_h3_order_id_blank_not_applied` | **passed** |
| H4: повтор события без ID | `test_h4_repeat_no_order_id_idempotent` | **passed** |
| H5: собственный ID — штатная дельта | `test_h5_matching_order_id_still_applies` | **passed** |
| H6: чужой ID — лоты не применяются | `test_h6_foreign_order_still_blocked` | **passed** |
| Прямой вызов `apply_late_entry_lots` без ID (+свой/чужой) | `test_i1_execution_apply_late_rejects_missing_order_id` | **passed** |
| Прямой вызов `record_recovery_entry_lots` без ID (+свой/чужой) | `test_i2_execution_record_recovery_rejects_missing_order_id` | **passed** |
| Аналогичные проверки для RECOVERY (main-путь, None/""/пробелы + повтор) | `test_i3_recovery_branch_no_order_id_not_applied` | **passed** |
| Сохранение `uncertain_state` после перезапуска | `test_i4_uncertain_state_survives_restart` | **passed** |
| Ошибка `_save_state_checked()` — без ложного подтверждения | `test_i5_save_failure_no_false_confirmation` | **passed** |
| Идемпотентность повторных событий | H4 + H5-повтор + I3-повтор (×3 значения) | **passed** |

Что доказывает I4 (перезапуск): guard сохранил state.json; после
`_load_state` в НОВЫЙ монитор позиция восстановлена с
quantity=3/entry_applied_lots=3 (неприменённые лоты НЕ попали в сохранённое
состояние); повтор события без order_id после перезапуска снова включает
`uncertain_state=True`; при расхождении снимка брокера (5 лот. у брокера
против 3 в учёте) сверка `reconcile()` снова фиксирует неопределённость
(`uncertain_state=True`, `reconcile_done=False`) и НЕ раздувает учёт.

Что доказывает I5 (ошибка сохранения): при `_save_state()=False` —
`state_save_failed=True`, событие `STATE_SAVE_FAILED` в диагностике,
реестр без новых записей (успешного маркера регистрации нет),
Telegram-уведомлений НЕ отправлено вовсе (`sent == []`) — ложного
подтверждения сохранения/регистрации нет.

### 4.2 Регрессия FIX 1 / FIX 2

- `tests/test_buy_registration_fixes.py` (FIX 1): **21 passed**.
- Тесты FIX 2 A–G (в `test_buy_registration_fix2.py`): **7 passed**.
- Прямые вызовы `record_recovery_entry_lots` в
  `tests/test_p0_batch2_late_execution.py` (P0-4): **passed** (в полном
  прогоне).

### 4.3 Полный набор и сравнение с baseline

| Прогон | Результат | Дельта |
|---|---|---|
| Baseline FIX 1+2 (воспроизведён в изолированной копии, снимок FIX 2) | 283 passed / 15 failed | — |
| FIX 3 (предыдущая поставка main.py) | 289 passed / 15 failed | +6 = H1–H6 |
| **Финальный комплект (этот этап)** | **294 passed / 15 failed** | +5 = I1–I5 |
| Legacy-файлы корня `test_execution.py test_order_ledger.py` (автономно) | 20 passed | — |

Списки 15 падений выгружены и сравнены построчно по имени теста между
baseline-копией и финальным комплектом: **множества идентичны** (`diff`
пуст). Все 15 — ранее известные (test_execution ×3 resolve_closing,
test_main trading_cycle, test_p0_batch2 N3, test_p0_defect_fixes defect4,
test_persistence ×3, test_tinkoff_client ×3 max_lots, test_trading_flag
×3). **Новых регрессий нет.** Особенность репозитория (не этого этапа):
запуск всего корня даёт 9 ошибок collection из-за legacy-файла
`test_execution.py` в корне (подмена модуля `config` в sys.modules) —
воспроизводится и на снимке FIX 2, поэтому полный набор измеряется по
пакету `tests/` (тот же скоуп, что и baseline).

## 5. Подтверждение: стратегия и торговые параметры не менялись

- `main.py` — не менялся на этом этапе вовсе (SHA256 совпадает с
  предыдущей поставкой FIX 3); стратегия (MA 9/21), фильтры, сигналы,
  расчёт SL/TP (`calculate_sl_tp`), риск-менеджмент, параметры торговли,
  порядок вызовов trading_cycle/reconcile/poll_open_orders — без
  изменений.
- `execution.py` — только два guard'а отклонения + комментарий; торговая
  логика (SL/TP, закрытия, промоция, sync_recovery_quantity,
  apply_close_execution) не тронута (дифф: 0 удалений, +55 добавлений,
  все — проверка идентификатора, лог и комментарии).
- `close_all_positions.py` — не менялся.

## 6. Подтверждение отсутствия API и торговых операций

- Робот не запускался (`main()`/`trading_cycle` в живом режиме не
  выполнялись).
- Обращений к T-Invest API нет: тесты — на заглушке `t_tech` (только
  перечисления SDK) и FakeClient, сетевых вызовов нет.
- Заявки не отправлялись и не отменялись; торговых операций (реальных и
  sandbox) нет.
- `git commit`/`git push` не выполнялись (HEAD `afa18f0` — как до сессии;
  изменения только в рабочем дереве).

## 7. Критерий принятия

Ни одно исполнение BUY без достоверного `order_id` не может увеличить
количество уже открытой позиции — теперь проверено на ДВУХ уровнях:
уровень вызывающего кода (`main.py`, guard `BUY_EXEC_NO_ORDER_ID`:
неопределённость, блокировка новых BUY, диагностика, сохранение состояния,
без фиктивных ID и без ложных уведомлений) и уровень самого модуля
(`execution.py`: физическое отклонение исполнения без идентификатора).
Штатная регистрация собственного `order_id`, защита от чужого `order_id`
и все прежние защиты FIX 1 / FIX 2 работают (раздел 4.2).

# План внедрения: Непрерывные итерации при User Feedback

## Анализ проблемы

Текущая архитектура имеет **два пути** обработки пользовательского фидбека:

1. **`run_feedback_cycle`** (вызывается из меню подтверждения — `main.py:6222`, `main.py:6335`, и из `_get_user_custom_critique` — `main.py:2543`):
   - ✅ Не очищает VFS (сохраняет staged-файлы)
   - ✅ Использует существующий `feedback_loop`
   - ✅ Проходит полный цикл валидации (staging correction → technical validation → AI validation → tests)
   - ❌ **НЕ сбрасывает лимиты итераций** — `can_revise_instruction()` проверяет `orchestrator_revisions < max_orchestrator_revisions`, а счётчик `orchestrator_revisions` накапливается со времени `process_request`. После исчерпания лимита в `process_request`, `run_feedback_cycle` немедленно падает с "Max revisions reached".

2. **`handle_user_feedback`** (вызывается из flow отклонения AI-валидатором — `main.py:2428`, `main.py:2484`, `main.py:2499`):
   - ❌ **Очищает VFS** через `self.vfs.discard_all()` — уничтожает ранее сгенерированный код
   - ❌ Запускает **одну итерацию** без полного цикла (нет staging correction loop, нет AI validation, нет многоуровневой проверки)
   - ❌ Отрывный от основного пайплайна процесс

## Файлы для модификации

### 1. `app/agents/feedback_loop.py` — `FeedbackLoopState`

**Добавить метод `reset_iteration_limits()`:**

```python
def reset_iteration_limits(self) -> None:
    """Reset iteration counters for a new user feedback cycle.
    
    Preserves all history records (validation_history, revision_history, 
    test_run_history, feedback_history) but resets the counters so that
    each user feedback gets a fresh set of iterations.
    """
    self.orchestrator_revisions = 0
    self.validator_attempts = 0
    self.test_runs = 0
    logger.info(
        f"FeedbackLoop: Iteration limits reset "
        f"(preserved {len(self.revision_history)} revisions, "
        f"{len(self.validation_history)} validations in history)"
    )
```

**Контракт:** Метод только обнуляет счётчики (`orchestrator_revisions`, `validator_attempts`, `test_runs`), но НЕ трогает списки истории (`revision_history`, `validation_history`, `test_run_history`, `feedback_history`). Это обеспечивает:
- `can_revise_instruction()` → `True` после сброса
- `can_retry_validation()` → `True` после сброса
- `can_run_tests()` → `True` после сброса
- История сохраняется для логирования и трейсинга

---

### 2. `app/agents/agent_pipeline.py` — метод `run_feedback_cycle`

**Изменение: Сброс лимитов в начале цикла**

После блока добавления user feedback в `feedback_loop` (после `self.feedback_loop.add_user_feedback(user_feedback, replaces_validator=True)`), ДО начала основного цикла `while iteration < max_iterations:`, добавить:

```python
# === CRITICAL: Reset iteration limits for each user feedback cycle ===
# Each user feedback gets a fresh set of iterations (обнуление лимитов)
self.feedback_loop.reset_iteration_limits()
logger.info("run_feedback_cycle: Iteration limits reset for new feedback cycle")
```

**Удалить проверку `can_revise_instruction()` в начале цикла**, которая сейчас блокирует выполнение при накопленных лимитах:

```python
# УДАЛИТЬ этот блок:
if not self.feedback_loop.can_revise_instruction():
    logger.warning(f"run_feedback_cycle: Max revisions reached")
    result.status = PipelineStatus.FAILED
    result.errors.append("Max revision iterations reached")
    result.duration_ms = (time.time() - start_time) * 1000
    return result
```

**Заменить на проверку внутри цикла** (после `iteration += 1`), использующую локальный счётчик `iteration` вместо `can_revise_instruction()`:

```python
# Check local iteration limit (reset for each user feedback)
if iteration > max_iterations:
    logger.warning(f"run_feedback_cycle: Max iterations ({max_iterations}) reached")
    result.status = PipelineStatus.FAILED
    result.errors.append(f"Max iterations ({max_iterations}) reached without success")
    result.duration_ms = (time.time() - start_time) * 1000
    return result
```

**Сохранить** вызовы `record_orchestrator_revision()` внутри цикла — они записывают историю, но после `reset_iteration_limits()` счётчик начинается с 0.

**Что НЕ меняется:**
- VFS не очищается (уже правильно)
- Полный цикл валидации (staging correction → technical → AI → tests) сохраняется
- Параметр `tester_report` сохраняется
- Все `_notify_stage` вызовы сохраняются
- Логика partial staging сохраняется

---

### 3. `app/agents/agent_pipeline.py` — метод `handle_user_feedback`

**Рефакторинг действий "accept" и "replace" — делегирование в `run_feedback_cycle`**

Текущая реализация `handle_user_feedback` для действий "accept" и "replace":
- Очищает VFS (`self.vfs.discard_all()`) — **УДАЛИТЬ**
- Запускает одну итерацию без staging correction loop, без AI validation, без многоуровневой проверки — **ЗАМЕНИТЬ** на вызов `run_feedback_cycle`

#### Действие "accept" (пользователь согласен с критикой валидатора):

```python
elif action == "accept":
    # User accepts validator critique — delegate to run_feedback_cycle
    logger.info("User accepted validator critique")
    
    # Get validator feedback formatted as user feedback text
    feedback = self.feedback_loop.get_feedback_for_orchestrator()
    validator_text = feedback.get("validator_feedback", "")
    
    # Use validator's critique as the "user feedback" for run_feedback_cycle
    user_feedback_text = f"[VALIDATOR FEEDBACK - ACCEPTED BY USER]\n{validator_text}" if validator_text else "Please fix the issues found by the AI Validator."
    
    # Delegate to run_feedback_cycle — preserves VFS, full validation, reset limits
    return await self.run_feedback_cycle(
        user_feedback=user_feedback_text,
        history=history or [],
    )
```

#### Действие "replace" (пользователь пишет свою критику):

```python
elif action == "replace" and user_message:
    # User provides custom critique — delegate to run_feedback_cycle
    logger.info("User replacing validator critique with custom feedback")
    
    # Delegate to run_feedback_cycle — preserves VFS, full validation, reset limits
    return await self.run_feedback_cycle(
        user_feedback=user_message,
        history=history or [],
    )
```

#### Действие "override" — БЕЗ ИЗМЕНЕНИЙ:

```python
elif action == "override":
    # User rejects validator critique - proceed to tests
    # KEEP AS-IS: does NOT clear VFS, runs validation with tests
    logger.info("User overrode validator - proceeding to tests")
    self.feedback_loop.user_overrides_count += 1
    # ... existing code unchanged ...
```

#### Действие "cancel" — БЕЗ ИЗМЕНЕНИЙ:

```python
if action == "cancel":
    await self.discard_pending_changes()
    # ... existing code unchanged ...
```

**Контракты, соблюдаемые при рефакторинге:**
- `run_feedback_cycle` возвращает `PipelineResult` — тот же тип, что и `handle_user_feedback`
- `run_feedback_cycle` принимает `user_feedback: str` и `history: List[Dict]` — совместимо
- `run_feedback_cycle` НЕ очищает VFS — ранее сгенерированный код сохраняется
- `run_feedback_cycle` сбрасывает лимиты (после изменения в п.2)
- `run_feedback_cycle` проходит полный цикл валидации

---

### 4. `main.py` — БЕЗ ИЗМЕНЕНИЙ

Текущая логика в `main.py` уже корректна:

- **Меню подтверждения** (`while True` loop, line 6079): После `run_feedback_cycle` выполняется `result = new_result` → `continue` → возврат в начало цикла → повторное отображение staged-файлов, анализа, результатов валидации и меню. **Финальные результаты отображаются корректно.**

- **AI validator rejection flow** (lines 2420-2446): Вызывает `handle_user_feedback(action="accept"/"override"/"cancel")` или `_get_user_custom_critique` → `run_feedback_cycle`. После рефакторинга `handle_user_feedback` будет делегировать в `run_feedback_cycle`, возвращая `PipelineResult`. **Вызовы в main.py не требуют изменений.**

- **Tester integration** (lines 6238-6361): После отчёта тестировщика, выбор "2" (Доработать) вызывает `run_feedback_cycle` с `tester_report`. **Не затрагивается.**

- **Бесконечный цикл доработок**: Пользователь может выбирать "2" (Доработать) неограниченное количество раз — `while True` loop это обеспечивает. Каждый вызов `run_feedback_cycle` сбрасывает лимиты. **Уже работает.**

---

## Обоснование

### Почему это решает проблему:

1. **"Отрывный от основного пайплайна процесс"** — `handle_user_feedback` теперь делегирует в `run_feedback_cycle`, который использует тот же `feedback_loop`, ту же VFS, тот же `session_id`. Процесс становится непрерывным.

2. **"Ранее сгенерированный код не должен удаляться"** — `run_feedback_cycle` НЕ очищает VFS (уже реализовано). `handle_user_feedback` больше не вызывает `self.vfs.discard_all()`. Код остаётся в VFS, но не применяется к дискам до `apply_pending_changes()`.

3. **"Не применяться к файлам на диске"** — VFS staging ≠ disk write. `apply_pending_changes()` вызывается только при выборе "1" (Применить) в меню подтверждения. До этого момента изменения существуют только в VFS.

4. **"Финальные результаты отображаются"** — После `run_feedback_cycle` → `result = new_result` → `continue` → повторное отображение всех staged-файлов, diff-ов, результатов валидации и runtime-тестов в начале `while True` loop.

5. **"Пользователь может дорабатывать код столько раз, сколько захочет"** — `while True` loop в `main.py:6079` обеспечивает бесконечный цикл. Каждая доработка → `run_feedback_cycle` → `continue` → новое меню.

6. **"Каждый user feedback обнуляет лимиты"** — `reset_iteration_limits()` в начале `run_feedback_cycle` сбрасывает `orchestrator_revisions`, `validator_attempts`, `test_runs` в 0. Каждый user feedback получает свежий набор из `MAX_FEEDBACK_ITERATIONS` (10) итераций.

7. **"Не уменьшать функциональность"** — Все существующие пути сохранены:
   - `process_request` — без изменений
   - `run_feedback_cycle` — только добавляется reset лимитов и убирается блокирующая проверка
   - `handle_user_feedback` — "accept"/"replace" делегируют в `run_feedback_cycle` (полный цикл вместо одной итерации — это УВЕЛИЧЕНИЕ функциональности)
   - `handle_user_feedback` "override"/"cancel" — без изменений
   - Tester integration — без изменений

8. **"Взаимодействие с Тестировщиком не должно ухудшиться"** — `run_feedback_cycle` уже принимает `tester_report` параметр. Post-tester меню (выбор "2") вызывает `run_feedback_cycle` с `tester_report=report.original_report`. После рефакторинга `handle_user_feedback` также может принимать `tester_report` (через `run_feedback_cycle`). Тестировщик запускается через `run_tester()` — без изменений.

### Контракты, соблюдаемые при изменениях:

| Контракт | Файл | Статус |
|----------|------|--------|
| `FeedbackLoopState.can_revise_instruction()` | feedback_loop.py | Сохраняется, но счётчик сбрасывается |
| `FeedbackLoopState.record_orchestrator_revision()` | feedback_loop.py | Сохраняется, история не теряется |
| `FeedbackHandler.add_user_feedback()` | feedback_handler.py | Без изменений |
| `AgentPipeline.run_feedback_cycle()` сигнатура | agent_pipeline.py | Без изменений |
| `AgentPipeline.handle_user_feedback()` сигнатура | agent_pipeline.py | Без изменений |
| `AgentPipeline.run_tester()` | agent_pipeline.py | Без изменений |
| `VirtualFileSystem` — staging без commit | virtual_fs.py | Без изменений |
| `PipelineResult` тип возврата | agent_pipeline.py | Без изменений |
| `main.py` confirmation loop | main.py | Без изменений |
| `main.py` tester post-report menu | main.py | Без изменений |
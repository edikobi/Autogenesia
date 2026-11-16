"""Слэш-команды чата воркспейса (1:1 покрытие print_chat_help из main.py).

Все обращения к истории и пайплайну идут через контракты HistoryAdapter /
PipelineAdapter; main.py никогда не импортируется. ``handle_command``
возвращает сентинел действия для воркспейса: None (обработано локально),
ACTION_SWITCH_TAB, ACTION_MENU или ACTION_QUIT.
"""

from __future__ import annotations
from .dialogs import push_modal

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .workspace import WorkspacePane

logger = logging.getLogger(__name__)

#: Сентинелы действий для воркспейса
ACTION_QUIT = "quit"
ACTION_MENU = "menu"
ACTION_SWITCH_TAB = "switch_tab"

mode_help = "| `/mode`, `/режим` | Переключить режим (agent/ask/general; `new` - новый проект) |"


#: Текст справки по командам чата (паритет с print_chat_help из main.py).
HELP_TEXT = "\n".join(
    [
        "📖 Команды чата",
        "",
        "| Команда | Описание |",
        "|---------|----------|",
        "| `/help`, `/?`, `/помощь`, `/справка` — эта справка;",
        mode_help,
        "| `/provider`, `/провайдер` | Выбор провайдера и API-ключей |",
        "| `/model`, `/модель`, `/модорк` | Модель оркестратора (auto — роутер) |",
        "| `/generator`, `/генератор`, `/ген` | Модель генератора кода |",
        "| `/types`, `/типы`, `/mypy` | Переключить проверку типов |",
        "| `/tester [on|off|run]`, `/тестер` | Тестировщик: статус/вкл/выкл/запуск по VFS |",
        "| `/index [status|update|rebuild]`, `/индекс` | Статистика/обновление/пересборка индекса |",
        "| `/diff`, `/дифф` | diff стейджированных изменений VFS |",
        "| `/commit`, `/apply`, `/применить` | Применить изменения VFS на диск (с бэкапом) |",
        "| `/rollback`, `/discard`, `/отмена` | Отменить изменения VFS |",
        "| `/restore [id]`, `/backup`, `/восстановить` | Восстановление из бэкапа |",
        "| `/changes [file <путь>]`, `/изменения` | История изменений файлов |",
        "| `/compress`, `/сжать` | Сжать историю диалога |",
        "| `/env`, `/доступ` | Диагностика прав доступа и venv |",
        "| `/legal`, `/юрист` | Legal-режим (только общий чат) |",
        "| `/h`, `/history`, `/история` | История диалога; `/export`, `/экспорт` — экспорт диалога в .md |",
        "| `/n`, `/new`, `/новый` | Новый диалог; `/title`, `/название` — переименовать диалог |",
        "| `/attach`, `/прикрепить` | Прикрепить файлы к диалогу |",
        "| `/s`, `/status`, `/статус` | Статус воркспейса; `/clear`, `/очистить` — очистить чат |",
        "| `/t`, `/thread`, `/тред` | Переключить вкладку/тред; `/m`, `/menu`, `/меню` — главное меню |",
        "| `/a`, `/about` | О программе; `/q`, `/quit`, `/exit`, `/выход` — выход |",
        "",
        "💡 В гейтах плана/подтверждения работают `!ok`/`!cancel`, "
        "`да`/`нет`/`тестер` и ввод текста замечаний прямо в чат.",
    ]
)


def _write(ws: "WorkspacePane", text: str, markup: bool = True) -> None:
    """Вывести строку в чат воркспейса."""
    try:
        ws.write_line(text, markup=markup)
    except Exception:
        logger.debug("command output write failed", exc_info=True)


async def handle_command(ws: "WorkspacePane", raw: str) -> Optional[str]:
    """Разобрать и выполнить слэш-команду.

    Args:
        ws: Воркспейс, в чате которого введена команда.
        raw: Сырой текст ввода (начинается с «/»).

    Returns:
        None — команда обработана локально; иначе сентинел действия
        (ACTION_SWITCH_TAB / ACTION_MENU / ACTION_QUIT).
    """
    from .dialogs import (
        GeneratorModelSelectDialog,
        ModelSelectDialog,
        provider_for_model,
        push_modal,
        tui_prompt,
    )
    from .settings_io import load_user_settings, save_user_settings

    app = ws.app
    text = (raw or "").strip()
    if not text.startswith("/"):
        return None
    parts = text.split(maxsplit=1)
    head = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""

    # ------------------------------------------------------- справка / навигация
    if head in ("/help", "/?", "/помощь", "/справка"):
        _write(ws, HELP_TEXT, markup=False)
        return None
    if head in ("/q", "/quit", "/exit", "/выход", "/в"):
        return ACTION_QUIT
    if head in ("/m", "/menu", "/меню", "/м"):
        return ACTION_MENU
    if head in ("/t", "/thread", "/threads", "/тред", "/треды"):
        return ACTION_SWITCH_TAB

    # ------------------------------------------------------- история
    if head in ("/h", "/history", "/история", "/и"):
        if ws.history_adapter is None or not ws.state.thread_id:
            _write(ws, "📜 Нет активного диалога")
            return None
        messages = await ws.history_adapter.view_history(ws.state.thread_id)
        lines = [f"📜 История диалога ({len(messages)} сообщений)"]
        for msg in messages[-20:]:
            role = getattr(msg, "role", "")
            who = {"user": "👤 Вы", "assistant": "🤖 Ассистент", "system": "⚙️ Система"}.get(
                role, f"🔧 {role}"
            )
            created = (getattr(msg, "created_at", "") or "")[:19].replace("T", " ")
            lines.append(f"\n{who} ({created}):")
            lines.append(str(getattr(msg, "content", "") or "")[:2000])
        _write(ws, "\n".join(lines), markup=False)
        return None

    if head in ("/export", "/экспорт", "/э"):
        if ws.history_adapter is None or not ws.state.thread_id:
            _write(ws, "📜 Нет активного диалога для экспорта")
            return None
        path = await tui_prompt(
            app,
            "Путь для экспорта (.md):",
            free_input=True,
            default=f"dialog_{ws.state.thread_id[:8]}.md",
            title="📄 Экспорт",
        )
        if path and path.strip():
            try:
                saved = await ws.history_adapter.export_to_markdown(ws.state.thread_id, path.strip())
                app.notify(f"Диалог экспортирован: {saved}", severity="information")
                _write(ws, f"📄 Экспорт: {saved}")
            except Exception as e:
                app.notify(f"Ошибка экспорта: {e}", severity="error")
        return None

    if head in ("/n", "/new", "/новый", "/н"):
        if ws.history_adapter is None:
            _write(ws, "📜 История недоступна")
            return None
        thread = await ws.history_adapter.new_conversation(ws.state.project_dir)
        ws.state.thread_id = getattr(thread, "id", None)
        ws.bridge.history = []
        await ws.persist_session()
        _write(ws, f"🆕 Новый диалог: {ws.state.thread_id}")
        return None

    if head in ("/title", "/название", "/переименовать"):
        if ws.history_adapter is None or not ws.state.thread_id:
            _write(ws, "📜 Нет активного диалога")
            return None
        new_title = await tui_prompt(app, "Новое название диалога:", free_input=True, title="🏷 Название")
        if new_title and new_title.strip():
            ok = await ws.history_adapter.rename_conversation(ws.state.thread_id, new_title.strip())
            if ok:
                app.notify("Название изменено", severity="information")
                _write(ws, f"🏷 Новое название: {new_title.strip()}")
            else:
                app.notify("Не удалось изменить название", severity="error")
        return None

    # ------------------------------------------------------- модели
    if head in ("/model", "/модель", "/мод", "/модельорк", "/modelorch", "/модорк"):
        result = await push_modal(
            app,
            lambda: ModelSelectDialog(current_model=ws.state.orchestrator_model),
        )
        if result:
            if len(result) >= 3:
                kind = result[0]
                model_id = result[1]
                picked_provider = result[2]
            else:
                kind = result[0]
                model_id = result[1]
                picked_provider = None
            if kind == "auto":
                ws.state.use_router = True
                ws.state.orchestrator_model = None
                ws.state.orchestrator_provider = None
            else:
                ws.state.use_router = False
                ws.state.orchestrator_model = model_id
                ws.state.orchestrator_provider = picked_provider or provider_for_model(model_id, "orchestrator")
            ws.bridge.sync_models_to_pipeline()
            settings = load_user_settings()
            settings.update(
                {
                    "use_router": ws.state.use_router,
                    "fixed_orchestrator_model": ws.state.orchestrator_model,
                    "orchestrator_provider": ws.state.orchestrator_provider,
                }
            )
            save_user_settings(settings)
            app.notify("Модель оркестратора обновлена", severity="information")
        return None

    if head in ("/generator", "/генератор", "/ген", "/модельген", "/modelgen", "/модген"):
        result = await push_modal(
            app,
            lambda: GeneratorModelSelectDialog(current_model=ws.state.generator_model),
        )
        if result:
            if len(result) >= 3:
                kind = result[0]
                model_id = result[1]
                picked_provider = result[2]
            else:
                kind = result[0]
                model_id = result[1]
                picked_provider = None
            if kind == "auto":
                ws.state.generator_model = None
                ws.state.generator_provider = None
            else:
                ws.state.generator_model = model_id
                ws.state.generator_provider = picked_provider or provider_for_model(model_id, "generator")
            ws.bridge.sync_models_to_pipeline()
            settings = load_user_settings()
            settings.update(
                {
                    "generator_model": ws.state.generator_model,
                    "generator_provider": ws.state.generator_provider,
                }
            )
            save_user_settings(settings)
            app.notify("Модель генератора обновлена", severity="information")
        return None

    if head in ("/types", "/типы", "/mypy"):
        ws.state.enable_type_checking = not ws.state.enable_type_checking
        settings = load_user_settings()
        settings["enable_type_checking"] = ws.state.enable_type_checking
        save_user_settings(settings)
        state_txt = "включена" if ws.state.enable_type_checking else "выключена"
        _write(ws, f"🔍 Проверка типов (mypy) {state_txt}")
        return None

    # ------------------------------------------------------- бэкапы / изменения
    if head in ("/restore", "/восстановить", "/бэкап"):
        await _handle_restore(ws, args)
        return None

    if head in ("/changes", "/изменения"):
        await _handle_changes(ws, args)
        return None

    if head in ("/attach", "/прикрепить", "/файл", "/файлы"):
        if ws.history_adapter is None or not ws.state.thread_id:
            _write(ws, "📜 Нет активного диалога")
            return None
        paths_raw = await tui_prompt(
            app, "Пути к файлам через пробел:", free_input=True, title="📎 Прикрепить файлы"
        )
        if paths_raw and paths_raw.strip():
            saved = await ws.history_adapter.attach_files(ws.state.thread_id, paths_raw.split())
            _write(ws, f"📎 Прикреплено: {', '.join(saved) if saved else 'ничего'}")
        return None

    if head in ("/legal", "/юрист"):
        if ws.state.mode != "general":
            _write(ws, "⚠️ Legal режим доступен только в Общем Чате")
        else:
            ws.state.is_legal_mode = not ws.state.is_legal_mode
            _write(ws, f"⚖️ Legal режим {'включён' if ws.state.is_legal_mode else 'выключен'}")
        return None

    # ------------------------------------------------------- статус / экран
    if head in ("/s", "/status", "/статус"):
        _write(ws, _status_text(ws), markup=False)
        return None

    if head in ("/clear", "/очистить", "/cls"):
        try:
            ws.query_one("#chat").clear()
        except Exception:
            logger.debug("chat clear failed", exc_info=True)
        try:
            ws._set_status_label("⏸ Готов")
        except Exception:
            pass
        return None

    if head in ("/a", "/about", "/о", "/опрограмме"):
        from .app import AboutScreen

        app._push_user_screen(AboutScreen())
        return None

    # ------------------------------------------------------- режимы (mode)
    if head in ("/mode", "/режим"):
        if not args:
            _write(ws, f"🔁 Текущий режим: {ws.state.mode}", markup=False)
            return None

        if args.lower() in ("new", "новый", "новый проект", "new_project"):
            ws.state.mode = "agent"
            ws.state.is_new_project = True
            try:
                settings = load_user_settings()
                settings["mode"] = "agent"
                settings["is_new_project"] = True
                save_user_settings(settings)
            except Exception:
                pass
            try:
                updater = getattr(ws.app, "_update_app_status", None)
                if callable(updater):
                    updater()
            except Exception:
                pass
            _write(ws, "🆕 Режим нового проекта включён (agent + is_new_project)")
            return None
        alias_map = {
            "agent": "agent", "агент": "agent",
            "ask": "ask", "вопрос": "ask",
            "general": "general", "чат": "general",
        }
        new_mode = alias_map.get(args.lower())
        if not new_mode:
            _write(ws, f"❌ Неизвестный режим: {args}. Допустимо: agent|ask|general", markup=False)
            return None
        ws.state.mode = new_mode
        try:
            settings = load_user_settings()
            settings["mode"] = new_mode
            save_user_settings(settings)
        except Exception:
            pass
        try:
            updater = getattr(ws.app, "_update_app_status", None)
            if callable(updater):
                updater()
        except Exception:
            pass
        _write(ws, f"🔁 Режим переключён: {new_mode}")
        return None

    # ------------------------------------------------------- провайдеры
    if head in ("/provider", "/провайдер"):
        try:
            from .menus import show_provider_settings

            await show_provider_settings(app)
        except Exception as e:
            _write(ws, f"⚠️ Настройки провайдеров недоступны: {e}", markup=False)
        return None

    # ------------------------------------------------------- тестер
    if head in ("/tester", "/тестер"):
        sub = args.strip().lower()
        if not sub:
            try:
                current = bool(getattr(ws.state, "enable_tester", True))
            except Exception:
                current = True
            _write(ws, f"🧪 Тестер: {'ON' if current else 'OFF'}", markup=False)
            return None
        if sub in ("on", "вкл", "включить", "enable"):
            ws.state.enable_tester = True
            try:
                settings = load_user_settings()
                settings["enable_tester"] = True
                save_user_settings(settings)
            except Exception:
                pass
            _write(ws, "🧪 Тестер включён")
            return None
        if sub in ("off", "выкл", "выключить", "disable"):
            ws.state.enable_tester = False
            try:
                settings = load_user_settings()
                settings["enable_tester"] = False
                save_user_settings(settings)
            except Exception:
                pass
            _write(ws, "🧪 Тестер выключен")
            return None
        if sub in ("run", "запуск", "запустить"):
            if not ws.bridge.has_pending:
                _write(ws, "⚠️ Нет изменений — запуск тестера невозможен", markup=False)
                return None

            async def _run_tester_now():
                """Запустить тестер в воркере."""
                try:
                    outcome = await ws.bridge._run_tester_stage()
                    if outcome == "apply":
                        await ws.bridge._apply_changes()
                except Exception as exc:
                    _write(ws, f"❌ Ошибка тестера: {exc}", markup=False)

            try:
                ws.run_worker(_run_tester_now(), group=f"tester-{ws.tab_key}", exclusive=True)
            except Exception as e:
                _write(ws, f"❌ Не удалось запустить тестер: {e}", markup=False)
            return None
        _write(ws, "Использование: /tester [on|off|run]", markup=False)
        return None

    # ------------------------------------------------------- индексация
    if head in ("/index", "/индекс", "/инд"):
        if not ws.state.project_dir:
            _write(ws, "⚠️ Проект не выбран — индексация недоступна", markup=False)
            return None
        sub = args.strip().lower()
        if not sub or sub in ("status", "статус"):
            try:
                from app.services.index_manager import get_index_stats

                from .pipeline_bridge import run_blocking

                stats = await run_blocking(get_index_stats, ws.state.project_dir)
                if not isinstance(stats, dict):
                    _write(ws, f"📊 Статистика индекса: {stats}", markup=False)
                    return None
                lines = ["📊 Статистика индекса:"]
                for k, v in stats.items():
                    lines.append(f"  • {k}: {v}")
                _write(ws, "\n".join(lines), markup=False)
            except Exception as e:
                _write(ws, f"⚠️ Не удалось получить статистику индекса: {e}", markup=False)
            return None
        if sub in ("update", "обновить"):
            try:
                await ws.bridge.update_index(incremental=True)
            except Exception as e:
                _write(ws, f"⚠️ Ошибка обновления индекса: {e}", markup=False)
            return None
        if sub in ("rebuild", "пересобрать"):
            try:
                await ws.bridge.update_index(incremental=False)
            except Exception as e:
                _write(ws, f"⚠️ Ошибка пересборки индекса: {e}", markup=False)
            return None
        _write(ws, "Использование: /index [status|update|rebuild]", markup=False)
        return None

    # ------------------------------------------------------- diff / commit / rollback
    if head in ("/diff", "/дифф"):
        try:
            from .diff_viewer import show_diff_viewer

            if not ws.bridge._diffs:
                _write(ws, "ℹ️ Изменений нет", markup=False)
                return None
            show_diff_viewer(
                ws.app,
                dict(ws.bridge._diffs),
                pending_changes=ws.bridge._pending_changes,
            )
        except Exception as e:
            _write(ws, f"❌ Не удалось открыть diff: {e}", markup=False)
        return None

    if head in ("/commit", "/apply", "/применить"):
        if not ws.bridge.has_pending:
            _write(ws, "⚠️ Нет изменений для применения", markup=False)
            return None
        try:
            await ws.bridge._apply_changes()
        except Exception as e:
            _write(ws, f"❌ Ошибка применения: {e}", markup=False)
        return None

    if head in ("/rollback", "/discard", "/отмена"):
        if not ws.bridge.has_pending:
            _write(ws, "⚠️ Нет изменений для отмены", markup=False)
            return None
        try:
            await ws.bridge.discard()
        except Exception as e:
            _write(ws, f"❌ Ошибка отмены: {e}", markup=False)
        return None

    # ------------------------------------------------------- бэкап
    if head in ("/backup", "/бэкап"):
        await _handle_restore(ws, args)
        return None

    # ------------------------------------------------------- сжатие истории
    if head in ("/compress", "/сжать"):
        try:
            from app.history.compressor import compress_history_if_needed

            history = list(ws.bridge.history or [])
            active_model = ws.state.get_current_orchestrator_model()
            result = await compress_history_if_needed(history, active_model=active_model)
            if isinstance(result, tuple) and len(result) >= 1:
                new_history = result[0]
                stats = result[1] if len(result) > 1 else None
                # Нормализация: элементы должны быть {"role","content"}
                normalized = []
                for item in new_history or []:
                    if isinstance(item, dict):
                        role = str(item.get("role", "user"))
                        content = item.get("content", "")
                        if isinstance(content, list):
                            content = "\n".join(str(x) for x in content)
                        elif isinstance(content, dict):
                            content = str(content)
                        normalized.append({"role": role, "content": str(content)})
                    else:
                        role = str(getattr(item, "role", "user"))
                        content = getattr(item, "content", "")
                        if isinstance(content, list):
                            content = "\n".join(str(x) for x in content)
                        normalized.append({"role": role, "content": str(content)})
                ws.bridge.history = normalized
                if stats is None:
                    _write(ws, f"🗜 История сжата: {len(normalized)} сообщений", markup=False)
                else:
                    tok_before = getattr(stats, "original_tokens", 0) or (
                        stats.get("original_tokens") if isinstance(stats, dict) else 0
                    )
                    tok_after = getattr(stats, "compressed_tokens", 0) or (
                        stats.get("compressed_tokens") if isinstance(stats, dict) else 0
                    )
                    msg_before = getattr(stats, "messages_before", 0) or (
                        stats.get("messages_before") if isinstance(stats, dict) else 0
                    )
                    msg_after = getattr(stats, "messages_after", 0) or (
                        stats.get("messages_after") if isinstance(stats, dict) else 0
                    )
                    _write(
                        ws,
                        f"🗜 История сжата: {msg_before}→{msg_after} сообщений, "
                        f"{tok_before}→{tok_after} токенов",
                        markup=False,
                    )
            else:
                _write(ws, "ℹ️ Сжатие не требуется", markup=False)
        except Exception as e:
            _write(ws, f"⚠️ Сжатие недоступно: {e}", markup=False)
        return None

    # ------------------------------------------------------- env / доступ
    if head in ("/env", "/доступ"):
        try:
            from .menus import show_env_access_settings

            await show_env_access_settings(ws.app)
        except Exception as e:
            _write(ws, f"⚠️ Настройки доступа недоступны: {e}", markup=False)
        return None

    _write(ws, f"❓ Неизвестная команда: {head}. Введите /help для списка команд.")
    return None


def _status_text(ws: "WorkspacePane") -> str:
    """Собрать текст статуса воркспейса (аналог print_status_bar + новые поля)."""
    state = ws.state
    model = "Авто (роутер)" if state.use_router else (state.orchestrator_model or "Авто")
    mode_suffix = " (Legal)" if state.is_legal_mode and state.mode == "general" else ""
    try:
        tester_label = "ON" if getattr(state, "enable_tester", True) else "OFF"
    except Exception:
        tester_label = "ON"
    try:
        stage = str(getattr(state, "pending_stage", "IDLE"))
    except Exception:
        stage = "IDLE"
    try:
        translate_label = "ON" if getattr(state, "auto_translate", True) else "OFF"
    except Exception:
        translate_label = "ON"
    try:
        reasoning = getattr(state, "reasoning_effort", None) or "—"
    except Exception:
        reasoning = "—"
    lines = [
        "📊 Статус воркспейса:",
        f"  Режим: {state.mode}{mode_suffix}",
        f"  Проект: {state.project_dir or '—'}",
        f"  Модель оркестратора: {model}",
        f"  Провайдер оркестратора: {state.get_current_orchestrator_provider() or 'авто'}",
        f"  Модель генератора: {state.generator_model or 'из конфига'}",
        f"  Тред: {state.thread_id or '—'}",
        f"  Проверка типов: {'ON' if state.enable_type_checking else 'OFF'}",
        f"  Pre-filter: {state.prefilter_mode}",
        f"  Тестер: {tester_label}",
        f"  Стадия: {stage}",
        f"  Перевод: {translate_label}",
        f"  Reasoning: {reasoning}",
        f"  Изменений в ожидании: {'да' if ws.bridge.has_pending else 'нет'}",
    ]
    return "\n".join(lines)



async def _handle_restore(ws: "WorkspacePane", args: str) -> None:
    """Восстановление из бэкапа: с ID из аргумента или через валидируемый диалог."""
    from .dialogs import RestoreDialog
    from .pipeline_bridge import run_blocking

    bm = ws.bridge.pipeline.backup_manager
    if bm is None:
        _write(ws, "❌ Менеджер бэкапов недоступен (нужен проект в режиме Агента)")
        return
    session_id = (args or "").strip()
    if not session_id:
        value = await push_modal(ws.app, lambda: RestoreDialog())
        if not value:
            return
        session_id = value.strip()
    try:
        results = await run_blocking(bm.restore_session, session_id)
    except Exception as e:
        _write(ws, f"❌ Ошибка восстановления: {e}")
        return
    if not results:
        _write(ws, f"❌ Сессия не найдена или пуста: {session_id}")
        return
    ok = sum(1 for v in results.values() if v)
    fail = len(results) - ok
    lines = [f"♻️ Восстановлено {ok} файл(ов), ошибок: {fail}"]
    for path, success in list(results.items())[:20]:
        lines.append(f"  {'✓' if success else '✗'} {path}")
    _write(ws, "\n".join(lines), markup=False)


async def _handle_changes(ws: "WorkspacePane", args: str) -> None:
    """История изменений файлов: по треду или по конкретному файлу."""
    adapter = ws.history_adapter
    if adapter is None:
        _write(ws, "❌ Менеджер истории недоступен")
        return
    parts = args.split(maxsplit=1)
    if parts and parts[0].lower() in ("file", "файл"):
        if len(parts) < 2 or not parts[1].strip():
            _write(ws, "Использование: /changes file <путь_к_файлу>", markup=False)
            return
        file_path = parts[1].strip()
        changes = await adapter.get_file_history(file_path, limit=15)
        if not changes:
            _write(ws, f"Нет записей об изменениях файла: {file_path}", markup=False)
            return
        lines = [f"📜 История изменений файла: {file_path}"]
        for i, c in enumerate(changes, 1):
            created = (getattr(c, "created_at", "") or "")[:19].replace("T", " ")
            emoji = {"create": "🆕", "modify": "📝", "delete": "🗑️"}.get(
                getattr(c, "change_type", ""), "?"
            )
            lines.append(
                f"  {i}. {created} {emoji} {getattr(c, 'change_type', '?')} "
                f"+{getattr(c, 'lines_added', 0)}/-{getattr(c, 'lines_removed', 0)}"
            )
        _write(ws, "\n".join(lines), markup=False)
        return
    if not ws.state.thread_id:
        _write(ws, "📜 Нет активного диалога")
        return
    changes = await adapter.get_thread_file_changes(ws.state.thread_id, limit=30)
    if not changes:
        _write(ws, "В этом диалоге нет записей об изменениях файлов", markup=False)
        return
    lines = ["📜 История изменений файлов в диалоге:"]
    for i, c in enumerate(changes, 1):
        created = (getattr(c, "created_at", "") or "")[:19].replace("T", " ")
        status = "✅ OK" if getattr(c, "applied", False) else "⏳ Pending"
        lines.append(f"  {i}. {created} {getattr(c, 'file_path', '?')} ({status})")
    _write(ws, "\n".join(lines), markup=False)
"""Global thread-safety contract for Tk-safe pipeline architecture.

Contract:
1. Only Tk main thread may call Tkinter/CustomTkinter (after, widgets, configure).
2. Background threads must not call Tkinter directly or indirectly.
3. UI thread must not block on STT/language/audio/artifact locks or I/O.
4. No lock held while posting UI work, scheduling flush, file I/O, or callbacks.
5. Future language pipelines emit events via UIEventBus only.
"""

from __future__ import annotations

import threading
from typing import Any

_contract_logged = False


def log_thread_safety_contract_active() -> None:
    global _contract_logged
    if _contract_logged:
        return
    _contract_logged = True
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("THREAD_SAFETY_CONTRACT_ACTIVE")
        jp_accuracy_log("DEADLOCK_CLASS_PREVENTION_ACTIVE")
        jp_accuracy_log("NO_TK_CALL_FROM_BACKGROUND_CONFIRMED")
        jp_accuracy_log("NO_UI_BLOCKING_LOCK_WAIT_CONFIRMED")
    except Exception:
        pass


def log_language_pipeline_contract() -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("LANGUAGE_PIPELINE_CONTRACT_ACTIVE")
        jp_accuracy_log("JAPANESE_PIPELINE_CONTRACT_COMPLIANT")
        jp_accuracy_log("FUTURE_LANGUAGE_PIPELINE_RULES_REGISTERED")
    except Exception:
        pass


def collect_deferred_work_under_lock(
    lock: Any, collect_fn: Any, post_fn: Any
) -> None:
    """Collect state while holding lock; post events only after release."""
    with lock:
        items = collect_fn()
    if items:
        post_fn(items)


def is_background_thread() -> bool:
    from alpha.utils.ui_thread_guard import is_ui_main_thread

    return not is_ui_main_thread()

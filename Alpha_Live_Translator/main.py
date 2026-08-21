"""Alpha Live Translator — application entry point (V2 event architecture)."""

import os
import sys

# Give stdout and stderr somewhere durable to go BEFORE importing anything
# that could fail. The packaged shortcut runs `pythonw.exe`, which has no
# console, so without this every print() and every startup traceback is
# discarded -- and on the delivery machine there is no terminal to open and
# no code to change. Kept to the standard library and wrapped so it can never
# be the reason the app does not start.
_CONSOLE_LOG_PATH = None
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from alpha.utils.console_capture import start as _start_console_capture

    _CONSOLE_LOG_PATH = _start_console_capture(
        os.path.dirname(os.path.abspath(__file__))
    )
except Exception:
    pass


if __name__ == "__main__":
    try:
        from pathlib import Path

        from alpha.utils import startup_perf as _startup_perf

        _startup_perf.ensure_started(force=True)
        _startup_perf.mark("process_entry")
        _startup_perf.sample_memory("process_entry")
        _startup_perf.sample_threads("process_entry")

        _startup_perf.mark("main_module_import_started")
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("APP_ENTRYPOINT_STARTED")
        from alpha.utils.session_watchdog import install_crash_hooks_extended

        install_crash_hooks_extended()
        jp_accuracy_log("IMPORTS_COMPLETED")
        _startup_perf.mark("main_module_import_completed")

        _startup_perf.mark("configuration_load_started")
        from alpha.utils.troubleshooting_paths import ensure_troubleshooting_startup

        t_cfg = __import__("time").perf_counter()
        ensure_troubleshooting_startup()
        _startup_perf.record_blocking(
            "ensure_troubleshooting_startup",
            (__import__("time").perf_counter() - t_cfg) * 1000.0,
            location="main.py:ensure_troubleshooting_startup",
        )
        jp_accuracy_log("TROUBLESHOOTING_PATHS_INITIALIZED")
        try:
            from alpha.utils.async_debug_log import ensure_async_logger_healthy_non_blocking

            health = ensure_async_logger_healthy_non_blocking()
            if not bool(health.get("writer_thread_alive", False)):
                jp_accuracy_log("START_CONTINUED_WITH_ASYNC_LOGGER_WARNING", **health)
        except Exception:
            jp_accuracy_log("START_CONTINUED_WITH_ASYNC_LOGGER_WARNING")

        # V26.5.1: defer heavy disk recovery/cleanup until after the UI window
        # is visible so benchmark command → Alpha window stays responsive.
        _deferred_startup_recover = True

        from alpha.constants import APP_CODENAME, APP_VERSION, LONG_SESSION_STABILITY_MODE

        _startup_perf.mark("configuration_load_completed")
        _startup_perf.mark("logging_initialization_started")
        jp_accuracy_log("APP_VERSION_CONFIRMED", app_version=APP_VERSION, app_codename=APP_CODENAME)

        _FEATURE_FLAG_EVENTS = (
            "EVIDENCE_PROTECTION_85232_ACTIVE",
            "REAL_LIVE_ALPHA_PROTECTION_ACTIVE",
            "SMOKE_TEST_ALPHA_OVERWRITE_BLOCKED_ACTIVE",
            "VISIBLE_ERROR_AUDIT_EXPANDED_ACTIVE",
            "REFERENCE_TRANSCRIPT_QUALITY_CHECK_ACTIVE",
            "CER_REFERENCE_VALIDATION_ACTIVE",
            "REFERENCE_ALIGNMENT_DIAGNOSIS_ACTIVE",
            "JAPANESE_BOUNDARY_DIAGNOSIS_ACTIVE",
            "BUSINESS_TERM_RISK_REPORT_ACTIVE",
            "GLOSSARY_CANDIDATE_REPORT_ACTIVE",
            "REFERENCE_CLEANUP_SUGGESTIONS_ACTIVE",
            "BUSINESS_CER_BENCHMARK_INTEGRITY_ACTIVE",
            "REPORT_SYNCHRONIZATION_85234_ACTIVE",
            "CER_TRUST_REQUIRES_ALIGNMENT_COVERAGE_ACTIVE",
            "LATEST_ANALYZER_REPORT_SYNC_ACTIVE",
            "UPLOAD_PACKAGE_ANALYZER_REPORTS_ACTIVE",
            "BUSINESS_VIDEO_TEST_PROTOCOL_ACTIVE",
            "REFERENCE_ALPHA_HASH_BINDING_ACTIVE",
            "ALIGNMENT_COVERAGE_REPAIR_852341_ACTIVE",
            "PARAGRAPH_WINDOW_ALIGNMENT_ACTIVE",
            "SLIDING_TEXT_WINDOW_ALIGNMENT_ACTIVE",
            "CER_TRUST_ALIGNMENT_V2_ACTIVE",
            "EVIDENCE_INDEX_SYNC_AFTER_SCORING_ACTIVE",
            "LATEST_REPORT_SYNC_STRICT_MATCH_ACTIVE",
            "LINE_COUNT_MISMATCH_TOLERANCE_ACTIVE",
            "JAPANESE_BOUNDARY_STABILIZER_ACTIVE",
            "JAPANESE_BOUNDARY_STABILIZER_MODE_SAFE_ACTIVE",
            "JAPANESE_BOUNDARY_SHADOW_METRICS_ACTIVE",
            "JAPANESE_BOUNDARY_DECISION_LOG_ACTIVE",
            "JAPANESE_LEADING_FRAGMENT_HOLD_ACTIVE",
            "JAPANESE_SAFE_MERGE_ACTIVE",
            "JAPANESE_DUPLICATE_CONTINUATION_GUARD_ACTIVE",
            "JAPANESE_MIDLINE_PUNCTUATION_CLEANUP_ACTIVE",
            "BOUNDARY_MERGE_REVISION_ACTIVE",
            "STABLE_LINE_REVISION_MODEL_ACTIVE",
            "CLEAN_ALPHA_EXPORT_ACTIVE",
            "RESIDUAL_DUPLICATE_CLEANUP_ACTIVE",
            "PUNCTUATION_ARTIFACT_CLEANUP_ACTIVE",
            "FINAL_CLEAN_TRANSCRIPT_PERSISTENCE_ACTIVE",
            "STABLE_REVISION_HISTORY_PERSISTENCE_ACTIVE",
            "BOUNDARY_DECISIONS_FINALIZATION_ACTIVE",
            "BOUNDARY_EVIDENCE_PACKAGE_COMPLETION_ACTIVE",
            "CUMULATIVE_ALPHA_REJECTION_ACTIVE",
            "CLEAN_ALPHA_EXPORT_REQUIRED_FOR_SCORING",
            "CORPORATE_IR_GLOSSARY_ACTIVE",
            "GLOSSARY_KEYTERM_BOOST_ACTIVE",
            "STABLE_GLOSSARY_CORRECTION_ACTIVE",
            "FINANCIAL_NUMBER_AUDIT_ACTIVE",
            "FINANCIAL_NUMBER_CORRECTION_ACTIVE",
            "CORPORATE_TERM_AUDIT_ACTIVE",
            "NAME_COMPANY_AUDIT_ACTIVE",
            "LOSSLESS_CLEAN_EXPORT_ACTIVE",
            "EXPORT_COVERAGE_GATE_ACTIVE",
            "VALID_SEGMENT_LOSS_BLOCKER_ACTIVE",
            "CONSERVATIVE_DUPLICATE_SUPPRESSION_ACTIVE",
            "SUPPRESSION_DECISION_LOG_ACTIVE",
            "GLOSSARY_EVIDENCE_SYNC_FIX_ACTIVE",
            "SCORE_GLOSSARY_METADATA_SYNC_ACTIVE",
            "LATEST_INDEX_GLOSSARY_SYNC_FIX_ACTIVE",
            "CANONICAL_TRANSCRIPT_LINEAGE_ACTIVE",
            "FINAL_EXPORT_LOCK_ACTIVE",
            "SOURCE_COMMIT_LINEAGE_REQUIRED",
            "LINEAGE_BASED_EXPORT_COVERAGE_ACTIVE",
            "PRE_CORRECTION_REENTRY_BLOCK_ACTIVE",
            "CORRECTED_LINE_REPRESENTS_SOURCE_ACTIVE",
            "TEXT_SIMILARITY_RECOVERY_SECONDARY_ONLY",
            "CANONICAL_CLEAN_TRANSCRIPT_REQUIRED_FOR_SCORING",
            "SUPPRESSION_AWARE_LINEAGE_COVERAGE_ACTIVE",
            "INTENTIONAL_SUPPRESSION_PROVENANCE_REQUIRED",
            "FULL_SPAN_FINANCIAL_NUMBER_CORRECTION_ACTIVE",
            "FINANCIAL_NUMBER_SEMANTIC_VALIDATION_ACTIVE",
            "MALFORMED_NUMERIC_OUTPUT_BLOCK_ACTIVE",
            "CANONICAL_CORRECTION_LINEAGE_REQUIRED",
            "CANONICAL_OUTPUT_ARTIFACT_UNIFICATION_ACTIVE",
            "ATOMIC_ACCURACY_EVIDENCE_FINALIZATION_ACTIVE",
            "CANONICAL_TRANSCRIPT_LEDGER_ACTIVE",
            "SINGLE_REVISION_AUTHORITY_ACTIVE",
            "ATOMIC_STOP_FINALIZATION_BARRIER_ACTIVE",
            "FINAL_EXPORT_FROM_FROZEN_LEDGER_ONLY_ACTIVE",
            "RAW_EVENT_LINEAGE_REQUIRED_ACTIVE",
            "FAIL_CLOSED_PIPELINE_VALIDATION_ACTIVE",
            "DETERMINISTIC_REFERENCE_SNAPSHOT_ACTIVE",
            "LIVE_RUNTIME_METRICS_REGISTRY_ACTIVE",
            "CURRENT_RUN_ARTIFACT_ISOLATION_ACTIVE",
            "CRITICAL_GATE_PARTIAL_PASS_DISABLED",
            "SAFE_STABLE_REVISION_ACTIVE",
            "REVISION_LINEAGE_REQUIRED_ACTIVE",
            "REVISION_TERMINAL_SENTENCE_GUARD_ACTIVE",
            "REVISION_CONTENT_LOSS_GUARD_ACTIVE",
            "UNPROVEN_REVISION_DEFAULT_APPEND_ACTIVE",
            "CER_OPERATION_ACCOUNTING_STRICT_ACTIVE",
            "RUNTIME_AUDIO_DELIVERY_COUNTERS_ACTIVE",
            "THREE_STAGE_ACCURACY_DIAGNOSTIC_ACTIVE",
            "RAW_DEEPGRAM_STAGE_CAPTURE_ACTIVE",
            "ASSEMBLER_ONLY_STAGE_CAPTURE_ACTIVE",
            "FINAL_ALPHA_STAGE_CAPTURE_ACTIVE",
            "DEEPGRAM_REQUEST_SNAPSHOT_ACTIVE",
            "IMMUTABLE_LIVE_STAGE_EVIDENCE_ACTIVE",
            "DIAGNOSTIC_STAGE_TEXT_MUTATION_DISABLED",
            "VALIDATION_LATEST_LIVE_WRITE_DISABLED",
            "AUTO_BUSINESS_CORRECTION_LEVEL_MINIMAL_PLUS_USER_GLOSSARY",
            "CUMULATIVE_MERGE_DUPLICATE_GUARD_ACTIVE",
            "BOUNDARY_EXPORT_SOURCE_UI_SEGMENTS_ACTIVE",
            "BOUNDARY_REPORT_PACKAGE_FIX_ACTIVE",
            "BOUNDARY_SUMMARY_PATH_FIX_ACTIVE",
            "RAW_DEEPGRAM_IMMUTABLE_CONFIRMED",
            "ACCURACY_DIRECTION_SETTLED_ACTIVE",
            "BENCHMARK_BASELINE_LOCK_ACTIVE",
            "ANTI_OVERFIT_MODE_ACTIVE",
            "LESSON_SPECIFIC_CORRECTIONS_DISABLED",
            "AUTO_BUSINESS_CORRECTION_LEVEL_MINIMAL",
            "CORRECTION_RULE_APPROVAL_REQUIRED",
            "SINGLE_SAMPLE_CORRECTION_BLOCKED",
            "REFERENCE_SCORING_REQUIRED_FOR_NEW_RULES",
            "ACCURACY_REGRESSION_ROLLBACK_85223_ACTIVE",
            "SAFE_CORRECTION_GATE_ACTIVE",
            "DISABLE_HARMFUL_85222_EXPANSION_RULES_ACTIVE",
            "DISABLE_POLITE_CLOSING_ZO_PREFIX_ACTIVE",
            "EVIDENCE_POINTER_FIXES_FROM_85222_PRESERVED",
            "ACCURACY_BASELINE_85221_PRESERVED",
            "EVIDENCE_POINTER_FINALIZATION_FIX_ACTIVE",
            "LATEST_POINTER_COMPLETED_STATUS_FIX_ACTIVE",
            "LATEST_UPLOAD_ZIP_POINTER_FIX_ACTIVE",
            "LATEST_ACCURACY_ZIP_FLUSH_FIX_ACTIVE",
            "BUSINESS_CORRECTION_GUARD_85221_ACTIVE",
            "BUSINESS_CORRECTION_IDEMPOTENT_MODE_ACTIVE",
            "PUNCTUATION_START_POST_CORRECTION_MERGE_ACTIVE",
            "STOP_TAIL_VALIDATION_NA_FIX_ACTIVE",
            "FINAL_UI_UPDATE_TIMED_OUT_KWARG_FIX_ACTIVE",
            "FULL_ACCURACY_LOGGING_STILL_ENABLED",
            "EVIDENCE_POINTER_FIX_PRESERVED",
            "LATEST_ACCURACY_ZIP_FIX_PRESERVED",
            "JAPANESE_BUSINESS_ACCURACY_8522_ACTIVE",
            "ACCURACY_EVIDENCE_MODE_ACTIVE",
            "AUTO_EXPORT_ALPHA_TXT_ACTIVE",
            "TEMP_AUDIO_RETENTION_2H_ACTIVE",
            "RAW_DEEPGRAM_IMMUTABLE_CONFIRMED",
            "RUNTIME_BASELINE_PRESERVED",
            "JAPANESE_STABLE_ACCURACY_FIX_ACTIVE",
            "RUNTIME_STOP_FREEZE_ELIMINATION_ACTIVE",
            "OFFLINE_EVIDENCE_PACKAGING_ACTIVE",
            "STOP_PATH_MINIMAL_MODE_ACTIVE",
        )

        def _emit_feature_flag_startup_logs():
            try:
                if os.environ.get("ALPHA_MULTIDOMAIN_BENCHMARK_MODE", "").strip() in (
                    "1",
                    "true",
                    "yes",
                ):
                    jp_accuracy_log(
                        "MULTIDOMAIN_BENCHMARK_MODE_ENV_ACTIVE",
                        profile=os.environ.get("JAPANESE_ACCURACY_PROFILE", ""),
                        keyterms_disabled=True,
                    )
            except Exception:
                pass
            for event_name in _FEATURE_FLAG_EVENTS:
                jp_accuracy_log(event_name)
            if LONG_SESSION_STABILITY_MODE:
                jp_accuracy_log("LONG_SESSION_STABILITY_MODE_ACTIVE")

        # Default: defer ~150 feature-flag accuracy logs until after first paint.
        # Set ALPHA_STARTUP_DEFER_FLAG_LOGS=0 to restore pre-paint synchronous spam.
        _defer_flag_logs = os.environ.get("ALPHA_STARTUP_DEFER_FLAG_LOGS", "1").strip().lower() not in (
            "0",
            "false",
            "no",
        )
        if not _defer_flag_logs:
            t_flags = __import__("time").perf_counter()
            _emit_feature_flag_startup_logs()
            _startup_perf.record_blocking(
                "feature_flag_startup_logs",
                (__import__("time").perf_counter() - t_flags) * 1000.0,
                location="main.py:feature_flag_logs",
            )
        from alpha.constants import TK_SAFE_PIPELINE_MODE

        _startup_perf.mark("logging_initialization_completed")

        # Pipeline workers start after first paint (default) so they do not block
        # window creation. Set ALPHA_STARTUP_PIPELINE_BEFORE_UI=1 for old order.
        _pipeline_before_ui = os.environ.get(
            "ALPHA_STARTUP_PIPELINE_BEFORE_UI", "0"
        ).strip().lower() in ("1", "true", "yes")
        if TK_SAFE_PIPELINE_MODE and _pipeline_before_ui:
            jp_accuracy_log("TK_SAFE_PIPELINE_MODE_ACTIVE")
            jp_accuracy_log("LANGUAGE_AGNOSTIC_UI_EVENT_BUS_ACTIVE")
            from alpha.utils.thread_safety import (
                log_language_pipeline_contract,
                log_thread_safety_contract_active,
            )
            from alpha.utils.ui_event_bus import start_ui_event_bus
            from alpha.utils.language_pipeline_worker import start_language_pipeline_worker

            log_thread_safety_contract_active()
            log_language_pipeline_contract()
            start_ui_event_bus()
            start_language_pipeline_worker()
        from alpha.constants import LONG_RUN_EVIDENCE_PACKAGE_MODE

        if LONG_RUN_EVIDENCE_PACKAGE_MODE:
            jp_accuracy_log("LONG_RUN_EVIDENCE_PACKAGE_MODE_ACTIVE")
        try:
            from alpha.utils.flight_recorder import record_flight_event

            record_flight_event("app_start", force=True)
        except Exception:
            pass

        from alpha.utils.diagnostic_test_log import (
            DIAGNOSTIC_LOGGING,
            diag_init,
            diag_log,
            diag_log_startup_safety_flags,
            diag_mark_first_ui_render,
            get_log_file_path,
            install_diagnostic_hooks,
        )

        diag_init()
        diag_log("startup", "before_ui_create")

        print(f"Alpha V{APP_VERSION} ({APP_CODENAME})")

        jp_accuracy_log("ROOT_WINDOW_CREATE_BEGIN")
        _startup_perf.mark("app_construction_started")

        # Real Alpha window only — no splash screen. Splash previously inflated
        # "first paint / interactive ready" metrics without the main UI being ready.
        t_import_ui = __import__("time").perf_counter()
        from alpha.ui.main_window import AlphaApp
        from alpha.utils.logging_utils import setup_logging

        _startup_perf.record_blocking(
            "import_alpha.ui.main_window",
            (__import__("time").perf_counter() - t_import_ui) * 1000.0,
            location="main.py:import AlphaApp",
        )

        install_crash_hooks_extended(AlphaApp)
        install_diagnostic_hooks(AlphaApp)
        setup_logging()

        from alpha.utils.tk_thread_guard import install_tk_thread_guard

        install_tk_thread_guard(AlphaApp)

        jp_accuracy_log("ROOT_WINDOW_CREATE_DONE")
        jp_accuracy_log("MAIN_WINDOW_CREATE_BEGIN")
        _startup_perf.mark("real_alpha_ui_construction_started")
        t_app = __import__("time").perf_counter()
        app = AlphaApp()
        _startup_perf.record_blocking(
            "AlphaApp.__init__",
            (__import__("time").perf_counter() - t_app) * 1000.0,
            location="main.py:AlphaApp()",
        )
        _startup_perf.mark("tkinter_root_created")
        _startup_perf.mark("window_show_requested")
        _startup_perf.mark("real_alpha_ui_construction_completed")
        try:
            app.update_idletasks()
            app.update()
        except Exception:
            pass
        _startup_perf.mark("real_alpha_first_paint")
        _startup_perf.mark("first_visible_paint")
        _startup_perf.mark("first_tk_idle_callback")
        # Interactive only after real Alpha first paint (never splash / never pre-paint).
        try:
            app.after(0, app._mark_real_alpha_interactive_ready)
        except Exception:
            pass
        jp_accuracy_log("MAIN_WINDOW_CREATE_DONE")
        diag_log("startup", "after_ui_create", {"ui_create_ms": None})
        _startup_perf.sample_threads("after_ui_create")
        _startup_perf.sample_memory("after_ui_create")

        def _install_stabilizer_hooks():
            # Class-level hook install is safe off the UI thread.
            import threading

            def _worker():
                try:
                    from alpha.transcription.japanese_final_chunk_stabilizer import (
                        install_japanese_stabilizer_hooks,
                    )

                    install_japanese_stabilizer_hooks(AlphaApp)
                except Exception:
                    pass

            threading.Thread(
                target=_worker, name="StabilizerImport", daemon=True
            ).start()

        # Delay nonessential post-paint background imports so GIL churn does not
        # stall the real Alpha UI during the first responsive window.
        _post_paint_bg_delay_ms = (
            2600 if os.environ.get("ALPHA_STARTUP_PROFILE", "").strip() in ("1", "true", "yes")
            else 900
        )
        app.after(_post_paint_bg_delay_ms, _install_stabilizer_hooks)

        def _start_deferred_pipeline():
            if not TK_SAFE_PIPELINE_MODE or _pipeline_before_ui:
                return
            import threading

            def _worker():
                try:
                    from alpha.utils.thread_safety import (
                        log_language_pipeline_contract,
                        log_thread_safety_contract_active,
                    )
                    from alpha.utils.ui_event_bus import start_ui_event_bus
                    from alpha.utils.language_pipeline_worker import (
                        start_language_pipeline_worker,
                    )

                    jp_accuracy_log("TK_SAFE_PIPELINE_MODE_ACTIVE")
                    jp_accuracy_log("LANGUAGE_AGNOSTIC_UI_EVENT_BUS_ACTIVE")
                    log_thread_safety_contract_active()
                    log_language_pipeline_contract()
                    start_ui_event_bus()
                    start_language_pipeline_worker()
                except Exception:
                    jp_accuracy_log("DEFERRED_PIPELINE_START_FAILED")

            threading.Thread(
                target=_worker, name="DeferredPipelineImport", daemon=True
            ).start()

        app.after(_post_paint_bg_delay_ms, _start_deferred_pipeline)
        if _defer_flag_logs:
            def _deferred_flag_logs():
                import threading

                def _worker():
                    try:
                        _emit_feature_flag_startup_logs()
                        jp_accuracy_log("DEFERRED_FEATURE_FLAG_STARTUP_LOGS_DONE")
                    except Exception:
                        pass

                threading.Thread(
                    target=_worker, name="DeferredFeatureFlagLogs", daemon=True
                ).start()

            app.after(_post_paint_bg_delay_ms + 100, _deferred_flag_logs)
        _startup_perf.install_autoquit(app)

        def _deferred_post_ui_startup():
            try:
                from alpha.utils.tk_thread_guard import scan_tk_call_sites

                scan_tk_call_sites()
            except Exception:
                pass
            try:
                from alpha.utils.audio_temp_capture import cleanup_old_audio_temp

                cleanup_old_audio_temp(reason="startup")
            except Exception:
                pass
            try:
                from alpha.utils.run_artifacts import recover_previous_incomplete_runs_on_startup

                recover_previous_incomplete_runs_on_startup()
            except Exception:
                pass
            try:
                from alpha.transcription.japanese_accuracy_cleaner import (
                    run_business_cleanup_selftest_once,
                )

                run_business_cleanup_selftest_once()
            except Exception:
                pass
            try:
                from alpha.transcription.japanese_business_accuracy import (
                    run_business_correction_guard_selftest,
                    run_minimal_correction_selftest,
                )

                run_business_correction_guard_selftest()
                run_minimal_correction_selftest()
            except Exception:
                pass
            jp_accuracy_log("DEFERRED_POST_UI_STARTUP_DONE")

        if DIAGNOSTIC_LOGGING:
            app.after(0, diag_mark_first_ui_render)
            app.after(0, diag_log_startup_safety_flags)
        if _deferred_startup_recover:
            # Heavy disk recovery/selftests contend for the GIL; keep them out of
            # the first interactive window so the real Alpha UI stays responsive.
            _recover_delay_ms = (
                3000
                if os.environ.get("ALPHA_STARTUP_PROFILE", "").strip().lower()
                in ("1", "true", "yes")
                else 1500
            )
            app.after(
                _recover_delay_ms,
                lambda: __import__("threading").Thread(
                    target=_deferred_post_ui_startup, name="DeferredStartup", daemon=True
                ).start(),
            )

        diag_log("startup", "before_mainloop")
        jp_accuracy_log("UI_FIRST_RENDER_BEGIN")
        jp_accuracy_log("UI_FIRST_RENDER_DONE")
        jp_accuracy_log("APP_MAINLOOP_ENTERING")

        if DIAGNOSTIC_LOGGING:
            print(f"[Diagnostic] logging to {get_log_file_path()}")

        try:
            app.mainloop()
        except KeyboardInterrupt as exc:
            try:
                from alpha.utils.crash_guard_log import handle_crash_event

                handle_crash_event(
                    "POST_RUN_MANUAL_EXIT",
                    exception_type=type(exc).__name__,
                    exception_message=str(exc),
                    host=app,
                )
            except Exception:
                pass
        else:
            try:
                from alpha.utils.crash_guard_log import handle_crash_event

                handle_crash_event("POST_RUN_NORMAL_APP_EXIT", host=app)
            except Exception:
                pass
    except Exception as exc:
        try:
            from alpha.utils.crash_guard_log import log_exception

            log_exception(exc, source="startup_fatal")
        except Exception:
            pass
        try:
            from alpha.utils.diagnostic_test_log import diag_log_exception

            diag_log_exception("startup", "fatal_error", exc)
        except Exception:
            pass
        # The full traceback, not just the message. This is often the only
        # record of why the app would not start on a machine we cannot reach.
        try:
            import traceback

            print("Fatal error during startup:")
            traceback.print_exc()
            if _CONSOLE_LOG_PATH:
                print("written to " + str(_CONSOLE_LOG_PATH))
        except Exception:
            print(f"Fatal error: {exc}")
        sys.exit(1)

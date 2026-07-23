"""Alpha Live Translator — application entry point (V2 event architecture)."""

import sys


if __name__ == "__main__":
    try:
        from pathlib import Path

        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log("APP_ENTRYPOINT_STARTED")
        from alpha.utils.session_watchdog import install_crash_hooks_extended

        install_crash_hooks_extended()
        jp_accuracy_log("IMPORTS_COMPLETED")

        from alpha.utils.troubleshooting_paths import ensure_troubleshooting_startup

        ensure_troubleshooting_startup()
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

        jp_accuracy_log("APP_VERSION_CONFIRMED", app_version=APP_VERSION, app_codename=APP_CODENAME)
        try:
            import os

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
        jp_accuracy_log("EVIDENCE_PROTECTION_85232_ACTIVE")
        jp_accuracy_log("REAL_LIVE_ALPHA_PROTECTION_ACTIVE")
        jp_accuracy_log("SMOKE_TEST_ALPHA_OVERWRITE_BLOCKED_ACTIVE")
        jp_accuracy_log("VISIBLE_ERROR_AUDIT_EXPANDED_ACTIVE")
        jp_accuracy_log("REFERENCE_TRANSCRIPT_QUALITY_CHECK_ACTIVE")
        jp_accuracy_log("CER_REFERENCE_VALIDATION_ACTIVE")
        jp_accuracy_log("REFERENCE_ALIGNMENT_DIAGNOSIS_ACTIVE")
        jp_accuracy_log("JAPANESE_BOUNDARY_DIAGNOSIS_ACTIVE")
        jp_accuracy_log("BUSINESS_TERM_RISK_REPORT_ACTIVE")
        jp_accuracy_log("GLOSSARY_CANDIDATE_REPORT_ACTIVE")
        jp_accuracy_log("REFERENCE_CLEANUP_SUGGESTIONS_ACTIVE")
        jp_accuracy_log("BUSINESS_CER_BENCHMARK_INTEGRITY_ACTIVE")
        jp_accuracy_log("REPORT_SYNCHRONIZATION_85234_ACTIVE")
        jp_accuracy_log("CER_TRUST_REQUIRES_ALIGNMENT_COVERAGE_ACTIVE")
        jp_accuracy_log("LATEST_ANALYZER_REPORT_SYNC_ACTIVE")
        jp_accuracy_log("UPLOAD_PACKAGE_ANALYZER_REPORTS_ACTIVE")
        jp_accuracy_log("BUSINESS_VIDEO_TEST_PROTOCOL_ACTIVE")
        jp_accuracy_log("REFERENCE_ALPHA_HASH_BINDING_ACTIVE")
        jp_accuracy_log("ALIGNMENT_COVERAGE_REPAIR_852341_ACTIVE")
        jp_accuracy_log("PARAGRAPH_WINDOW_ALIGNMENT_ACTIVE")
        jp_accuracy_log("SLIDING_TEXT_WINDOW_ALIGNMENT_ACTIVE")
        jp_accuracy_log("CER_TRUST_ALIGNMENT_V2_ACTIVE")
        jp_accuracy_log("EVIDENCE_INDEX_SYNC_AFTER_SCORING_ACTIVE")
        jp_accuracy_log("LATEST_REPORT_SYNC_STRICT_MATCH_ACTIVE")
        jp_accuracy_log("LINE_COUNT_MISMATCH_TOLERANCE_ACTIVE")
        jp_accuracy_log("JAPANESE_BOUNDARY_STABILIZER_ACTIVE")
        jp_accuracy_log("JAPANESE_BOUNDARY_STABILIZER_MODE_SAFE_ACTIVE")
        jp_accuracy_log("JAPANESE_BOUNDARY_SHADOW_METRICS_ACTIVE")
        jp_accuracy_log("JAPANESE_BOUNDARY_DECISION_LOG_ACTIVE")
        jp_accuracy_log("JAPANESE_LEADING_FRAGMENT_HOLD_ACTIVE")
        jp_accuracy_log("JAPANESE_SAFE_MERGE_ACTIVE")
        jp_accuracy_log("JAPANESE_DUPLICATE_CONTINUATION_GUARD_ACTIVE")
        jp_accuracy_log("JAPANESE_MIDLINE_PUNCTUATION_CLEANUP_ACTIVE")
        jp_accuracy_log("BOUNDARY_MERGE_REVISION_ACTIVE")
        jp_accuracy_log("STABLE_LINE_REVISION_MODEL_ACTIVE")
        jp_accuracy_log("CLEAN_ALPHA_EXPORT_ACTIVE")
        jp_accuracy_log("RESIDUAL_DUPLICATE_CLEANUP_ACTIVE")
        jp_accuracy_log("PUNCTUATION_ARTIFACT_CLEANUP_ACTIVE")
        jp_accuracy_log("FINAL_CLEAN_TRANSCRIPT_PERSISTENCE_ACTIVE")
        jp_accuracy_log("STABLE_REVISION_HISTORY_PERSISTENCE_ACTIVE")
        jp_accuracy_log("BOUNDARY_DECISIONS_FINALIZATION_ACTIVE")
        jp_accuracy_log("BOUNDARY_EVIDENCE_PACKAGE_COMPLETION_ACTIVE")
        jp_accuracy_log("CUMULATIVE_ALPHA_REJECTION_ACTIVE")
        jp_accuracy_log("CLEAN_ALPHA_EXPORT_REQUIRED_FOR_SCORING")
        jp_accuracy_log("CORPORATE_IR_GLOSSARY_ACTIVE")
        jp_accuracy_log("GLOSSARY_KEYTERM_BOOST_ACTIVE")
        jp_accuracy_log("STABLE_GLOSSARY_CORRECTION_ACTIVE")
        jp_accuracy_log("FINANCIAL_NUMBER_AUDIT_ACTIVE")
        jp_accuracy_log("FINANCIAL_NUMBER_CORRECTION_ACTIVE")
        jp_accuracy_log("CORPORATE_TERM_AUDIT_ACTIVE")
        jp_accuracy_log("NAME_COMPANY_AUDIT_ACTIVE")
        jp_accuracy_log("LOSSLESS_CLEAN_EXPORT_ACTIVE")
        jp_accuracy_log("EXPORT_COVERAGE_GATE_ACTIVE")
        jp_accuracy_log("VALID_SEGMENT_LOSS_BLOCKER_ACTIVE")
        jp_accuracy_log("CONSERVATIVE_DUPLICATE_SUPPRESSION_ACTIVE")
        jp_accuracy_log("SUPPRESSION_DECISION_LOG_ACTIVE")
        jp_accuracy_log("GLOSSARY_EVIDENCE_SYNC_FIX_ACTIVE")
        jp_accuracy_log("SCORE_GLOSSARY_METADATA_SYNC_ACTIVE")
        jp_accuracy_log("LATEST_INDEX_GLOSSARY_SYNC_FIX_ACTIVE")
        jp_accuracy_log("CANONICAL_TRANSCRIPT_LINEAGE_ACTIVE")
        jp_accuracy_log("FINAL_EXPORT_LOCK_ACTIVE")
        jp_accuracy_log("SOURCE_COMMIT_LINEAGE_REQUIRED")
        jp_accuracy_log("LINEAGE_BASED_EXPORT_COVERAGE_ACTIVE")
        jp_accuracy_log("PRE_CORRECTION_REENTRY_BLOCK_ACTIVE")
        jp_accuracy_log("CORRECTED_LINE_REPRESENTS_SOURCE_ACTIVE")
        jp_accuracy_log("TEXT_SIMILARITY_RECOVERY_SECONDARY_ONLY")
        jp_accuracy_log("CANONICAL_CLEAN_TRANSCRIPT_REQUIRED_FOR_SCORING")
        jp_accuracy_log("SUPPRESSION_AWARE_LINEAGE_COVERAGE_ACTIVE")
        jp_accuracy_log("INTENTIONAL_SUPPRESSION_PROVENANCE_REQUIRED")
        jp_accuracy_log("FULL_SPAN_FINANCIAL_NUMBER_CORRECTION_ACTIVE")
        jp_accuracy_log("FINANCIAL_NUMBER_SEMANTIC_VALIDATION_ACTIVE")
        jp_accuracy_log("MALFORMED_NUMERIC_OUTPUT_BLOCK_ACTIVE")
        jp_accuracy_log("CANONICAL_CORRECTION_LINEAGE_REQUIRED")
        jp_accuracy_log("CANONICAL_OUTPUT_ARTIFACT_UNIFICATION_ACTIVE")
        jp_accuracy_log("ATOMIC_ACCURACY_EVIDENCE_FINALIZATION_ACTIVE")
        jp_accuracy_log("CANONICAL_TRANSCRIPT_LEDGER_ACTIVE")
        jp_accuracy_log("SINGLE_REVISION_AUTHORITY_ACTIVE")
        jp_accuracy_log("ATOMIC_STOP_FINALIZATION_BARRIER_ACTIVE")
        jp_accuracy_log("FINAL_EXPORT_FROM_FROZEN_LEDGER_ONLY_ACTIVE")
        jp_accuracy_log("RAW_EVENT_LINEAGE_REQUIRED_ACTIVE")
        jp_accuracy_log("FAIL_CLOSED_PIPELINE_VALIDATION_ACTIVE")
        jp_accuracy_log("DETERMINISTIC_REFERENCE_SNAPSHOT_ACTIVE")
        jp_accuracy_log("LIVE_RUNTIME_METRICS_REGISTRY_ACTIVE")
        jp_accuracy_log("CURRENT_RUN_ARTIFACT_ISOLATION_ACTIVE")
        jp_accuracy_log("CRITICAL_GATE_PARTIAL_PASS_DISABLED")
        jp_accuracy_log("SAFE_STABLE_REVISION_ACTIVE")
        jp_accuracy_log("REVISION_LINEAGE_REQUIRED_ACTIVE")
        jp_accuracy_log("REVISION_TERMINAL_SENTENCE_GUARD_ACTIVE")
        jp_accuracy_log("REVISION_CONTENT_LOSS_GUARD_ACTIVE")
        jp_accuracy_log("UNPROVEN_REVISION_DEFAULT_APPEND_ACTIVE")
        jp_accuracy_log("CER_OPERATION_ACCOUNTING_STRICT_ACTIVE")
        jp_accuracy_log("RUNTIME_AUDIO_DELIVERY_COUNTERS_ACTIVE")
        jp_accuracy_log("THREE_STAGE_ACCURACY_DIAGNOSTIC_ACTIVE")
        jp_accuracy_log("RAW_DEEPGRAM_STAGE_CAPTURE_ACTIVE")
        jp_accuracy_log("ASSEMBLER_ONLY_STAGE_CAPTURE_ACTIVE")
        jp_accuracy_log("FINAL_ALPHA_STAGE_CAPTURE_ACTIVE")
        jp_accuracy_log("DEEPGRAM_REQUEST_SNAPSHOT_ACTIVE")
        jp_accuracy_log("IMMUTABLE_LIVE_STAGE_EVIDENCE_ACTIVE")
        jp_accuracy_log("DIAGNOSTIC_STAGE_TEXT_MUTATION_DISABLED")
        jp_accuracy_log("VALIDATION_LATEST_LIVE_WRITE_DISABLED")
        jp_accuracy_log("AUTO_BUSINESS_CORRECTION_LEVEL_MINIMAL_PLUS_USER_GLOSSARY")
        jp_accuracy_log("CUMULATIVE_MERGE_DUPLICATE_GUARD_ACTIVE")
        jp_accuracy_log("BOUNDARY_EXPORT_SOURCE_UI_SEGMENTS_ACTIVE")
        jp_accuracy_log("BOUNDARY_REPORT_PACKAGE_FIX_ACTIVE")
        jp_accuracy_log("BOUNDARY_SUMMARY_PATH_FIX_ACTIVE")
        jp_accuracy_log("RAW_DEEPGRAM_IMMUTABLE_CONFIRMED")
        jp_accuracy_log("ACCURACY_DIRECTION_SETTLED_ACTIVE")
        jp_accuracy_log("BENCHMARK_BASELINE_LOCK_ACTIVE")
        jp_accuracy_log("ANTI_OVERFIT_MODE_ACTIVE")
        jp_accuracy_log("LESSON_SPECIFIC_CORRECTIONS_DISABLED")
        jp_accuracy_log("AUTO_BUSINESS_CORRECTION_LEVEL_MINIMAL")
        jp_accuracy_log("CORRECTION_RULE_APPROVAL_REQUIRED")
        jp_accuracy_log("SINGLE_SAMPLE_CORRECTION_BLOCKED")
        jp_accuracy_log("REFERENCE_SCORING_REQUIRED_FOR_NEW_RULES")
        jp_accuracy_log("ACCURACY_REGRESSION_ROLLBACK_85223_ACTIVE")
        jp_accuracy_log("SAFE_CORRECTION_GATE_ACTIVE")
        jp_accuracy_log("DISABLE_HARMFUL_85222_EXPANSION_RULES_ACTIVE")
        jp_accuracy_log("DISABLE_POLITE_CLOSING_ZO_PREFIX_ACTIVE")
        jp_accuracy_log("EVIDENCE_POINTER_FIXES_FROM_85222_PRESERVED")
        jp_accuracy_log("ACCURACY_BASELINE_85221_PRESERVED")
        jp_accuracy_log("EVIDENCE_POINTER_FINALIZATION_FIX_ACTIVE")
        jp_accuracy_log("LATEST_POINTER_COMPLETED_STATUS_FIX_ACTIVE")
        jp_accuracy_log("LATEST_UPLOAD_ZIP_POINTER_FIX_ACTIVE")
        jp_accuracy_log("LATEST_ACCURACY_ZIP_FLUSH_FIX_ACTIVE")
        jp_accuracy_log("BUSINESS_CORRECTION_GUARD_85221_ACTIVE")
        jp_accuracy_log("BUSINESS_CORRECTION_IDEMPOTENT_MODE_ACTIVE")
        jp_accuracy_log("PUNCTUATION_START_POST_CORRECTION_MERGE_ACTIVE")
        jp_accuracy_log("STOP_TAIL_VALIDATION_NA_FIX_ACTIVE")
        jp_accuracy_log("FINAL_UI_UPDATE_TIMED_OUT_KWARG_FIX_ACTIVE")
        jp_accuracy_log("FULL_ACCURACY_LOGGING_STILL_ENABLED")
        jp_accuracy_log("EVIDENCE_POINTER_FIX_PRESERVED")
        jp_accuracy_log("LATEST_ACCURACY_ZIP_FIX_PRESERVED")
        jp_accuracy_log("JAPANESE_BUSINESS_ACCURACY_8522_ACTIVE")
        jp_accuracy_log("ACCURACY_EVIDENCE_MODE_ACTIVE")
        jp_accuracy_log("AUTO_EXPORT_ALPHA_TXT_ACTIVE")
        jp_accuracy_log("TEMP_AUDIO_RETENTION_2H_ACTIVE")
        jp_accuracy_log("RAW_DEEPGRAM_IMMUTABLE_CONFIRMED")
        jp_accuracy_log("RUNTIME_BASELINE_PRESERVED")
        jp_accuracy_log("JAPANESE_STABLE_ACCURACY_FIX_ACTIVE")
        jp_accuracy_log("RUNTIME_STOP_FREEZE_ELIMINATION_ACTIVE")
        jp_accuracy_log("OFFLINE_EVIDENCE_PACKAGING_ACTIVE")
        jp_accuracy_log("STOP_PATH_MINIMAL_MODE_ACTIVE")
        if LONG_SESSION_STABILITY_MODE:
            jp_accuracy_log("LONG_SESSION_STABILITY_MODE_ACTIVE")
        from alpha.constants import TK_SAFE_PIPELINE_MODE

        if TK_SAFE_PIPELINE_MODE:
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
        from alpha.ui.main_window import AlphaApp
        from alpha.utils.logging_utils import setup_logging

        install_crash_hooks_extended(AlphaApp)
        install_diagnostic_hooks(AlphaApp)
        setup_logging()

        from alpha.transcription.japanese_final_chunk_stabilizer import (
            install_japanese_stabilizer_hooks,
        )

        install_japanese_stabilizer_hooks(AlphaApp)

        from alpha.utils.tk_thread_guard import install_tk_thread_guard

        install_tk_thread_guard(AlphaApp)

        jp_accuracy_log("ROOT_WINDOW_CREATE_DONE")
        jp_accuracy_log("MAIN_WINDOW_CREATE_BEGIN")
        app = AlphaApp()
        jp_accuracy_log("MAIN_WINDOW_CREATE_DONE")
        diag_log("startup", "after_ui_create", {"ui_create_ms": None})

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
            app.after(50, lambda: __import__("threading").Thread(
                target=_deferred_post_ui_startup, name="DeferredStartup", daemon=True
            ).start())

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
        print(f"Fatal error: {exc}")
        sys.exit(1)

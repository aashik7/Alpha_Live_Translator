"""Conservative transcript duplicate protection for v3.2.1 hotfix."""

import re
import time
import traceback

import tkinter as tk

from alpha.constants import (
    DEBUG_TEAMS_DIAGNOSTICS,
    UI_MAX_UPDATES_PER_TICK,
    UI_QUEUE_POLL_MS,
    UI_UPDATE_INTERVAL_MS,
)


class TranscriptStabilityCounters:
    """Lightweight counters for transcript decisions."""

    def __init__(self):
        self.skipped = 0
        self.added = 0
        self.updated = 0
        self.copy_export_word_count = 0

    def reset(self):
        self.skipped = 0
        self.added = 0
        self.updated = 0
        self.copy_export_word_count = 0

    def as_dict(self):
        return {
            "skipped": self.skipped,
            "added": self.added,
            "updated": self.updated,
            "copy_export_word_count": self.copy_export_word_count,
        }


def normalize_for_compare(text: str) -> str:
    """Lowercase, strip, collapse whitespace, remove punctuation for comparison."""
    cleaned = (text or "").lower().strip()
    cleaned = re.sub(r"[^\w\s]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def decide_transcript_action(previous_text: str | None, current_text: str) -> tuple[str, str | None]:
    """
    Return (action, text) using strict hotfix decision order.

    Actions: skip, add, update
    """
    current = (current_text or "").strip()
    if not current:
        return ("skip", None)

    previous = (previous_text or "").strip() if previous_text else ""
    if not previous:
        return ("add", current)

    prev_n = normalize_for_compare(previous)
    curr_n = normalize_for_compare(current)

    if curr_n == prev_n:
        return ("skip", None)

    if curr_n in prev_n:
        return ("skip", None)

    if prev_n in curr_n:
        return ("update", current)

    if curr_n.startswith(prev_n):
        return ("update", current)

    if prev_n.startswith(curr_n):
        return ("skip", None)

    return ("add", current)


def apply_transcript_sequence(texts: list[str], speaker: int = 1) -> list[str]:
    """Apply hotfix stabilization to a sequence of finals (test helper, no GUI)."""
    lines: list[str] = []
    last_by_speaker: dict[int, str] = {}

    for text in texts:
        previous = last_by_speaker.get(speaker)
        action, result = decide_transcript_action(previous, text)
        if action == "skip" or not result:
            continue
        if action == "update":
            if lines:
                lines[-1] = result
            else:
                lines.append(result)
        else:
            lines.append(result)
        last_by_speaker[speaker] = result

    return lines


class DuplicateProtectionMixin:
    """Mixin providing conservative transcript display from TranscriptStore."""

    def _ensure_stability_state(self):
        if not hasattr(self, "_transcript_stability_counters"):
            self._transcript_stability_counters = TranscriptStabilityCounters()

    def _render_transcript_from_store(self):
        """Re-render the live transcript textbox from TranscriptStore."""
        box = getattr(self, "initial_verse_box", None)
        if box is None:
            return

        clean = ""
        if hasattr(self, "transcript_store") and self.transcript_store is not None:
            clean = self.transcript_store.get_clean_text()

        box.configure(state="normal")
        box.delete("1.0", "end")
        if clean.strip():
            box.insert("1.0", clean)
            if not clean.endswith("\n"):
                box.insert("end", "\n")
        elif hasattr(self, "_show_text_placeholder"):
            self._show_text_placeholder(box)
        box.configure(state="disabled")
        box.see(tk.END)

        scrollbar = getattr(box, "_scrollbar", None)
        if scrollbar is not None and hasattr(self, "check_scrollbar_visibility"):
            self.check_scrollbar_visibility(box, scrollbar)

    def _apply_transcript_to_store(self, speaker_num, text, timestamp=None, action: str = "add"):
        if not hasattr(self, "transcript_store") or self.transcript_store is None:
            return

        source_language = None
        target_language = None
        if hasattr(self, "source_language"):
            try:
                source_language = self.source_language.get()
            except Exception:
                pass
        if hasattr(self, "target_language"):
            try:
                target_language = self.target_language.get()
            except Exception:
                pass

        if action == "update":
            updated = self.transcript_store.update_last_segment(
                speaker=speaker_num,
                text=text,
                timestamp=timestamp,
            )
            if updated:
                self._transcript_stability_counters.updated += 1
            else:
                self.transcript_store.add_segment(
                    speaker=speaker_num,
                    text=text,
                    timestamp=timestamp,
                    source_language=source_language,
                    target_language=target_language,
                )
                self._transcript_stability_counters.added += 1
        else:
            self.transcript_store.add_segment(
                speaker=speaker_num,
                text=text,
                timestamp=timestamp,
                source_language=source_language,
                target_language=target_language,
            )
            self._transcript_stability_counters.added += 1

    def _display_transcript_item(self, item):
        """Accept one final transcript item, update store, re-render UI."""
        self._ensure_stability_state()

        if item.get("is_final") is False:
            return

        speaker_num = item.get("speaker", 1)
        if speaker_num is not None and str(speaker_num).isdigit():
            speaker_num = int(speaker_num)

        text = (item.get("text") or "").strip()
        if not text:
            return

        # fixes TASK_5_FINAL_CLEANUP_REPORT.md Fix 2: TranscriptStore itself
        # has no channel/canonical_utterance_id concept (would require
        # modifying transcript_store.py, outside this fix's file scope —
        # see TASK_2E_FINDINGS.md item 3). Verify with what IS available:
        # only trust a positionally-found "last segment for this speaker"
        # as this item's previous_text when the identity registry confirms
        # this canonical_utterance_id has been observed before (a genuine
        # revision) — never for a first-time utterance, and always via the
        # hard-speaker-boundary-safe lookup (get_last_segment_if_active,
        # Task 2F) instead of the plain positional one.
        session_id = str(item.get("session_id") or getattr(self, "_live_session_id", "") or "")
        channel_index = item.get("channel_index", item.get("channel"))
        canonical_utterance_id = str(item.get("canonical_utterance_id") or "")
        allow_previous_lookup = True
        if canonical_utterance_id:
            try:
                from alpha.transcription.canonical_identity_registry import (
                    resolve_canonical_record_id,
                )

                allow_previous_lookup = bool(
                    resolve_canonical_record_id(
                        session_id=session_id,
                        channel_index=channel_index,
                        canonical_utterance_id=canonical_utterance_id,
                    )
                )
            except Exception:
                allow_previous_lookup = False

        previous_text = None
        if (
            allow_previous_lookup
            and hasattr(self, "transcript_store")
            and self.transcript_store is not None
        ):
            segment = self.transcript_store.get_last_segment_if_active(speaker_num)
            if segment is not None:
                previous_text = segment.text

        action, result_text = decide_transcript_action(previous_text, text)
        # Utterance lifecycle / authoritative same-utterance correction must
        # replace the active permanent record — never append a second version.
        life_decision = str(item.get("lifecycle_decision") or "").upper()
        if life_decision in ("SUPERSEDE_PREVIOUS", "REPLACE_ACTIVE", "EXTEND_ACTIVE"):
            if previous_text:
                action, result_text = "update", text
            else:
                action, result_text = "add", text
        elif item.get("superseded_record_id") or item.get("revision_target_id"):
            if previous_text:
                action, result_text = "update", text
        if action == "skip" or not result_text:
            self._transcript_stability_counters.skipped += 1
            return

        # Canonical Stable commit is the translation authority.
        # Japanese assembler commits before publish (canonical_record_id set).
        # English / generic finals must commit here before UI + DeepL.
        # fixes TASK_5_FINAL_CLEANUP_REPORT.md Fix 2 / REPAIR_PLAN.md Phase 1
        # rule ("no operation may select a target because it is merely the
        # latest active record" — applied here to a claim of "already
        # committed"): verify the claim against the identity registry
        # instead of trusting caller-supplied flags outright. This is now
        # safe for Japanese too — Fix 1 made the Japanese assembler register
        # identity via the same accept_boundary_proposal path English's
        # fallback below already used, so the registry has a real entry to
        # verify against instead of always being empty for Japanese items.
        raw_committed_claim = bool(
            item.get("canonical_record_id")
            or item.get("_jp_continuity_assembler")
            or item.get("canonical_ledger_committed")
        )
        already_committed = False
        if raw_committed_claim:
            if canonical_utterance_id:
                try:
                    from alpha.transcription.canonical_identity_registry import (
                        resolve_canonical_record_id as _resolve_for_trust_gate,
                    )

                    exact_record_id = str(
                        _resolve_for_trust_gate(
                            session_id=session_id,
                            channel_index=channel_index,
                            canonical_utterance_id=canonical_utterance_id,
                        )
                        or ""
                    )
                except Exception:
                    exact_record_id = ""
                claimed_record_id = str(item.get("canonical_record_id") or "")
                if exact_record_id and (
                    not claimed_record_id or claimed_record_id == exact_record_id
                ):
                    already_committed = True
                else:
                    try:
                        from alpha.utils.japanese_accuracy_log import jp_accuracy_log as _jal

                        _jal(
                            "ALREADY_COMMITTED_CLAIM_UNVERIFIED",
                            session_id=session_id,
                            channel_index=channel_index,
                            canonical_utterance_id=canonical_utterance_id,
                            claimed_record_id=claimed_record_id,
                            registry_record_id=exact_record_id,
                        )
                    except Exception:
                        pass
            # No canonical_utterance_id at all: the claim is unverifiable —
            # fail closed (already_committed stays False), same rule Task 1
            # applied to canonical record targeting.
        if not already_committed:
            try:
                from alpha.transcription.canonical_identity_registry import (
                    assign_canonical_record_id,
                    observe_identity,
                    resolve_canonical_record_id,
                )
                from alpha.transcription.pipeline_commit_transaction import (
                    execute_pipeline_commit,
                )
                from alpha.utils.pipeline_integrity import PipelineIntegrityError
                from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                session_id = str(
                    item.get("session_id")
                    or getattr(self, "_live_session_id", "")
                    or ""
                )
                channel_index = item.get("channel_index", item.get("channel"))
                canonical_utterance_id = str(item.get("canonical_utterance_id") or "")
                provider_utterance_id = str(
                    item.get("provider_utterance_id")
                    or item.get("request_id")
                    or item.get("event_id")
                    or ""
                )
                source_version = int(item.get("source_version") or 1)
                canonical_decision = str(
                    item.get("canonical_decision")
                    or item.get("lifecycle_decision")
                    or ("SUPERSEDE" if action == "update" else "CREATE_NEW")
                ).upper()
                identity = observe_identity(
                    session_id=session_id,
                    channel_index=channel_index,
                    canonical_utterance_id=canonical_utterance_id,
                    provider_utterance_id=provider_utterance_id,
                    source_version=source_version,
                    decision=canonical_decision,
                    text=result_text,
                    lifecycle_state=str(
                        item.get("lifecycle_state")
                        or ("COMMITTED" if item.get("speech_final") else "ACTIVE_FINAL_CHUNK")
                    ),
                    translation_eligible=bool(item.get("translation_eligible", True)),
                )
                if not identity.accepted:
                    self._transcript_stability_counters.skipped += 1
                    jp_accuracy_log(
                        "IDENTITY_REJECTION",
                        reason=identity.reason,
                        session_id=session_id,
                        channel_index=channel_index,
                        canonical_utterance_id=canonical_utterance_id,
                        source_version=source_version,
                    )
                    return
                if identity.duplicate:
                    self._transcript_stability_counters.skipped += 1
                    jp_accuracy_log(
                        "DUPLICATE_IGNORE",
                        reason=identity.reason,
                        session_id=session_id,
                        channel_index=channel_index,
                        canonical_utterance_id=canonical_utterance_id,
                        source_version=source_version,
                        canonical_record_id=(identity.entry or {}).get("canonical_record_id", ""),
                    )
                    return

                applied = "append"
                if action == "update":
                    applied = "revise"
                if str(item.get("revision_target_id") or "").strip():
                    applied = "revise"
                revision_target = str(
                    item.get("revision_target_id")
                    or item.get("superseded_record_id")
                    or ""
                )
                if applied == "revise":
                    exact_target = str(
                        resolve_canonical_record_id(
                            session_id=session_id,
                            channel_index=channel_index,
                            canonical_utterance_id=canonical_utterance_id,
                        )
                        or ""
                    )
                    if revision_target and exact_target and revision_target != exact_target:
                        self._transcript_stability_counters.skipped += 1
                        jp_accuracy_log(
                            "IDENTITY_REJECTION",
                            reason="ambiguous_revision_target",
                            session_id=session_id,
                            channel_index=channel_index,
                            canonical_utterance_id=canonical_utterance_id,
                            source_version=source_version,
                            revision_target_id=revision_target,
                            exact_target_id=exact_target,
                        )
                        return
                    revision_target = exact_target or revision_target
                    if not revision_target:
                        self._transcript_stability_counters.skipped += 1
                        jp_accuracy_log(
                            "FALLBACK_BLOCKED",
                            reason="missing_exact_revision_target",
                            session_id=session_id,
                            channel_index=channel_index,
                            canonical_utterance_id=canonical_utterance_id,
                            source_version=source_version,
                        )
                        return
                txn = execute_pipeline_commit(
                    speaker=int(speaker_num or 1),
                    assembler_text=result_text,
                    final_text=result_text,
                    requested_action=applied,
                    applied_action=applied,
                    revision_target_id=revision_target,
                    source_raw_event_ids=list(item.get("source_raw_event_ids") or []),
                    commit_reason=str(
                        item.get("stabilizer_reason")
                        or item.get("lifecycle_commit_reason")
                        or "ui_stable_final"
                    ),
                    metadata={
                        "source": "duplicate_protection_display",
                        "session_id": session_id,
                        "channel_index": channel_index,
                        "canonical_utterance_id": canonical_utterance_id,
                        "provider_utterance_id": provider_utterance_id,
                        "source_version": source_version,
                        "canonical_decision": canonical_decision,
                        "idempotency_decision": canonical_decision,
                        "translation_eligible": bool(item.get("translation_eligible", True)),
                        "synthetic_record": not bool(item.get("source_raw_event_ids")),
                    },
                )
                if not txn.success:
                    self._transcript_stability_counters.skipped += 1
                    try:
                        jp_accuracy_log(
                            "STABLE_COMMIT_BEFORE_TRANSLATION_REJECTED",
                            failure_reason=txn.failure_reason,
                            text_preview=result_text[:120],
                        )
                    except Exception:
                        pass
                    # Count frozen-ledger failures for session evidence.
                    if "frozen" in str(txn.failure_reason or "").lower():
                        self._frozen_ledger_error_count = int(
                            getattr(self, "_frozen_ledger_error_count", 0) or 0
                        ) + 1
                    return
                item["canonical_record_id"] = txn.record_id
                item["canonical_ledger_committed"] = True
                assign_result = assign_canonical_record_id(
                    session_id=session_id,
                    channel_index=channel_index,
                    canonical_utterance_id=canonical_utterance_id,
                    canonical_record_id=str(txn.record_id or ""),
                )
                if not assign_result.accepted:
                    self._transcript_stability_counters.skipped += 1
                    jp_accuracy_log(
                        "IDENTITY_REJECTION",
                        reason=assign_result.reason,
                        session_id=session_id,
                        channel_index=channel_index,
                        canonical_utterance_id=canonical_utterance_id,
                        canonical_record_id=str(txn.record_id or ""),
                    )
                    return
                if txn.evidence_write_failed or txn.metrics_write_failed:
                    jp_accuracy_log(
                        "COMMIT_APPLIED",
                        session_id=session_id,
                        channel_index=channel_index,
                        canonical_utterance_id=canonical_utterance_id,
                        canonical_record_id=str(txn.record_id or ""),
                        evidence_write_failed=txn.evidence_write_failed,
                        metrics_write_failed=txn.metrics_write_failed,
                    )
            except PipelineIntegrityError as exc:
                self._transcript_stability_counters.skipped += 1
                if "frozen" in str(exc).lower():
                    self._frozen_ledger_error_count = int(
                        getattr(self, "_frozen_ledger_error_count", 0) or 0
                    ) + 1
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log(
                        "STABLE_COMMIT_BEFORE_TRANSLATION_REJECTED",
                        failure_reason=str(exc),
                        text_preview=result_text[:120],
                    )
                except Exception:
                    pass
                return
            except Exception as exc:
                self._transcript_stability_counters.skipped += 1
                try:
                    from alpha.utils.japanese_accuracy_log import jp_accuracy_log

                    jp_accuracy_log(
                        "STABLE_COMMIT_BEFORE_TRANSLATION_REJECTED",
                        failure_reason=f"{type(exc).__name__}:{exc}",
                        text_preview=result_text[:120],
                    )
                except Exception:
                    pass
                return

        timestamp = item.get("timestamp")
        self._apply_transcript_to_store(
            speaker_num,
            result_text,
            timestamp=timestamp,
            action=action,
        )
        # Translation is submitted only from the UI segment hooks below,
        # and only after a successful canonical Stable commit above.
        canonical_utterance_id = str(item.get("canonical_utterance_id") or "")
        source_version = int(item.get("source_version") or 1)
        translation_eligible = bool(item.get("translation_eligible", True))
        if not translation_eligible:
            self._render_transcript_from_store()
            return
        if action == "update" and hasattr(self, "_on_store_segment_updated"):
            self._on_store_segment_updated(
                speaker_num,
                result_text,
                canonical_utterance_id=canonical_utterance_id,
                source_version=source_version,
                source_record_id=str(item.get("canonical_record_id") or ""),
            )
        elif hasattr(self, "_on_store_segment_added"):
            self._on_store_segment_added(
                speaker_num,
                result_text,
                canonical_utterance_id=canonical_utterance_id,
                source_version=source_version,
                source_record_id=str(item.get("canonical_record_id") or ""),
            )
        else:
            self._render_transcript_from_store()
            if hasattr(self, "submit_text_for_translation"):
                try:
                    self.submit_text_for_translation(
                        result_text,
                        speaker=speaker_num,
                        canonical_utterance_id=canonical_utterance_id,
                        source_version=source_version,
                        source_record_id=str(item.get("canonical_record_id") or ""),
                    )
                except Exception as exc:
                    print(f"[Translation] submit failed: {exc}")

    def _process_ui_queue_once(self):
        """Process one transcript queue batch (no reschedule)."""
        tick_start = time.perf_counter()
        queued_items = 0
        processed_items = 0
        chars_added = 0
        deferred_items = 0
        try:
            self._ensure_stability_state()
            if not hasattr(self, "last_displayed_speaker"):
                self.last_displayed_speaker = None

            queued_items = self.transcript_queue.qsize()
            max_per_poll = UI_MAX_UPDATES_PER_TICK
            while not self.transcript_queue.empty() and processed_items < max_per_poll:
                item = self.transcript_queue.get()
                if isinstance(item, list):
                    items_to_process = item
                else:
                    items_to_process = [item]

                for sub_item in items_to_process:
                    text_len = len((sub_item.get("text") or ""))
                    self._display_transcript_item(sub_item)
                    chars_added += text_len
                processed_items += 1

            deferred_items = self.transcript_queue.qsize()
        except Exception as e:
            print(f"[ERROR] Processing UI queue: {e}")
            traceback.print_exc()

        elapsed_ms = round((time.perf_counter() - tick_start) * 1000, 1)
        if elapsed_ms > 50 and hasattr(self, "_perf_log_ui_update_batch"):
            self._perf_log_ui_update_batch(
                queued_items=queued_items,
                processed_items=processed_items,
                elapsed_ms=elapsed_ms,
                transcript_chars_added=chars_added,
                skipped_or_deferred_items=deferred_items,
            )

    def process_ui_queue(self):
        """Process transcript queue; reschedule via host scheduler when available."""
        self._process_ui_queue_once()
        scheduler = getattr(self, "_schedule_ui_queue_tick", None)
        if callable(scheduler):
            scheduler()
        else:
            self.after(UI_UPDATE_INTERVAL_MS, self.process_ui_queue)

    def reset_transcript_stability_state(self):
        """Reset stabilization counters."""
        self._ensure_stability_state()
        self._transcript_stability_counters.reset()
        self.last_displayed_speaker = None

    def log_copy_export_stats(self, clean_text: str, segment_count: int):
        """Print copy/export diagnostics without secrets."""
        self._ensure_stability_state()
        word_count = len(clean_text.split()) if clean_text else 0
        self._transcript_stability_counters.copy_export_word_count = word_count
        counters = self._transcript_stability_counters.as_dict()
        print(
            f"[Transcript Export] words={word_count}, segments={segment_count}, "
            f"counters={counters}"
        )

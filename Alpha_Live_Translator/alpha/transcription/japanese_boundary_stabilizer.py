"""Japanese Boundary Stabilizer — stable-layer sentence boundary improvement (8.5.24)."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional

from alpha.constants import (
    APP_VERSION,
    BOUNDARY_STABILIZER_HOLD_MS_DEFAULT,
    BOUNDARY_STABILIZER_HOLD_MS_MAX,
    BOUNDARY_STABILIZER_LEADING_FRAGMENT_MAX_CHARS,
    BOUNDARY_STABILIZER_PENDING_MERGE_MAX_CHARS,
    BOUNDARY_STABILIZER_SAFE_MERGE_MAX_CHARS,
    BOUNDARY_MERGE_REVISION_ENABLED,
    BOUNDARY_SUMMARY_PATH_FIX_ENABLED,
    JAPANESE_BOUNDARY_DECISION_LOG_ENABLED,
    JAPANESE_BOUNDARY_STABILIZER_ENABLED,
    JAPANESE_BOUNDARY_STABILIZER_MODE,
    JAPANESE_DUPLICATE_CONTINUATION_GUARD_ENABLED,
    JAPANESE_LEADING_FRAGMENT_HOLD_ENABLED,
    JAPANESE_MIDLINE_PUNCTUATION_CLEANUP_ENABLED,
    JAPANESE_SAFE_MERGE_ENABLED,
    JAPANESE_STOP_FLUSH_BOUNDARY_SAFE,
)
from alpha.transcription.japanese_stable_accuracy import count_japanese_chars, is_clear_sentence
from alpha.transcription.speaker_boundary_guard import speakers_confirmed_same
from alpha.utils.cjk_text import compact_cjk_for_compare

_LEADING_PARTICLES: tuple[str, ...] = (
    "の",
    "が",
    "は",
    "て",
    "を",
    "に",
    "で",
    "から",
    "と",
    "か",
    "も",
    "では",
    "そして",
    "ですから",
    "なので",
)

_VALID_TRANSITION_PREFIXES: tuple[str, ...] = (
    "では皆さん",
    "では一緒に",
    "そして今回",
    "ですから",
    "なので",
)

_INCOMPLETE_ENDINGS: tuple[str, ...] = (
    "ので",
    "けど",
    "たり",
    "つつ",
    "という",
    "と言って",
    "について",
    "に対して",
    "になりますが",
    "して",
    "されて",
    "いただいて",
    "おりまして",
    "申し訳ございません",
    "よろしくお願いいたします",
    "の",
    "が",
    "は",
    "を",
    "に",
    "で",
    "と",
    "から",
)

_STRONG_TERMINALS: tuple[str, ...] = (
    "。",
    "？",
    "！",
    "ですね。",
    "ます。",
    "ました。",
    "ください。",
    "でしょう。",
    "と思います。",
    "になります。",
)

_PUNCT_ARTIFACT_RE = [
    (re.compile(r"。、+"), "。"),
    (re.compile(r"、。+"), "。"),
    (re.compile(r"。{2,}"), "。"),
    (re.compile(r"、{2,}"), "、"),
]

_stabilizer: Optional["JapaneseBoundaryStabilizer"] = None


def _jp_log(event: str, **fields: Any) -> None:
    try:
        from alpha.utils.japanese_accuracy_log import jp_accuracy_log

        jp_accuracy_log(event, **fields)
    except Exception:
        pass


def _run_id() -> str:
    try:
        from alpha.utils.run_identity import get_current_run_identity

        ident = get_current_run_identity()
        if ident is not None:
            return ident.run_id
    except Exception:
        pass
    return ""


def _decision_log_path() -> Path:
    try:
        from alpha.constants import BOUNDARY_SUMMARY_PATH_FIX_ENABLED
        from alpha.utils.troubleshooting_paths import get_run_folder

        run = get_run_folder()
        if run:
            return Path(run) / "accuracy" / "boundary_stabilizer_decisions.jsonl"
    except Exception:
        pass
    return Path("troubleshooting/runs/_pending/accuracy/boundary_stabilizer_decisions.jsonl")


def _summary_path() -> Path:
    try:
        from alpha.utils.troubleshooting_paths import get_run_folder

        run = get_run_folder()
        if run:
            return Path(run) / "accuracy" / "boundary_stabilizer_summary.json"
    except Exception:
        pass
    return Path("troubleshooting/runs/_pending/accuracy/boundary_stabilizer_summary.json")


def is_leading_fragment_line(text: str) -> tuple[bool, str]:
    segment = (text or "").strip()
    segment = re.sub(r"^\[Speaker\s+\d+\]\s*", "", segment)
    if not segment:
        return False, ""
    for prefix in _VALID_TRANSITION_PREFIXES:
        if segment.startswith(prefix) and count_japanese_chars(segment) >= 12:
            return False, ""
    for particle in sorted(_LEADING_PARTICLES, key=len, reverse=True):
        if segment.startswith(particle):
            return True, particle
    return False, ""


def has_incomplete_ending(text: str) -> tuple[bool, str]:
    segment = (text or "").strip()
    if not segment:
        return False, ""
    if is_clear_sentence(segment):
        return False, ""
    for suffix in sorted(_INCOMPLETE_ENDINGS, key=len, reverse=True):
        if segment.endswith(suffix):
            return True, suffix
    if segment.endswith(("ます", "ました", "です", "でした")) and not segment.endswith("。"):
        return True, segment[-2:]
    return False, ""


def has_strong_terminal_boundary(text: str) -> bool:
    segment = (text or "").strip()
    if not segment:
        return False
    if is_clear_sentence(segment):
        return True
    for term in _STRONG_TERMINALS:
        if segment.endswith(term):
            return True
    return False


def cleanup_midline_punctuation(text: str) -> tuple[str, bool]:
    if not JAPANESE_MIDLINE_PUNCTUATION_CLEANUP_ENABLED:
        return text, False
    out = text or ""
    changed = False
    for pattern, repl in _PUNCT_ARTIFACT_RE:
        new = pattern.sub(repl, out)
        if new != out:
            changed = True
            out = new
    out = re.sub(r"\s+([、。！？])", r"\1", out)
    out = re.sub(r"([、。！？])\s+", r"\1", out)
    if out != text:
        changed = True
    if changed:
        _jp_log("MIDLINE_PUNCTUATION_ARTIFACT_CLEANED", before=text[:80], after=out[:80])
    return out, changed


def safe_merge_text(previous: str, current: str) -> tuple[str, str, bool]:
    prev = (previous or "").strip()
    cur = (current or "").strip()
    if not prev or not cur:
        return cur or prev, "empty_side", False
    combined_len = count_japanese_chars(prev + cur)
    if combined_len > BOUNDARY_STABILIZER_SAFE_MERGE_MAX_CHARS:
        return cur, "too_long", False
    if prev.endswith(("ます", "ました")) and cur.startswith("て"):
        merged = prev + "。" + cur
        return merged, "inserted_punctuation_masu_te", True
    if prev.endswith("ます") and cur.startswith("が"):
        return prev + cur, "grammatical_ga_continuation", True
    if cur.startswith(("を", "に", "で", "から", "の", "が", "は", "て")):
        return prev + cur, "particle_continuation", True
    if not has_strong_terminal_boundary(prev):
        return prev + cur, "incomplete_previous", True
    return cur, "uncertain", False


def duplicate_continuation_ratio(previous: str, current: str) -> float:
    prev_c = compact_cjk_for_compare(previous or "", "ja")
    cur_c = compact_cjk_for_compare(current or "", "ja")
    if not prev_c or not cur_c:
        return 0.0
    if cur_c in prev_c:
        return 1.0
    if prev_c in cur_c:
        return len(prev_c) / max(len(cur_c), 1)
    shared = 0
    for i in range(min(len(prev_c), len(cur_c))):
        if prev_c[i] == cur_c[i]:
            shared += 1
        else:
            break
    return shared / max(len(cur_c), 1)


class JapaneseBoundaryStabilizer:
    """Stable-layer boundary stabilizer — does not mutate raw Deepgram."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._pending = ""
        self._pending_since = 0.0
        self._pending_speaker: Any = None
        self._previous_line = ""
        self._previous_speaker: Any = None
        self._input_count = 0
        self._output_count = 0
        self._held_count = 0
        self._merge_previous_count = 0
        self._merge_pending_count = 0
        self._duplicate_suppressed_count = 0
        self._punctuation_cleanup_count = 0
        self._timeout_emit_count = 0
        self._stop_flush_emit_count = 0
        self._stop_flush_drop_count = 0
        self._leading_before = 0
        self._leading_after = 0
        self._punct_before = 0
        self._punct_after = 0
        self._dup_before = 0
        self._dup_after = 0
        self._translation_ready_before = 0
        self._translation_ready_after = 0
        self._shadow_before_lines: list[str] = []

    def set_previous_line(self, text: str, speaker: Any = None) -> None:
        self._previous_line = (text or "").strip()
        self._previous_speaker = speaker

    def _estimate_translation_ready(self, text: str) -> bool:
        segment = (text or "").strip()
        if count_japanese_chars(segment) < 8:
            return False
        if is_leading_fragment_line(segment)[0]:
            return False
        if has_incomplete_ending(segment)[0] and not is_clear_sentence(segment):
            return False
        return has_strong_terminal_boundary(segment) or count_japanese_chars(segment) >= 20

    def _record_input_metrics(self, text: str) -> None:
        self._input_count += 1
        if is_leading_fragment_line(text)[0]:
            self._leading_before += 1
        if re.search(r"。、|、。|\.\.|、、", text or ""):
            self._punct_before += 1
        if self._previous_line and duplicate_continuation_ratio(self._previous_line, text) >= 0.7:
            self._dup_before += 1
        if self._estimate_translation_ready(text):
            self._translation_ready_before += 1

    def _record_output_metrics(self, text: str) -> None:
        if is_leading_fragment_line(text)[0]:
            self._leading_after += 1
        if re.search(r"。、|、。|\.\.|、、", text or ""):
            self._punct_after += 1
        if self._previous_line and duplicate_continuation_ratio(self._previous_line, text) >= 0.7:
            self._dup_after += 1
        if self._estimate_translation_ready(text):
            self._translation_ready_after += 1

    def _map_output_contract(
        self,
        *,
        emit_now: bool,
        action: str,
        reason: str,
        output_text: str,
        update_previous: bool = False,
        suppress: bool = False,
    ) -> dict[str, Any]:
        if suppress or action == "suppress_duplicate_continuation":
            output_action = "suppress_current"
            should_revise = False
            should_append = False
            should_emit = False
            should_export = False
            _jp_log("BOUNDARY_OUTPUT_SUPPRESS_CURRENT", reason=reason)
        elif not emit_now:
            output_action = "hold_pending"
            should_revise = False
            should_append = False
            should_emit = False
            should_export = False
            _jp_log("BOUNDARY_OUTPUT_HOLD_PENDING", reason=reason)
        elif update_previous or action in ("merge_with_previous", "merge_pending_and_current"):
            output_action = "revise_previous_line"
            should_revise = True
            should_append = False
            should_emit = True
            should_export = True
            _jp_log("BOUNDARY_OUTPUT_REVISE_PREVIOUS_LINE", reason=reason)
        elif action == "cleanup_punctuation_only":
            output_action = "punctuation_cleanup_revision" if update_previous else "append_new_line"
            should_revise = update_previous
            should_append = not update_previous
            should_emit = True
            should_export = True
            _jp_log(
                "BOUNDARY_OUTPUT_PUNCTUATION_REVISION"
                if should_revise
                else "BOUNDARY_OUTPUT_APPEND_NEW_LINE"
            )
        else:
            output_action = "append_new_line"
            should_revise = False
            should_append = True
            should_emit = True
            should_export = True
            _jp_log("BOUNDARY_OUTPUT_APPEND_NEW_LINE", reason=reason)

        if BOUNDARY_MERGE_REVISION_ENABLED:
            _jp_log("BOUNDARY_OUTPUT_CONTRACT_APPLIED", output_action=output_action)

        return {
            "output_action": output_action,
            "should_revise": should_revise,
            "should_append": should_append,
            "suppress_current": suppress or output_action == "suppress_current",
            "replaces_previous": should_revise,
            "should_emit_to_ui": should_emit,
            "should_export": should_export,
            "revision_reason": reason,
        }

    def _build_result(
        self,
        *,
        emit_now: bool,
        output_text: str,
        pending_text: str,
        action: str,
        reason: str,
        before_text: str,
        confidence: str = "medium",
        risk_level: str = "low",
        update_previous: bool = False,
        stop_flush: bool = False,
        commit_reason: str = "",
        speaker_prefix: str = "",
    ) -> dict[str, Any]:
        out_text = self._with_speaker(output_text, speaker_prefix)
        pend_text = self._with_speaker(pending_text, speaker_prefix) if pending_text else pending_text
        contract = self._map_output_contract(
            emit_now=emit_now,
            action=action,
            reason=reason,
            output_text=out_text,
            update_previous=update_previous,
            suppress=action == "suppress_duplicate_continuation",
        )
        return {
            "emit_now": emit_now,
            "output_text": out_text,
            "pending_text": pend_text,
            "action": action,
            "reason": reason,
            "before_text": before_text,
            "after_text": out_text if emit_now else pend_text,
            "confidence": confidence,
            "risk_level": risk_level,
            "update_previous": update_previous,
            "stop_flush": stop_flush,
            "commit_reason": commit_reason,
            "raw_mutation": False,
            "previous_line_excerpt": (self._previous_line or "")[:80],
            "pending_before": self._pending,
            **contract,
        }

    def _with_speaker(self, text: str, speaker_prefix: str) -> str:
        body = (text or "").strip()
        if not body:
            return ""
        if speaker_prefix and not body.startswith("[Speaker"):
            return f"{speaker_prefix}{body}"
        return body

    def process(
        self,
        input_text: str,
        *,
        commit_reason: str = "",
        previous_line: str | None = None,
        previous_speaker: Any = None,
        speaker: Any = None,
        stop_flush: bool = False,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        if not JAPANESE_BOUNDARY_STABILIZER_ENABLED:
            cleaned, _ = cleanup_midline_punctuation(input_text)
            return self._build_result(
                emit_now=True,
                output_text=cleaned,
                pending_text="",
                action="emit_unchanged",
                reason="stabilizer_disabled",
                before_text=input_text,
                commit_reason=commit_reason,
            )

        now = timestamp if timestamp is not None else time.monotonic()
        if previous_line is not None:
            prev_body = re.sub(r"^\[Speaker\s+\d+\]\s*", "", (previous_line or "").strip())
            self._previous_line = prev_body
            # fixes TASK_2C_REPORT.md: previous-line speaker must be tracked
            # alongside the text so later merge decisions can be gated on it.
            self._previous_speaker = previous_speaker

        text = (input_text or "").strip()
        speaker_prefix = ""
        sp = re.match(r"^(\[Speaker\s+\d+\]\s*)", text)
        if sp:
            speaker_prefix = sp.group(1)
            text = text[len(speaker_prefix) :].strip()
        if not text:
            return self._build_result(
                emit_now=False,
                output_text="",
                pending_text=self._pending,
                action="stop_flush_drop_empty" if stop_flush else "emit_unchanged",
                reason="empty_input",
                before_text=input_text,
                stop_flush=stop_flush,
                commit_reason=commit_reason,
            )

        self._record_input_metrics(text)
        _jp_log("BOUNDARY_STABILIZER_DECISION", input_preview=text[:60], commit_reason=commit_reason)

        if self._pending:
            pending_age_ms = (now - self._pending_since) * 1000.0 if self._pending_since else 0
            # fixes TASK_2C_REPORT.md: speaker identity checked BEFORE any
            # pending-merge text logic. A pending fragment can never be
            # merged with a different (or unknown) speaker's incoming
            # fragment -- emit the pending fragment on its own first.
            if not speakers_confirmed_same(self._pending_speaker, speaker):
                emit_pending = self._pending
                emit_pending_speaker = self._pending_speaker
                self._pending = text
                self._pending_speaker = speaker
                self._pending_since = now
                cleaned, _ = cleanup_midline_punctuation(emit_pending)
                self._output_count += 1
                self._record_output_metrics(cleaned)
                _jp_log(
                    "SPEAKER_BOUNDARY_PENDING_FLUSHED",
                    pending_speaker=emit_pending_speaker,
                    candidate_speaker=speaker,
                    text_preview=cleaned[:80],
                )
                result = self._build_result(
                    emit_now=True,
                    output_text=cleaned,
                    pending_text=text,
                    action="hold_leading_fragment",
                    reason="speaker_change_pending_flush",
                    before_text=emit_pending,
                    confidence="low",
                    commit_reason=commit_reason,
                    speaker_prefix=speaker_prefix,
                )
                self._log_decision(result, emitted_to_ui=True)
                return result
            merged, merge_reason, ok = safe_merge_text(self._pending, text)
            if ok and count_japanese_chars(merged) <= BOUNDARY_STABILIZER_PENDING_MERGE_MAX_CHARS:
                self._pending = ""
                self._pending_speaker = None
                self._pending_since = 0.0
                self._merge_pending_count += 1
                cleaned, punct_changed = cleanup_midline_punctuation(merged)
                if punct_changed:
                    self._punctuation_cleanup_count += 1
                self._output_count += 1
                self._record_output_metrics(cleaned)
                _jp_log("BOUNDARY_STABILIZER_MERGE", action="merge_pending_and_current", reason=merge_reason)
                _jp_log("INCOMPLETE_ENDING_MERGED_NEXT", merged_preview=cleaned[:80])
                result = self._build_result(
                    emit_now=True,
                    output_text=cleaned,
                    pending_text="",
                    action="merge_pending_and_current",
                    reason=merge_reason,
                    before_text=input_text,
                    commit_reason=commit_reason,
                    speaker_prefix=speaker_prefix,
                )
                self._log_decision(result, emitted_to_ui=True)
                return result
            if pending_age_ms >= BOUNDARY_STABILIZER_HOLD_MS_MAX:
                emit_pending = self._pending
                self._pending = text
                self._pending_speaker = speaker
                self._pending_since = now
                self._timeout_emit_count += 1
                cleaned, _ = cleanup_midline_punctuation(emit_pending)
                self._output_count += 1
                self._record_output_metrics(cleaned)
                _jp_log("INCOMPLETE_ENDING_TIMEOUT_EMITTED", text_preview=cleaned[:80])
                result = self._build_result(
                    emit_now=True,
                    output_text=cleaned,
                    pending_text=text,
                    action="hold_leading_fragment",
                    reason="pending_timeout_emit",
                    before_text=emit_pending,
                    confidence="low",
                    commit_reason=commit_reason,
                    speaker_prefix=speaker_prefix,
                )
                self._log_decision(result, emitted_to_ui=True)
                return result

        cleaned, punct_changed = cleanup_midline_punctuation(text)
        if punct_changed:
            self._punctuation_cleanup_count += 1
            _jp_log("MIDLINE_PUNCTUATION_ARTIFACT_DETECTED", text_preview=text[:80])
        text = cleaned

        # fixes TASK_2C_REPORT.md: duplicate-continuation suppression and
        # merge-with-previous both compare against self._previous_line: gate
        # both on confirmed same speaker BEFORE the text-similarity checks
        # run, so a different (or unknown) speaker's text is never silently
        # suppressed or merged as if it were the previous speaker's line.
        previous_speaker_confirmed = speakers_confirmed_same(self._previous_speaker, speaker)

        if (
            JAPANESE_DUPLICATE_CONTINUATION_GUARD_ENABLED
            and self._previous_line
            and previous_speaker_confirmed
        ):
            ratio = duplicate_continuation_ratio(self._previous_line, text)
            if ratio >= 0.95:
                self._duplicate_suppressed_count += 1
                _jp_log("DUPLICATE_CONTINUATION_SUPPRESSED", ratio=ratio)
                result = self._build_result(
                    emit_now=False,
                    output_text="",
                    pending_text=self._pending,
                    action="suppress_duplicate_continuation",
                    reason="mostly_contained_in_previous",
                    before_text=text,
                    commit_reason=commit_reason,
                    speaker_prefix=speaker_prefix,
                )
                self._log_decision(result, emitted_to_ui=False)
                return result
            if ratio >= 0.7 and len(text) < len(self._previous_line):
                self._duplicate_suppressed_count += 1
                _jp_log("DUPLICATE_CONTINUATION_LONGER_VERSION_KEPT")
                result = self._build_result(
                    emit_now=False,
                    output_text="",
                    pending_text=self._pending,
                    action="suppress_duplicate_continuation",
                    reason="shorter_duplicate",
                    before_text=text,
                    commit_reason=commit_reason,
                    speaker_prefix=speaker_prefix,
                )
                self._log_decision(result, emitted_to_ui=False)
                return result

        is_leading, particle = is_leading_fragment_line(text)
        prev_incomplete = has_incomplete_ending(self._previous_line)[0] if self._previous_line else False

        if (
            JAPANESE_SAFE_MERGE_ENABLED
            and previous_speaker_confirmed
            and self._previous_line
            and (is_leading or prev_incomplete or not has_strong_terminal_boundary(self._previous_line))
        ):
            merged, merge_reason, ok = safe_merge_text(self._previous_line, text)
            if ok:
                if merge_reason == "uncertain":
                    _jp_log("SAFE_MERGE_REJECTED_UNCERTAIN")
                else:
                    if count_japanese_chars(merged) > BOUNDARY_STABILIZER_SAFE_MERGE_MAX_CHARS:
                        _jp_log("SAFE_MERGE_REJECTED_TOO_LONG")
                    else:
                        self._merge_previous_count += 1
                        if particle:
                            _jp_log("LEADING_FRAGMENT_MERGED_PREVIOUS", particle=particle)
                        else:
                            _jp_log("SAFE_MERGE_PREVIOUS_CURRENT", reason=merge_reason)
                        if "inserted_punctuation" in merge_reason:
                            _jp_log("SAFE_MERGE_INSERTED_PUNCTUATION")
                        merged, _ = cleanup_midline_punctuation(merged)
                        self._output_count += 1
                        self._record_output_metrics(merged)
                        result = self._build_result(
                            emit_now=True,
                            output_text=merged,
                            pending_text="",
                            action="merge_with_previous",
                            reason=merge_reason,
                            before_text=text,
                            update_previous=True,
                            commit_reason=commit_reason,
                            speaker_prefix=speaker_prefix,
                        )
                        self._log_decision(result, emitted_to_ui=True)
                        return result

        if (
            JAPANESE_LEADING_FRAGMENT_HOLD_ENABLED
            and is_leading
            and count_japanese_chars(text) < BOUNDARY_STABILIZER_LEADING_FRAGMENT_MAX_CHARS
            and not stop_flush
        ):
            for prefix in _VALID_TRANSITION_PREFIXES:
                if text.startswith(prefix) and count_japanese_chars(text) >= 12:
                    _jp_log("LEADING_FRAGMENT_ALLOWED_VALID_TRANSITION", prefix=prefix)
                    break
            else:
                self._pending = text
                self._pending_speaker = speaker
                self._pending_since = now
                self._held_count += 1
                _jp_log("LEADING_FRAGMENT_DETECTED", particle=particle)
                _jp_log("LEADING_FRAGMENT_HELD", text_preview=text[:80])
                _jp_log("BOUNDARY_STABILIZER_HOLD", reason="leading_fragment")
                result = self._build_result(
                    emit_now=False,
                    output_text="",
                    pending_text=text,
                    action="hold_leading_fragment",
                    reason=f"leading_particle_{particle}",
                    before_text=text,
                    confidence="low",
                    commit_reason=commit_reason,
                    speaker_prefix=speaker_prefix,
                )
                self._log_decision(result, emitted_to_ui=False)
                return result

        incomplete, inc_suffix = has_incomplete_ending(text)
        if incomplete and not stop_flush and not is_clear_sentence(text):
            self._pending = text
            self._pending_speaker = speaker
            self._pending_since = now
            self._held_count += 1
            _jp_log("INCOMPLETE_ENDING_DETECTED", suffix=inc_suffix)
            _jp_log("INCOMPLETE_ENDING_HELD", text_preview=text[:80])
            result = self._build_result(
                emit_now=False,
                output_text="",
                pending_text=text,
                action="hold_leading_fragment",
                reason=f"incomplete_ending_{inc_suffix}",
                before_text=text,
                confidence="low",
                commit_reason=commit_reason,
                speaker_prefix=speaker_prefix,
            )
            self._log_decision(result, emitted_to_ui=False)
            return result

        self._output_count += 1
        self._record_output_metrics(text)
        _jp_log("BOUNDARY_STABILIZER_EMIT", text_preview=text[:80])
        result = self._build_result(
            emit_now=True,
            output_text=text,
            pending_text="",
            action="emit_unchanged" if text == input_text.strip() else "cleanup_punctuation_only",
            reason="ready_to_emit",
            before_text=input_text,
            commit_reason=commit_reason,
            speaker_prefix=speaker_prefix,
        )
        self._log_decision(result, emitted_to_ui=True)
        return result

    def flush_pending(self, *, stop_flush: bool = False) -> Optional[dict[str, Any]]:
        if not JAPANESE_STOP_FLUSH_BOUNDARY_SAFE:
            self._pending = ""
            return None
        _jp_log("BOUNDARY_STABILIZER_STOP_FLUSH_STARTED")
        pending = (self._pending or "").strip()
        pending_speaker = self._pending_speaker
        self._pending = ""
        self._pending_speaker = None
        self._pending_since = 0.0
        if not pending:
            _jp_log("BOUNDARY_STABILIZER_PENDING_CLEARED_ON_STOP")
            return None
        if count_japanese_chars(pending) < 8:
            self._stop_flush_drop_count += 1
            _jp_log("BOUNDARY_STABILIZER_STOP_FLUSH_DROPPED", text_preview=pending[:40])
            return None
        cleaned, _ = cleanup_midline_punctuation(pending)
        self._stop_flush_emit_count += 1
        self._output_count += 1
        _jp_log("BOUNDARY_STABILIZER_STOP_FLUSH_EMITTED", text_preview=cleaned[:80])
        _jp_log("INCOMPLETE_ENDING_STOP_FLUSHED")
        result = self._build_result(
            emit_now=True,
            output_text=cleaned,
            pending_text="",
            action="stop_flush_emit",
            reason="stop_flush_meaningful_pending",
            before_text=pending,
            stop_flush=True,
            confidence="low",
            commit_reason="stop_listening",
        )
        # fixes TASK_2C_REPORT.md: expose the pending fragment's own speaker
        # so a stop-flush caller can attribute it correctly instead of
        # defaulting to whatever speaker last committed elsewhere.
        result["pending_speaker"] = pending_speaker
        self._log_decision(result, emitted_to_ui=True)
        return result

    def note_emitted(self, text: str, speaker: Any = None) -> None:
        body = re.sub(r"^\[Speaker\s+\d+\]\s*", "", (text or "").strip())
        self._previous_line = body
        self._previous_speaker = speaker

    def _log_decision(self, result: dict[str, Any], *, emitted_to_ui: bool) -> None:
        if not JAPANESE_BOUNDARY_DECISION_LOG_ENABLED:
            return
        record = {
            "timestamp": time.time(),
            "run_id": _run_id(),
            "app_version": APP_VERSION,
            "input_text": result.get("before_text", ""),
            "output_text": result.get("after_text", ""),
            "action": result.get("action", ""),
            "reason": result.get("reason", ""),
            "confidence": result.get("confidence", ""),
            "risk_level": result.get("risk_level", ""),
            "previous_line_excerpt": result.get("previous_line_excerpt", ""),
            "pending_before": result.get("pending_before", ""),
            "pending_after": result.get("pending_text", ""),
            "raw_mutation": False,
            "commit_reason": result.get("commit_reason", ""),
            "stop_flush": bool(result.get("stop_flush")),
            "emitted_to_ui": emitted_to_ui,
        }
        path = _decision_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def get_metrics(self) -> dict[str, Any]:
        inp = max(self._input_count, 1)
        return {
            "boundary_stabilizer_enabled": JAPANESE_BOUNDARY_STABILIZER_ENABLED,
            "boundary_stabilizer_mode": JAPANESE_BOUNDARY_STABILIZER_MODE,
            "input_stable_candidate_count": self._input_count,
            "output_stable_commit_count": self._output_count,
            "held_fragment_count": self._held_count,
            "merge_previous_count": self._merge_previous_count,
            "merge_pending_count": self._merge_pending_count,
            "duplicate_suppressed_count": self._duplicate_suppressed_count,
            "punctuation_cleanup_count": self._punctuation_cleanup_count,
            "timeout_emit_count": self._timeout_emit_count,
            "stop_flush_emit_count": self._stop_flush_emit_count,
            "stop_flush_drop_count": self._stop_flush_drop_count,
            "raw_mutation_count": 0,
            "dangerous_correction_count": 0,
            "leading_fragment_before_count": self._leading_before,
            "leading_fragment_after_count": self._leading_after,
            "punctuation_artifact_before_count": self._punct_before,
            "punctuation_artifact_after_count": self._punct_after,
            "duplicate_continuation_before_count": self._dup_before,
            "duplicate_continuation_after_count": self._dup_after,
            "translation_ready_before_ratio": round(self._translation_ready_before / inp, 4),
            "translation_ready_after_ratio": round(self._translation_ready_after / max(self._output_count, 1), 4),
            "boundary_risk_estimate_before": self._leading_before + self._dup_before,
            "boundary_risk_estimate_after": self._leading_after + self._dup_after,
        }

    def write_summary(self, extra_fields: dict[str, Any] | None = None) -> Path:
        rev_metrics: dict[str, Any] = {}
        try:
            from alpha.transcription.stable_line_revision import get_stable_line_revision_manager

            rev_metrics = get_stable_line_revision_manager().get_metrics()
        except Exception:
            pass
        summary = {
            "app_version": APP_VERSION,
            "run_id": _run_id(),
            **self.get_metrics(),
            **rev_metrics,
            "input_stable_candidate_count": self._input_count,
            "written_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if extra_fields:
            summary.update(extra_fields)
        path = _summary_path()
        if BOUNDARY_SUMMARY_PATH_FIX_ENABLED:
            try:
                from alpha.utils.troubleshooting_paths import get_run_folder

                run = get_run_folder()
                if run:
                    path = Path(run) / "accuracy" / "boundary_stabilizer_summary.json"
            except Exception:
                pass
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        _jp_log("BOUNDARY_STABILIZER_SUMMARY_WRITTEN", path=str(path))
        _jp_log("BOUNDARY_SUMMARY_FINAL_PATH_WRITTEN", path=str(path))
        latest_copy = Path("troubleshooting/latest/boundary_stabilizer_summary.json")
        latest_copy.parent.mkdir(parents=True, exist_ok=True)
        latest_copy.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        _jp_log("BOUNDARY_SUMMARY_LATEST_COPY_WRITTEN", path=str(latest_copy))
        _jp_log("BOUNDARY_SUMMARY_85242_FIELDS_UPDATED")
        if summary.get("residual_duplicate_after_count") is not None:
            _jp_log("BOUNDARY_SUMMARY_RESIDUAL_DUPLICATE_METRICS_WRITTEN")
        if summary.get("punctuation_artifact_after_count") is not None:
            _jp_log("BOUNDARY_SUMMARY_PUNCTUATION_METRICS_WRITTEN")
        if str(path).replace("\\", "/").find("_pending") >= 0:
            _jp_log("BOUNDARY_SUMMARY_PENDING_PATH_REPLACED", final_path=str(path))
        self._update_evidence_index(summary, path)
        return path

    def _update_evidence_index(self, summary: dict[str, Any], path: Path) -> None:
        updates = {
            "boundary_stabilizer_enabled": True,
            "boundary_stabilizer_summary_path": str(path).replace("\\", "/"),
            "latest_boundary_stabilizer_summary_path": "troubleshooting/latest/boundary_stabilizer_summary.json",
            "boundary_stabilizer_decisions_path": str(_decision_log_path()).replace("\\", "/"),
            "latest_boundary_stabilizer_decisions_path": str(_decision_log_path()).replace("\\", "/"),
            "held_fragment_count": summary.get("held_fragment_count", 0),
            "merge_previous_count": summary.get("merge_previous_count", 0),
            "merge_pending_count": summary.get("merge_pending_count", 0),
            "duplicate_suppressed_count": summary.get("duplicate_suppressed_count", 0),
            "punctuation_cleanup_count": summary.get("punctuation_cleanup_count", 0),
            "leading_fragment_after_count": summary.get("leading_fragment_after_count", 0),
            "translation_ready_after_ratio": summary.get("translation_ready_after_ratio", 0),
            "output_clean_line_count": summary.get("output_clean_line_count", 0),
            "revise_previous_line_count": summary.get("revise_previous_line_count", 0),
            "cumulative_duplicate_detected_count": summary.get("cumulative_duplicate_detected_count", 0),
        }
        for rel in (
            "troubleshooting/latest/latest_accuracy_evidence_index.json",
            "troubleshooting/latest_accuracy_evidence_index.json",
        ):
            p = Path(rel)
            if not p.exists():
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                data.update(updates)
                p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                # fixes BUG_FIX_ROADMAP.md Batch 2 item 7: logging only.
                # This updates a secondary evidence index only (not the
                # transcript itself), so behavior stays swallow-and-continue.
                _jp_log(
                    "ACCURACY_EVIDENCE_INDEX_UPDATE_FAILED",
                    reason=f"{type(exc).__name__}:{exc}",
                    path=str(p),
                )
        _jp_log("LATEST_ACCURACY_INDEX_BOUNDARY_STABILIZER_FIELDS_UPDATED")

    def simulate_lines(self, lines: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
        self.reset()
        outputs: list[str] = []
        decisions: list[dict[str, Any]] = []
        for line in lines:
            line = (line or "").strip()
            if not line:
                continue
            result = self.process(line, commit_reason="simulation")
            decisions.append(result)
            if result.get("emit_now") and result.get("output_text"):
                if result.get("should_revise") or result.get("update_previous"):
                    if outputs:
                        outputs[-1] = result["output_text"]
                    else:
                        outputs.append(result["output_text"])
                else:
                    outputs.append(result["output_text"])
                self.note_emitted(result["output_text"])
        flush = self.flush_pending(stop_flush=True)
        if flush and flush.get("emit_now") and flush.get("output_text"):
            outputs.append(flush["output_text"])
            decisions.append(flush)
        return outputs, decisions


def get_boundary_stabilizer() -> JapaneseBoundaryStabilizer:
    global _stabilizer
    if _stabilizer is None:
        _stabilizer = JapaneseBoundaryStabilizer()
        _jp_log("BOUNDARY_STABILIZER_INTEGRATED_AFTER_ASSEMBLER")
    return _stabilizer


def reset_boundary_stabilizer() -> None:
    global _stabilizer
    if _stabilizer is not None:
        _stabilizer.reset()
    _stabilizer = None


def flush_boundary_stabilizer_on_stop() -> Optional[dict[str, Any]]:
    stabilizer = get_boundary_stabilizer()
    result = stabilizer.flush_pending(stop_flush=True)
    stabilizer.write_summary()
    return result

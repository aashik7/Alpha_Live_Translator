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

        previous_text = None
        if hasattr(self, "transcript_store") and self.transcript_store is not None:
            segment = self.transcript_store.get_last_segment(speaker_num)
            if segment is not None:
                previous_text = segment.text

        action, result_text = decide_transcript_action(previous_text, text)
        if action == "skip" or not result_text:
            self._transcript_stability_counters.skipped += 1
            return

        timestamp = item.get("timestamp")
        self._apply_transcript_to_store(
            speaker_num,
            result_text,
            timestamp=timestamp,
            action=action,
        )
        if action == "update" and hasattr(self, "_on_store_segment_updated"):
            self._on_store_segment_updated(speaker_num, result_text)
        elif hasattr(self, "_on_store_segment_added"):
            self._on_store_segment_added(speaker_num, result_text)
        else:
            self._render_transcript_from_store()

        if hasattr(self, "submit_text_for_translation"):
            try:
                self.submit_text_for_translation(result_text, speaker=speaker_num)
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

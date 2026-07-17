"""Transcript stabilization and duplicate protection for the UI queue."""

import re
import traceback

import tkinter as tk

MIN_CONTAINED_LENGTH = 12

class TranscriptStabilityCounters:
    """Lightweight counters for transcript stabilization decisions."""

    def __init__(self):
        self.duplicate_exact_skipped = 0
        self.duplicate_contained_skipped = 0
        self.progressive_extension_replaced = 0
        self.fragment_safe_merged = 0
        self.store_segment_added = 0
        self.store_segment_updated = 0
        self.copy_export_word_count = 0

    def reset(self):
        self.duplicate_exact_skipped = 0
        self.duplicate_contained_skipped = 0
        self.progressive_extension_replaced = 0
        self.fragment_safe_merged = 0
        self.store_segment_added = 0
        self.store_segment_updated = 0
        self.copy_export_word_count = 0

    def as_dict(self):
        return {
            "duplicate_exact_skipped": self.duplicate_exact_skipped,
            "duplicate_contained_skipped": self.duplicate_contained_skipped,
            "progressive_extension_replaced": self.progressive_extension_replaced,
            "fragment_safe_merged": self.fragment_safe_merged,
            "store_segment_added": self.store_segment_added,
            "store_segment_updated": self.store_segment_updated,
            "copy_export_word_count": self.copy_export_word_count,
        }


def normalize_for_compare(text: str) -> str:
    """Lowercase, strip, collapse whitespace, remove punctuation for comparison."""
    cleaned = (text or "").lower().strip()
    cleaned = re.sub(r"[^\w\s]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def compact_for_compare(text: str) -> str:
    """Normalized text with all spaces removed."""
    return normalize_for_compare(text).replace(" ", "")


def is_exact_duplicate(previous: str, current: str) -> bool:
    """True when normalized previous equals normalized current."""
    return normalize_for_compare(previous) == normalize_for_compare(current)


def is_contained_duplicate(previous: str, current: str) -> bool:
    """True when either normalized string contains the other (min length 12)."""
    prev_n = normalize_for_compare(previous)
    curr_n = normalize_for_compare(current)
    if len(prev_n) < MIN_CONTAINED_LENGTH and len(curr_n) < MIN_CONTAINED_LENGTH:
        return False
    if len(prev_n) >= MIN_CONTAINED_LENGTH and prev_n in curr_n:
        return True
    if len(curr_n) >= MIN_CONTAINED_LENGTH and curr_n in prev_n:
        return True
    return False


def previous_contains_current(previous: str, current: str) -> bool:
    """True when normalized previous contains normalized current."""
    prev_n = normalize_for_compare(previous)
    curr_n = normalize_for_compare(current)
    if len(curr_n) < MIN_CONTAINED_LENGTH:
        return False
    return curr_n in prev_n


def current_contains_previous(previous: str, current: str) -> bool:
    """True when normalized current contains normalized previous."""
    prev_n = normalize_for_compare(previous)
    curr_n = normalize_for_compare(current)
    if len(prev_n) < MIN_CONTAINED_LENGTH:
        return False
    return prev_n in curr_n


def is_progressive_extension(previous: str, current: str) -> bool:
    """True when current is a longer version starting with previous."""
    prev_n = normalize_for_compare(previous)
    curr_n = normalize_for_compare(current)
    if not prev_n or not curr_n or len(curr_n) <= len(prev_n):
        return False
    if curr_n.startswith(prev_n):
        return True
    prev_c = compact_for_compare(previous)
    curr_c = compact_for_compare(current)
    return bool(prev_c) and curr_c.startswith(prev_c) and len(curr_c) > len(prev_c)


def merge_with_safe_space(previous: str, current: str) -> str:
    """Join two fragments with exactly one space; never glue word boundaries."""
    prev = (previous or "").strip()
    curr = (current or "").strip()
    if not prev:
        return curr
    if not curr:
        return prev
    return f"{prev} {curr}"


def _find_char_overlap_length(previous: str, current: str) -> int:
    """Largest suffix of previous that matches prefix of current (case-insensitive)."""
    prev_lower = (previous or "").rstrip().lower()
    curr_lower = (current or "").lstrip().lower()
    max_overlap = min(len(prev_lower), len(curr_lower))
    for size in range(max_overlap, 0, -1):
        if prev_lower.endswith(curr_lower[:size]):
            return size
    return 0


def remove_overlap_and_merge(previous: str, current: str) -> str:
    """Merge fragments, deduplicating suffix/prefix overlap when present."""
    prev = (previous or "").rstrip()
    curr = (current or "").lstrip()
    if not prev:
        return curr.strip()
    if not curr:
        return prev.strip()

    overlap = _find_char_overlap_length(prev, curr)
    if overlap >= 3:
        merged = (prev + curr[overlap:]).strip()
        return re.sub(r"\s+", " ", merged)
    return merge_with_safe_space(prev, curr)


def has_suffix_prefix_overlap(previous: str, current: str) -> bool:
    """True when fragments share overlap or current continues a non-terminal previous."""
    if _find_char_overlap_length(previous, current) >= 3:
        return True
    prev = (previous or "").rstrip()
    curr = (current or "").lstrip()
    if not prev or not curr:
        return False
    if prev[-1] in ".?!":
        return False
    return curr[0].islower()


def decide_transcript_action(previous_text: str | None, current_text: str) -> tuple[str, str | None]:
    """
    Return (action, text) using the required decision order.

    Actions: skip, append_new, replace_last, merge_last
    """
    current = (current_text or "").strip()
    if not current:
        return ("skip", None)

    previous = (previous_text or "").strip() if previous_text else ""
    if not previous:
        return ("append_new", current)

    if is_exact_duplicate(previous, current):
        return ("skip", None)

    if previous_contains_current(previous, current):
        return ("skip", None)

    if current_contains_previous(previous, current) or is_progressive_extension(
        previous, current
    ):
        return ("replace_last", current)

    if has_suffix_prefix_overlap(previous, current):
        return ("merge_last", remove_overlap_and_merge(previous, current))

    return ("append_new", current)


def apply_transcript_sequence(texts: list[str], speaker: int = 1) -> list[str]:
    """Apply stabilization to a sequence of finals (test helper, no GUI)."""
    lines: list[str] = []
    last_by_speaker: dict[int, str] = {}

    for text in texts:
        previous = last_by_speaker.get(speaker)
        action, result = decide_transcript_action(previous, text)
        if action == "skip" or not result:
            continue
        if action in ("replace_last", "merge_last"):
            if lines:
                lines[-1] = result
            else:
                lines.append(result)
            last_by_speaker[speaker] = result
        else:
            lines.append(result)
            last_by_speaker[speaker] = result

    return lines


class DuplicateProtectionMixin:
    """Mixin providing stabilized transcript display for the live transcript panel."""

    _SPEAKER_COLORS = {
        1: "#4a9eff",
        2: "#50c878",
        3: "#ffa500",
        4: "#ff6b6b",
        5: "#9b59b6",
        6: "#1abc9c",
        7: "#e74c3c",
        8: "#3498db",
        9: "#f39c12",
        10: "#2ecc71",
        11: "#95a5a6",
        12: "#e91e63",
    }

    def _ensure_stability_state(self):
        if not hasattr(self, "_transcript_stability_counters"):
            self._transcript_stability_counters = TranscriptStabilityCounters()
        if not hasattr(self, "_ui_line_meta"):
            self._ui_line_meta = {}

    def _get_previous_segment_text(self, speaker_num: int) -> str | None:
        if hasattr(self, "transcript_store") and self.transcript_store is not None:
            segment = self.transcript_store.get_last_segment_for_speaker(speaker_num)
            if segment is not None:
                return segment.text
        meta = getattr(self, "_ui_line_meta", {}).get(speaker_num)
        if meta:
            return meta.get("text")
        return None

    def _configure_speaker_tag(self, speaker_num: int):
        tag_name = f"speaker_{speaker_num}"
        if tag_name not in self.initial_verse_box.tag_names():
            color = self._SPEAKER_COLORS.get(speaker_num, "#ffffff")
            self.initial_verse_box.tag_configure(
                tag_name,
                foreground=color,
                font=("Segoe UI", 12, "bold"),
            )

    def _append_new_transcript_line(self, speaker_num: int, text: str):
        self.initial_verse_box.configure(state="normal")

        if self.last_displayed_speaker is not None and speaker_num != self.last_displayed_speaker:
            self.initial_verse_box.insert(tk.END, "\n\n")

        label = f"[Speaker {speaker_num}] "
        label_start = self.initial_verse_box.index(tk.END)
        self.initial_verse_box.insert(tk.END, label)
        self._configure_speaker_tag(speaker_num)
        label_end = self.initial_verse_box.index(tk.END)
        self.initial_verse_box.tag_add(f"speaker_{speaker_num}", label_start, label_end)

        text_start = self.initial_verse_box.index(tk.END)
        self.initial_verse_box.insert(tk.END, text + "\n")
        self.initial_verse_box.configure(state="disabled")

        self._ui_line_meta[speaker_num] = {
            "text": text,
            "text_start": text_start,
            "label_start": label_start,
        }
        self.last_displayed_speaker = speaker_num
        self.last_speaker_id = speaker_num
        self.last_speaker = speaker_num

    def _update_existing_transcript_line(self, speaker_num: int, text: str):
        meta = self._ui_line_meta.get(speaker_num)
        if not meta:
            self._append_new_transcript_line(speaker_num, text)
            return

        self.initial_verse_box.configure(state="normal")
        self.initial_verse_box.delete(meta["text_start"], tk.END)
        self.initial_verse_box.insert(meta["text_start"], text + "\n")
        self.initial_verse_box.configure(state="disabled")

        meta["text"] = text
        self._ui_line_meta[speaker_num] = meta
        self.last_displayed_speaker = speaker_num

    def _sync_transcript_store(self, speaker_num: int, text: str, action: str):
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

        if action == "append_new":
            self.transcript_store.add_segment(
                speaker=speaker_num,
                text=text,
                source_language=source_language,
                target_language=target_language,
            )
            self._transcript_stability_counters.store_segment_added += 1
        elif action in ("replace_last", "merge_last"):
            updated = self.transcript_store.update_last_segment(
                speaker=speaker_num,
                text=text,
            )
            if updated:
                self._transcript_stability_counters.store_segment_updated += 1
            else:
                self.transcript_store.add_segment(
                    speaker=speaker_num,
                    text=text,
                    source_language=source_language,
                    target_language=target_language,
                )
                self._transcript_stability_counters.store_segment_added += 1

    def _display_transcript_item(self, item):
        """Render one stabilized transcript dict into the live transcript text box."""
        self._ensure_stability_state()

        speaker_num = item.get("speaker", 1)
        text = (item.get("text") or "").strip()
        if not text:
            return

        previous_text = self._get_previous_segment_text(speaker_num)
        action, result_text = decide_transcript_action(previous_text, text)

        if action == "skip":
            if is_exact_duplicate(previous_text or "", text):
                self._transcript_stability_counters.duplicate_exact_skipped += 1
            else:
                self._transcript_stability_counters.duplicate_contained_skipped += 1
            return

        if action == "replace_last":
            self._transcript_stability_counters.progressive_extension_replaced += 1
            self._update_existing_transcript_line(speaker_num, result_text)
            self._sync_transcript_store(speaker_num, result_text, action)
            print(f"[UI-REPLACE] Speaker {speaker_num}: {result_text[:50]}...")
        elif action == "merge_last":
            self._transcript_stability_counters.fragment_safe_merged += 1
            self._update_existing_transcript_line(speaker_num, result_text)
            self._sync_transcript_store(speaker_num, result_text, action)
            print(f"[UI-MERGE] Speaker {speaker_num}: {result_text[:50]}...")
        else:
            self._append_new_transcript_line(speaker_num, result_text)
            self._sync_transcript_store(speaker_num, result_text, action)
            print(f"[UI] Speaker {speaker_num}: {result_text[:50]}...")

        self.initial_verse_box.see(tk.END)
        self.check_scrollbar_visibility(
            self.initial_verse_box, self.initial_verse_box._scrollbar
        )

        if hasattr(self, "submit_text_for_translation"):
            try:
                self.submit_text_for_translation(result_text, speaker=speaker_num)
            except Exception as exc:
                print(f"[Translation] submit failed: {exc}")

    def process_ui_queue(self):
        """Process transcript queue with stabilization (UI thread via after)."""
        try:
            self._ensure_stability_state()
            if not hasattr(self, "last_displayed_speaker"):
                self.last_displayed_speaker = None

            while not self.transcript_queue.empty():
                item = self.transcript_queue.get()
                if isinstance(item, list):
                    items_to_process = item
                else:
                    items_to_process = [item]

                for sub_item in items_to_process:
                    self._display_transcript_item(sub_item)

        except Exception as e:
            print(f"[ERROR] Processing UI queue: {e}")
            traceback.print_exc()

        self.after(100, self.process_ui_queue)

    def reset_transcript_stability_state(self):
        """Reset UI stabilization metadata and counters."""
        self._ensure_stability_state()
        self._transcript_stability_counters.reset()
        self._ui_line_meta = {}
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

"""Transcript duplicate detection for the UI queue."""

import re
import time
import traceback

import tkinter as tk

from alpha.config import (
    FUZZY_DEDUP_JACCARD_THRESHOLD,
    FUZZY_DEDUP_WINDOW_S,
    MAX_TRANSCRIPT_HASH_HISTORY,
    TRANSCRIPT_MERGE_WINDOW_S,
)


class DuplicateProtectionMixin:
    """Mixin providing hash + fuzzy duplicate protection for transcript display."""

    def _normalize_display_text(self, text):
        """Normalize transcript text for display-boundary duplicate checks."""
        cleaned = (text or "").lower().strip()
        cleaned = re.sub(r"[^\w\s]", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _should_display_text_once(self, text, window_seconds=8.0):
        """Return False when the same normalized text was shown recently."""
        if not hasattr(self, "_recent_displayed_texts"):
            self._recent_displayed_texts = []

        normalized = self._normalize_display_text(text)
        if not normalized:
            return True

        now = time.time()
        self._recent_displayed_texts = [
            (entry_text, entry_time)
            for entry_text, entry_time in self._recent_displayed_texts
            if now - entry_time <= window_seconds
        ]

        for entry_text, entry_time in self._recent_displayed_texts:
            if entry_text == normalized and now - entry_time <= window_seconds:
                return False

        self._recent_displayed_texts.append((normalized, now))
        return True

    def _normalize_transcript_tokens(self, text):
        """Lowercase token set with punctuation stripped for fuzzy dedup."""
        cleaned = re.sub(r"[^\w\s]", "", text.lower())  # CHANGED: normalize for fuzzy match (fix 8)
        return set(cleaned.split())  # CHANGED: (fix 8)

    def _jaccard_similarity(self, tokens_a, tokens_b):
        """Jaccard index between two token sets."""
        if not tokens_a and not tokens_b:  # CHANGED: (fix 8)
            return 1.0  # CHANGED: (fix 8)
        union = tokens_a | tokens_b  # CHANGED: (fix 8)
        if not union:  # CHANGED: (fix 8)
            return 0.0  # CHANGED: (fix 8)
        return len(tokens_a & tokens_b) / len(union)  # CHANGED: (fix 8)

    def _prune_transcript_hashes(self, transcript_hash):
        """Keep only the most recent MAX_TRANSCRIPT_HASH_HISTORY hashes."""
        self.last_transcript_hash.add(transcript_hash)  # CHANGED: track hash (fix 8)
        self._transcript_hash_order.append(transcript_hash)  # CHANGED: ordered prune list (fix 8)
        while len(self._transcript_hash_order) > MAX_TRANSCRIPT_HASH_HISTORY:  # CHANGED: cap at 200 (fix 8)
            old_hash = self._transcript_hash_order.pop(0)  # CHANGED: drop oldest (fix 8)
            self.last_transcript_hash.discard(old_hash)  # CHANGED: (fix 8)

    def _is_fuzzy_duplicate(self, speaker_num, text):
        """Skip near-duplicate utterances from same speaker within 3 seconds."""
        now = time.time()  # CHANGED: fuzzy dedup timestamp (fix 8)
        tokens = self._normalize_transcript_tokens(text)  # CHANGED: (fix 8)
        prev = self._last_speaker_utterance.get(speaker_num)  # CHANGED: (fix 8)
        if prev:  # CHANGED: (fix 8)
            prev_tokens, prev_time = prev  # CHANGED: (fix 8)
            if (  # CHANGED: (fix 8)
                now - prev_time <= FUZZY_DEDUP_WINDOW_S  # CHANGED: within 3s window (fix 8)
                and self._jaccard_similarity(tokens, prev_tokens)  # CHANGED: (fix 8)
                > FUZZY_DEDUP_JACCARD_THRESHOLD  # CHANGED: >0.85 similarity (fix 8)
            ):  # CHANGED: (fix 8)
                # Allow longer refinements through — only skip redundant repeats
                if len(tokens) > len(prev_tokens) and tokens - prev_tokens:
                    self._last_speaker_utterance[speaker_num] = (tokens, now)
                    return False
                return True  # CHANGED: (fix 8)
        self._last_speaker_utterance[speaker_num] = (tokens, now)  # CHANGED: update last utterance (fix 8)
        return False  # CHANGED: (fix 8)

    def _sentence_ends(self, text):
        """Return True when text ends with terminal sentence punctuation."""
        return bool(text) and text.rstrip().endswith((".", "?", "!"))

    def _try_merge_transcript_fragment(self, speaker_num, text):
        """Append to the previous same-speaker line when Deepgram splits mid-phrase."""
        meta = getattr(self, "_fragment_merge_meta", None)
        if not meta:
            return False

        now = time.time()
        if meta.get("speaker") != speaker_num:
            return False
        if now - meta.get("time", 0) > TRANSCRIPT_MERGE_WINDOW_S:
            return False
        if self._sentence_ends(meta.get("text", "")):
            return False

        merged = f"{meta['text']} {text}".strip()
        self.initial_verse_box.configure(state="normal")
        self.initial_verse_box.delete(meta["text_start"], tk.END)
        self.initial_verse_box.insert(meta["text_start"], merged + "\n")
        self.initial_verse_box.configure(state="disabled")
        self.initial_verse_box.see(tk.END)
        self.check_scrollbar_visibility(
            self.initial_verse_box, self.initial_verse_box._scrollbar
        )

        meta["text"] = merged
        meta["time"] = now
        self._fragment_merge_meta = meta
        self.last_displayed_speaker = speaker_num
        print(f"[UI-MERGE] Speaker {speaker_num}: {merged[:60]}...")
        return True

    def _display_transcript_item(self, item):
        """Render one transcript dict into the Initial verse text box."""
        # TODO V3: Move this direct UI update to EventBus after regression testing.
        speaker_num = item.get("speaker", 1)
        text = item.get("text", "").strip()
        if not text:
            return

        transcript_key = f"spk{speaker_num}:{text}"
        transcript_hash = hash(transcript_key)

        if transcript_hash in self.last_transcript_hash:  # CHANGED: exact hash dedup (fix 8)
            print(f"[SKIP] Duplicate: Speaker {speaker_num}: {text[:30]}...")
            return

        if self._is_fuzzy_duplicate(speaker_num, text):  # CHANGED: fuzzy Jaccard dedup (fix 8)
            print(f"[SKIP] Fuzzy duplicate: Speaker {speaker_num}: {text[:30]}...")  # CHANGED: (fix 8)
            return

        if not self._should_display_text_once(text):
            short_text = text[:60] + ("..." if len(text) > 60 else "")
            print(f"[DuplicateGuard] Skipped repeated transcript: {short_text}")
            return

        if self._try_merge_transcript_fragment(speaker_num, text):
            if hasattr(self, "submit_text_for_translation"):
                try:
                    merged_text = self._fragment_merge_meta.get("text", text)
                    self.submit_text_for_translation(merged_text, speaker=speaker_num)
                except Exception as exc:
                    print(f"[Translation] submit failed: {exc}")
            if hasattr(self, "record_transcript_segment"):
                try:
                    self.record_transcript_segment(
                        speaker_num, self._fragment_merge_meta.get("text", text)
                    )
                except Exception as exc:
                    print(f"[Summary] record failed: {exc}")
            return

        self.initial_verse_box.configure(state="normal")

        if self.last_displayed_speaker is not None:
            if speaker_num != self.last_displayed_speaker:
                self.initial_verse_box.insert(tk.END, "\n\n")

        label = f"[Speaker {speaker_num}] "
        start_idx = self.initial_verse_box.index(tk.END)
        self.initial_verse_box.insert(tk.END, label)

        tag_name = f"speaker_{speaker_num}"
        if tag_name not in self.initial_verse_box.tag_names():
            colors = {
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
            color = colors.get(speaker_num, "#ffffff")
            self.initial_verse_box.tag_configure(
                tag_name,
                foreground=color,
                font=("Segoe UI", 12, "bold"),
            )

        end_idx = self.initial_verse_box.index(tk.END)
        self.initial_verse_box.tag_add(tag_name, start_idx, end_idx)
        text_start = self.initial_verse_box.index(tk.END)
        self.initial_verse_box.insert(tk.END, text + "\n")
        self.initial_verse_box.configure(state="disabled")

        self._fragment_merge_meta = {
            "speaker": speaker_num,
            "text": text,
            "time": time.time(),
            "text_start": text_start,
        }

        self._prune_transcript_hashes(transcript_hash)  # CHANGED: prune hash set to 200 (fix 8)
        self.last_displayed_speaker = speaker_num
        self.last_speaker_id = speaker_num
        self.last_speaker = speaker_num
        self.initial_verse_box.see(tk.END)
        self.check_scrollbar_visibility(
            self.initial_verse_box, self.initial_verse_box._scrollbar
        )
        print(f"[UI] Speaker {speaker_num}: {text[:50]}...")

        if hasattr(self, "submit_text_for_translation"):
            try:
                self.submit_text_for_translation(text, speaker=speaker_num)
            except Exception as exc:
                print(f"[Translation] submit failed: {exc}")

        if hasattr(self, "record_transcript_segment"):
            try:
                self.record_transcript_segment(speaker_num, text)
            except Exception as exc:
                print(f"[Summary] record failed: {exc}")

    def process_ui_queue(self):
        """Process transcript queue with duplicate protection (UI thread via after)."""
        try:
            if not hasattr(self, "last_transcript_hash"):
                self.last_transcript_hash = set()
            if not hasattr(self, "last_displayed_speaker"):
                self.last_displayed_speaker = None
            if not hasattr(self, "_transcript_hash_order"):
                self._transcript_hash_order = []
            if not hasattr(self, "_last_speaker_utterance"):
                self._last_speaker_utterance = {}
            if not hasattr(self, "_recent_displayed_texts"):
                self._recent_displayed_texts = []
            if not hasattr(self, "_fragment_merge_meta"):
                self._fragment_merge_meta = None

            while not self.transcript_queue.empty():
                item = self.transcript_queue.get()

                if isinstance(item, list):  # CHANGED: support list-of-dicts format (fix 4)
                    items_to_process = item  # CHANGED: backward compatibility (fix 4)
                else:
                    items_to_process = [item]  # CHANGED: single dict format (fix 4)

                for sub_item in items_to_process:  # CHANGED: one line per speaker segment (fix 4)
                    self._display_transcript_item(sub_item)  # CHANGED: (fix 4)

        except Exception as e:
            print(f"[ERROR] Processing UI queue: {e}")
            traceback.print_exc()

        self.after(100, self.process_ui_queue)

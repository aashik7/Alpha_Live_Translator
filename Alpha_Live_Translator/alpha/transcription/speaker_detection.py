"""Nova-3 speaker diarization and mid-utterance speaker splitting."""


class SpeakerDetectionMixin:
    """Mixin providing speaker extraction from Deepgram Nova-3 responses."""

    def _words_to_segment_text(self, words):
            """Join Deepgram word objects into a single segment string."""
            parts = []
            for word in words:
                token = word.get("punctuated_word") or word.get("word") or ""
                if token:
                    parts.append(token)
            return " ".join(parts).strip()

    def extract_speaker_from_nova3(self, data):
            """Split utterance by mid-segment speaker changes using word-level metadata."""
            try:
                alternatives = data.get("channel", {}).get("alternatives", [])
                if not alternatives:
                    return []

                alt = alternatives[0]
                words = alt.get("words", [])
                full_transcript = alt.get("transcript", "").strip()

                if not words:
                    if not full_transcript:
                        return []
                    return [{"speaker": self._fallback_speaker_detection(), "text": full_transcript}]

                segments = []
                run_speaker = None
                run_words = []

                def flush_run():
                    nonlocal run_words, run_speaker
                    if run_speaker is not None and run_words:
                        text = self._words_to_segment_text(run_words)
                        if text:
                            segments.append({"speaker": run_speaker, "text": text})
                    run_words = []

                for word in words:
                    raw_sp = word.get("speaker")
                    sp_num = int(raw_sp) + 1 if raw_sp is not None else run_speaker

                    if run_speaker is not None and sp_num is not None and sp_num != run_speaker:
                        flush_run()  # CHANGED: speaker change mid-utterance split (fix 4)
                        run_speaker = sp_num
                        run_words = [word]
                    else:
                        if run_speaker is None and sp_num is not None:
                            run_speaker = sp_num
                        run_words.append(word)

                flush_run()

                if len(segments) > 1:
                    print(f"[Speaker] Split utterance into {len(segments)} speaker segments")  # CHANGED: (fix 4)

                if segments:
                    return segments

                if full_transcript:
                    return [{"speaker": self._fallback_speaker_detection(), "text": full_transcript}]
                return []

            except Exception as e:
                print(f"[ERROR] Extracting speaker: {e}")
                return [{"speaker": 1, "text": ""}]

    def _fallback_speaker_detection(self):
            """The speaker when the provider sent no diarization: one speaker.

            Item 50 (2026-09-29). This rotated the speaker 1 -> 2 -> 3 -> 4
            whenever 4 s passed without a Results message, and Japanese and
            English both run without diarization, so every speaker label was a
            guess from pauses -- and the Japanese assembler treats a speaker
            change as a hard boundary. A pause split sentences in half (the
            owner's meeting of 2026-09-29: 45 flips, 20 mid-sentence), each half
            translated alone. Replayed on that meeting's own finals, one speaker
            gave 68 lines instead of 96 and halved the lines shown >10 s late.
            Nothing here knows who is talking, so it says one person is.
            """
            return 1

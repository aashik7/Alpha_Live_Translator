"""Deepgram Nova-3 WebSocket client, reconnect, and health monitoring."""

import json
import queue
import threading
import time
from urllib.parse import quote

import websocket
from tkinter import messagebox

from alpha.config import (
    DEEPGRAM_API_KEY,
    DEEPGRAM_ENDPOINTING_MS,
    DEEPGRAM_MODEL,
    DEEPGRAM_SAMPLE_RATE,
    DEEPGRAM_UTTERANCE_END_MS,
    DG_KEEPALIVE_INTERVAL_S,
    DG_RECONNECT_BACKOFF_MAX_S,
    HEALTH_MONITOR_INTERVAL_MS,
    LANGUAGE_CONFIG,
)

GRACEFUL_DRAIN_MAX_S = 1.5
GRACEFUL_FINALIZE_WAIT_S = 4.0
GRACEFUL_CLOSE_WAIT_S = 1.5
GRACEFUL_STOP_DEFAULT_TIMEOUT_S = 7.0


class DeepgramClientMixin:
    """Mixin providing Deepgram streaming STT WebSocket lifecycle."""

    def _build_deepgram_url(self):
            """Build the Deepgram live-listen WebSocket URL with Nova-3 accuracy options."""
            lang = self._listen_language
            lang_cfg = LANGUAGE_CONFIG.get(lang, {})  # CHANGED: read keyterms for active lang (fix 11)
            keyterm_params = ""  # CHANGED: accumulate keyterm query params (fix 11)
            for term in lang_cfg.get("keyterms", []):  # CHANGED: append each keyterm (fix 11)
                keyterm_params += f"&keyterm={quote(term)}"  # CHANGED: URL-encode keyterm (fix 11)
            params = (
                f"model={DEEPGRAM_MODEL}"
                f"&language={lang}"
                f"&punctuate=true"
                f"&smart_format=true"
                f"&diarize_model=latest"  # CHANGED: replace deprecated diarize=true (fix 1)
                f"&numerals=true"
                f"&profanity_filter=false"
                f"&redact=false"
                f"&endpointing={DEEPGRAM_ENDPOINTING_MS}"
                f"&utterance_end_ms={DEEPGRAM_UTTERANCE_END_MS}"
                f"&encoding=linear16"
                f"&sample_rate={DEEPGRAM_SAMPLE_RATE}"
                f"&channels=1"
                # utterance_end_ms requires interim_results=true on Deepgram's API;
                # interim payloads are still ignored in _deepgram_on_message.
                f"&interim_results=true"
                f"{keyterm_params}"  # CHANGED: language keyterm boosting (fix 11)
            )
            return f"wss://api.deepgram.com/v1/listen?{params}"

    def _get_language_name(self, lang_code):
            """Return display name for a Deepgram language code."""
            return LANGUAGE_CONFIG.get(lang_code, {}).get("name", lang_code)

    def _print_accuracy_startup(self, lang_code):
            """Print expected accuracy target for the selected language."""
            cfg = LANGUAGE_CONFIG.get(lang_code, {})
            lang_name = cfg.get("name", lang_code)
            expected_wer = cfg.get("expected_wer", 0.03)
            expected_accuracy = (1 - expected_wer) * 100
            print(f"[Nova-3] Model activated - Enhanced accuracy mode")
            print(f"[Language] Set to: {lang_name} ({lang_code})")
            print(f"[Diarization] diarize_model=latest enabled")  # CHANGED: reflect new diarize param (fix 1)
            print(
                f"[Accuracy] Target: {expected_accuracy:.1f}% "
                f"(WER <= {expected_wer:.2f}) for {lang_name}"
            )

    def _deepgram_on_message(self, _ws, message):
            """Handle Deepgram WebSocket messages - simplified final-only processing."""
            if not isinstance(message, str):
                return
            try:
                data = json.loads(message)
                msg_type = data.get("type", "")

                if msg_type in ("Metadata", "Open"):
                    return

                if msg_type == "UtteranceEnd":
                    print("[UtteranceEnd] Resetting speaker state")
                    self.current_speaker = None
                    self.fallback_speaker = 1
                    self.last_speech_time = time.time()
                    self._fragment_merge_meta = None
                    return

                if msg_type != "Results":
                    return

                # ONLY process final results - IGNORE interim completely
                if not data.get("is_final", False):
                    return

                alternatives = data.get("channel", {}).get("alternatives", [])
                if not alternatives:
                    return

                transcript = alternatives[0].get("transcript", "").strip()
                if not transcript:
                    return

                segments = self.extract_speaker_from_nova3(data)  # CHANGED: list of speaker segments (fix 4)
                if not segments:
                    return

                if self._dg_awaiting_transcript_reset:  # CHANGED: reset backoff after reconnect transcript (fix 5)
                    self._dg_backoff_seconds = 1.0  # CHANGED: (fix 5)
                    self._dg_awaiting_transcript_reset = False  # CHANGED: (fix 5)
                    print("[Reconnect] Backoff reset after first transcript")  # CHANGED: (fix 5)

                for segment in segments:  # CHANGED: enqueue one item per speaker run (fix 4)
                    speaker_num = segment.get("speaker", 1)
                    segment_text = segment.get("text", "").strip()
                    if not segment_text:
                        continue
                    # TODO V3: EventBus-only path after process_ui_queue migration.
                    if hasattr(self, "publish_transcript_event"):
                        self.publish_transcript_event(
                            text=segment_text,
                            speaker=speaker_num,
                            is_final=True,
                            queue_item={
                                "speaker": speaker_num,
                                "text": segment_text,
                                "is_final": True,
                            },
                        )
                    else:
                        self.transcript_queue.put(
                            {
                                "speaker": speaker_num,
                                "text": segment_text,
                                "is_final": True,
                            }
                        )
                    self._transcripts_received += 1
                    if getattr(self, "_is_finalizing", False):
                        print("[STOP] final transcript received during finalize")
                    print(f"[FINAL] Speaker {speaker_num}: {segment_text}")

            except Exception as e:
                print(f"[ERROR] Processing Deepgram message: {e}")
                import traceback
                traceback.print_exc()

    def _deepgram_on_open(self, ws):
            """Start streaming queued audio to Deepgram when the socket opens."""
            print("Nova-3 connected — streaming audio to Deepgram")

            replay_chunks = list(getattr(self, "_dg_replay_buffer", []) or [])  # CHANGED: replay after reconnect (fix 5)
            self._dg_replay_buffer = []  # CHANGED: clear replay buffer (fix 5)

            def stream_audio():
                chunks_sent = 0
                last_keepalive = time.perf_counter()  # CHANGED: track keepalive timing (fix 5/6)
                for chunk in replay_chunks:  # CHANGED: send buffered audio first (fix 5)
                    if self._stop_event.is_set():
                        return
                    try:
                        ws.send(chunk, opcode=websocket.ABNF.OPCODE_BINARY)  # CHANGED: replay buffered PCM (fix 5)
                        chunks_sent += 1  # CHANGED: (fix 5)
                        self._chunks_sent_count = chunks_sent  # CHANGED: (fix 5)
                        last_keepalive = time.perf_counter()  # CHANGED: (fix 5)
                    except Exception as exc:
                        print(f"Error replaying audio to Deepgram: {exc}")  # CHANGED: (fix 5)
                        return
                if replay_chunks:  # CHANGED: (fix 5)
                    print(f"[Reconnect] Replayed {len(replay_chunks)} buffered audio chunks")  # CHANGED: (fix 5)

                while (
                    not self._stop_event.is_set()
                    and not getattr(self, "_dg_stop_sending_audio", False)
                ):
                    try:
                        chunk = self._audio_q.get(timeout=0.1)
                    except queue.Empty:
                        if time.perf_counter() - last_keepalive >= DG_KEEPALIVE_INTERVAL_S:  # CHANGED: JSON keepalive (fix 5/6)
                            try:
                                ws.send(json.dumps({"type": "KeepAlive"}))  # CHANGED: replace silence injection (fix 6)
                                last_keepalive = time.perf_counter()  # CHANGED: (fix 5/6)
                            except Exception as exc:
                                print(f"Error sending Deepgram keepalive: {exc}")  # CHANGED: (fix 5)
                                break
                        continue
                    try:
                        ws.send(chunk, opcode=websocket.ABNF.OPCODE_BINARY)
                        chunks_sent += 1
                        self._chunks_sent_count = chunks_sent
                        last_keepalive = time.perf_counter()  # CHANGED: audio counts as activity (fix 5)
                        if chunks_sent == 1:
                            print(
                                f"First audio chunk sent to Deepgram ({len(chunk)} bytes)"
                            )
                    except Exception as exc:
                        print(f"Error sending audio to Deepgram: {exc}")
                        break

            threading.Thread(target=stream_audio, daemon=True).start()

    def _schedule_reconnect(self):
            """Queue a Deepgram reconnect attempt (daemon thread, no self.after)."""
            if not self.is_listening or self._stop_event.is_set():  # CHANGED: only while listening (fix 5)
                return
            with self._dg_reconnect_lock:  # CHANGED: (fix 5)
                if self._dg_reconnecting:  # CHANGED: avoid duplicate reconnect threads (fix 5)
                    return
                self._dg_reconnecting = True  # CHANGED: (fix 5)
            threading.Thread(target=self._reconnect_deepgram, daemon=True).start()

    def _reconnect_deepgram(self):
            """Reconnect to Deepgram with exponential backoff and audio replay."""
            try:
                buffered = []  # CHANGED: snapshot queued audio before reconnect (fix 5)
                if self._audio_q is not None:  # CHANGED: (fix 5)
                    while True:  # CHANGED: (fix 5)
                        try:
                            buffered.append(self._audio_q.get_nowait())  # CHANGED: (fix 5)
                        except queue.Empty:  # CHANGED: (fix 5)
                            break  # CHANGED: (fix 5)
                self._dg_replay_buffer = buffered  # CHANGED: replay on next on_open (fix 5)

                wait_s = min(self._dg_backoff_seconds, DG_RECONNECT_BACKOFF_MAX_S)  # CHANGED: backoff cap (fix 5)
                print(f"[Reconnect] Waiting {wait_s:.0f}s before reconnect (backoff)")  # CHANGED: (fix 5)
                time.sleep(wait_s)  # CHANGED: exponential backoff delay (fix 5)
                self._dg_backoff_seconds = min(self._dg_backoff_seconds * 2, DG_RECONNECT_BACKOFF_MAX_S)  # CHANGED: (fix 5)

                if not self.is_listening or self._stop_event.is_set():  # CHANGED: (fix 5)
                    return

                if self._dg_ws is not None:  # CHANGED: close stale socket (fix 5)
                    try:
                        self._dg_ws.close()  # CHANGED: (fix 5)
                    except Exception:
                        pass  # CHANGED: (fix 5)
                    self._dg_ws = None  # CHANGED: (fix 5)

                url = self._build_deepgram_url()  # CHANGED: fresh URL on reconnect (fix 5)
                print(f"[Reconnect] Connecting to Deepgram: {url}")  # CHANGED: (fix 5)
                self._dg_awaiting_transcript_reset = True  # CHANGED: reset backoff after transcript (fix 5)
                ws = websocket.WebSocketApp(  # CHANGED: new WebSocket session (fix 5)
                    url,  # CHANGED: (fix 5)
                    header={"Authorization": f"Token {DEEPGRAM_API_KEY}"},  # CHANGED: (fix 5)
                    on_message=self._deepgram_on_message,  # CHANGED: (fix 5)
                    on_open=self._deepgram_on_open,  # CHANGED: (fix 5)
                    on_error=self._deepgram_on_error,  # CHANGED: (fix 5)
                    on_close=self._deepgram_on_close,  # CHANGED: (fix 5)
                )  # CHANGED: (fix 5)
                self._dg_ws = ws  # CHANGED: (fix 5)
                ws.run_forever()  # CHANGED: blocking reconnect in daemon thread (fix 5)
            except Exception as exc:
                print(f"[Reconnect] Deepgram reconnect error: {exc}")  # CHANGED: (fix 5)
            finally:
                with self._dg_reconnect_lock:  # CHANGED: release reconnect lock (fix 5)
                    self._dg_reconnecting = False

    def _deepgram_on_close(self, _ws, code, msg):
            """Handle WebSocket close; schedule reconnect while listening."""
            print(f"Deepgram closed: {code} {msg}")  # CHANGED: explicit close handler (fix 5)
            if self.is_listening and not self._stop_event.is_set():  # CHANGED: auto-reconnect (fix 5)
                self._schedule_reconnect()

    def _deepgram_on_error(self, _ws, err):
            """Handle WebSocket errors; reconnect on transient failures."""
            print(f"Deepgram WebSocket error: {err}")
            err_text = str(err)
            if hasattr(self, "publish_error_event"):
                self.publish_error_event(
                    err_text,
                    source="deepgram",
                    recoverable="400" not in err_text
                    and "INVALID_QUERY_PARAMETER" not in err_text,
                )
            if "400" in err_text or "INVALID_QUERY_PARAMETER" in err_text:
                # TODO V3: Move this direct UI update to EventBus after regression testing.
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        "Deepgram Connection Error",
                        "Could not connect to Deepgram.\n\n"
                        f"{err_text}\n\n"
                        "Listening has been stopped.",
                    ),
                )
                self.after(0, lambda: self._stop_listening(graceful=False))
                return
            if self.is_listening and not self._stop_event.is_set():  # CHANGED: reconnect transient errors (fix 5)
                self._schedule_reconnect()

    def _deepgram_worker(self):
            """Run the Deepgram WebSocket connection in a background thread."""
            url = self._build_deepgram_url()
            lang_code = self._listen_language
            print(f"\n{'=' * 60}")
            print("CONNECTING TO NOVA-3")
            print("Model: nova-3")
            print(f"Language: {lang_code}")
            print("Diarization: diarize_model=latest")  # CHANGED: reflect streaming diarize param (fix 1)
            print("Processing: Final results ONLY (interim ignored in UI)")
            print(f"{'=' * 60}\n")
            print(f"Deepgram URL: {url}")
            try:
                ws = websocket.WebSocketApp(
                    url,
                    header={"Authorization": f"Token {DEEPGRAM_API_KEY}"},
                    on_message=self._deepgram_on_message,
                    on_open=self._deepgram_on_open,
                    on_error=self._deepgram_on_error,
                    on_close=self._deepgram_on_close,  # CHANGED: reconnect on close (fix 5)
                )
                self._dg_ws = ws
                ws.run_forever()
            except Exception as exc:
                print(f"Deepgram connection error: {exc}")

    def _health_monitor(self):
            """Log pipeline health every 5 seconds while listening."""
            if not self.is_listening:
                self._health_monitor_job = None
                return

            audio_qsize = self._audio_q.qsize() if self._audio_q else 0
            sys_qsize = self.sys_audio_queue.qsize() if self.sys_audio_queue else 0
            mic_qsize = self.mic_audio_queue.qsize() if self.mic_audio_queue else 0
            print(
                f"[Health] chunks_sent={self._chunks_sent_count}, "
                f"transcripts={self._transcripts_received}, "
                f"audio_q={audio_qsize}, sys_q={sys_qsize}, mic_q={mic_qsize}, "
                f"lang={self._listen_language}, "
                f"endpointing={DEEPGRAM_ENDPOINTING_MS}ms"
            )
            if (
                self._chunks_sent_count > 0
                and self._transcripts_received == 0
                and not getattr(self, "_health_no_transcript_hint_shown", False)
            ):
                print(
                    "[Health] Audio is reaching Deepgram, but no transcript has returned "
                    "yet. Check Deepgram API key, language settings, or WebSocket errors."
                )
                self._health_no_transcript_hint_shown = True
            self._health_monitor_job = self.after(
                HEALTH_MONITOR_INTERVAL_MS, self._health_monitor
            )

    def _start_health_monitor(self):
            """Begin periodic health logging."""
            if self._health_monitor_job is not None:
                self.after_cancel(self._health_monitor_job)
            self._health_monitor_job = self.after(
                HEALTH_MONITOR_INTERVAL_MS, self._health_monitor
            )

    def _stop_health_monitor(self):
            """Stop periodic health logging."""
            if self._health_monitor_job is not None:
                self.after_cancel(self._health_monitor_job)
                self._health_monitor_job = None

    def _ensure_graceful_stop_state(self):
            """Initialize graceful-stop coordination flags."""
            if not hasattr(self, "_graceful_stop_lock"):
                self._graceful_stop_lock = threading.Lock()
            if not hasattr(self, "_graceful_stop_in_progress"):
                self._graceful_stop_in_progress = False
            if not hasattr(self, "_graceful_stop_completed"):
                self._graceful_stop_completed = False
            if not hasattr(self, "_dg_stop_sending_audio"):
                self._dg_stop_sending_audio = False
            if not hasattr(self, "_is_finalizing"):
                self._is_finalizing = False

    def reset_graceful_stop_state(self):
            """Reset graceful-stop flags for a new listening session."""
            self._ensure_graceful_stop_state()
            with self._graceful_stop_lock:
                self._graceful_stop_in_progress = False
                self._graceful_stop_completed = False
            self._dg_stop_sending_audio = False
            self._is_finalizing = False

    def request_finalize(self):
            """Send Deepgram Finalize control message; safe if socket is closed."""
            self._ensure_graceful_stop_state()
            ws = getattr(self, "_dg_ws", None)
            if ws is None:
                print("[Finalize] WebSocket not available")
                return False
            try:
                ws.send(json.dumps({"type": "Finalize"}))
                print("[Finalize] Sent Finalize request")
                return True
            except Exception as exc:
                print(f"[Finalize] Failed to send: {exc}")
                return False

    def request_close_stream(self):
            """Send Deepgram CloseStream control message; safe if socket is closed."""
            self._ensure_graceful_stop_state()
            ws = getattr(self, "_dg_ws", None)
            if ws is None:
                print("[CloseStream] WebSocket not available")
                return False
            try:
                ws.send(json.dumps({"type": "CloseStream"}))
                print("[CloseStream] Sent CloseStream request")
                return True
            except Exception as exc:
                print(f"[CloseStream] Failed to send: {exc}")
                return False

    def _drain_audio_queue_to_deepgram(self, max_seconds=GRACEFUL_DRAIN_MAX_S):
            """Send already-queued PCM chunks to Deepgram within a bounded window."""
            ws = getattr(self, "_dg_ws", None)
            audio_q = getattr(self, "_audio_q", None)
            if ws is None or audio_q is None:
                return 0

            deadline = time.perf_counter() + max(0.0, max_seconds)
            sent = 0
            while time.perf_counter() < deadline:
                try:
                    chunk = audio_q.get_nowait()
                except queue.Empty:
                    time.sleep(0.05)
                    continue
                try:
                    ws.send(chunk, opcode=websocket.ABNF.OPCODE_BINARY)
                    sent += 1
                except Exception as exc:
                    print(f"[Drain] Error sending queued audio: {exc}")
                    break
            if sent:
                print(f"[Drain] Sent {sent} queued audio chunk(s) before finalize")
            return sent

    def _wait_bounded(self, seconds, deadline):
            """Sleep up to seconds without exceeding the overall graceful-stop deadline."""
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return
            time.sleep(min(seconds, remaining))

    def stop_gracefully(self, timeout_seconds=GRACEFUL_STOP_DEFAULT_TIMEOUT_S):
            """
            Finalize Deepgram, wait for pending finals, then close stream safely.

            Idempotent: repeated calls after completion are no-ops.
            Returns dict with keys: timed_out (bool), finalized (bool), closed (bool).
            """
            self._ensure_graceful_stop_state()
            with self._graceful_stop_lock:
                if self._graceful_stop_completed:
                    return {
                        "timed_out": False,
                        "finalized": False,
                        "closed": False,
                        "skipped": True,
                    }
                if self._graceful_stop_in_progress:
                    return {
                        "timed_out": False,
                        "finalized": False,
                        "closed": False,
                        "skipped": True,
                    }
                self._graceful_stop_in_progress = True
                self._is_finalizing = True

            deadline = time.perf_counter() + max(0.1, float(timeout_seconds))
            finalized = False
            closed = False

            try:
                self._wait_bounded(GRACEFUL_DRAIN_MAX_S, deadline)

                self._dg_stop_sending_audio = True
                self._drain_audio_queue_to_deepgram(
                    max_seconds=min(
                        GRACEFUL_DRAIN_MAX_S,
                        max(0.0, deadline - time.perf_counter()),
                    )
                )

                finalized = self.request_finalize()
                self._wait_bounded(GRACEFUL_FINALIZE_WAIT_S, deadline)

                closed = self.request_close_stream()
                self._wait_bounded(GRACEFUL_CLOSE_WAIT_S, deadline)

                ws = getattr(self, "_dg_ws", None)
                if ws is not None:
                    try:
                        ws.close()
                    except Exception as exc:
                        print(f"[GracefulStop] WebSocket close error: {exc}")
                    self._dg_ws = None
            finally:
                self._stop_event.set()
                with self._graceful_stop_lock:
                    self._graceful_stop_in_progress = False
                    self._graceful_stop_completed = True
                self._is_finalizing = False

            timed_out = time.perf_counter() >= deadline
            if timed_out:
                print("Graceful stop timed out; socket closed safely.")
            return {
                "timed_out": timed_out,
                "finalized": finalized,
                "closed": closed,
                "skipped": False,
            }

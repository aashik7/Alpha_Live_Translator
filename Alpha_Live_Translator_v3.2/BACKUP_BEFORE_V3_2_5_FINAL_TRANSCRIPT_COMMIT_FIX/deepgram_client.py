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
GRACEFUL_STOP_DEFAULT_TIMEOUT_S = 12.0
STOP_QUEUE_FLUSH_MAX_S = 5.0
STOP_CAPTURE_OPEN_FLUSH_MAX_S = 2.0
STOP_SETTLE_DELAY_S = 0.3
STOP_FINALIZE_WAIT_MAX_S = 5.0
STOP_CLOSE_WAIT_MAX_S = 1.5
STOP_MAX_TIMEOUT_S = 12.0


def _agent_debug_log(run_id: str, hypothesis_id: str, location: str, message: str, data=None):
    # region agent log
    try:
        payload = {
            "sessionId": "46ae0c",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        with open("debug-46ae0c.log", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception:
        pass
    # endregion


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
                # region agent log
                _agent_debug_log(
                    run_id="manual-analysis",
                    hypothesis_id="TRACE",
                    location="deepgram_client.py:_deepgram_on_message",
                    message="incoming websocket message",
                    data={
                        "type": msg_type,
                        "is_final": bool(data.get("is_final", False)),
                    },
                )
                # endregion

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
                        # region agent log
                        _agent_debug_log(
                            run_id="pre-fix",
                            hypothesis_id="H4",
                            location="deepgram_client.py:_deepgram_on_message",
                            message="final transcript received during finalize",
                            data={
                                "speaker": speaker_num,
                                "text_len": len(segment_text),
                                "queue_size": int(self.get_outgoing_audio_queue_size()) if hasattr(self, "get_outgoing_audio_queue_size") else -1,
                            },
                        )
                        # endregion
                    print(f"[FINAL] Speaker {speaker_num}: {segment_text}")

            except Exception as e:
                print(f"[ERROR] Processing Deepgram message: {e}")
                import traceback
                traceback.print_exc()

    def _deepgram_on_open(self, ws):
            """Start streaming queued audio to Deepgram when the socket opens."""
            print("Nova-3 connected — streaming audio to Deepgram")
            # region agent log
            _agent_debug_log(
                run_id="manual-analysis",
                hypothesis_id="TRACE",
                location="deepgram_client.py:_deepgram_on_open",
                message="websocket opened",
                data={
                    "replay_chunks": len(list(getattr(self, "_dg_replay_buffer", []) or [])),
                    "audio_q_size": int(self.get_outgoing_audio_queue_size()) if hasattr(self, "get_outgoing_audio_queue_size") else -1,
                },
            )
            # endregion

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
                        if chunks_sent % 100 == 0:
                            # region agent log
                            _agent_debug_log(
                                run_id="manual-analysis",
                                hypothesis_id="TRACE",
                                location="deepgram_client.py:stream_audio",
                                message="audio chunks sent milestone",
                                data={
                                    "chunks_sent": int(chunks_sent),
                                    "audio_q_size": int(self.get_outgoing_audio_queue_size()) if hasattr(self, "get_outgoing_audio_queue_size") else -1,
                                },
                            )
                            # endregion
                        if chunks_sent == 1:
                            print(
                                f"First audio chunk sent to Deepgram ({len(chunk)} bytes)"
                            )
                    except Exception as exc:
                        print(f"Error sending audio to Deepgram: {exc}")
                        break
                # region agent log
                _agent_debug_log(
                    run_id="pre-fix",
                    hypothesis_id="H2",
                    location="deepgram_client.py:stream_audio_loop_exit",
                    message="sender loop exited",
                    data={
                        "stop_event": bool(self._stop_event.is_set()),
                        "stop_sending_audio": bool(getattr(self, "_dg_stop_sending_audio", False)),
                        "queue_size_on_exit": int(self.get_outgoing_audio_queue_size()) if hasattr(self, "get_outgoing_audio_queue_size") else -1,
                    },
                )
                # endregion

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
            # region agent log
            _agent_debug_log(
                run_id="manual-analysis",
                hypothesis_id="TRACE",
                location="deepgram_client.py:_deepgram_on_close",
                message="websocket closed",
                data={
                    "code": code,
                    "msg": str(msg),
                    "is_listening": bool(getattr(self, "is_listening", False)),
                    "stop_event": bool(self._stop_event.is_set()) if hasattr(self, "_stop_event") else False,
                },
            )
            # endregion
            if self.is_listening and not self._stop_event.is_set():  # CHANGED: auto-reconnect (fix 5)
                self._schedule_reconnect()

    def _deepgram_on_error(self, _ws, err):
            """Handle WebSocket errors; reconnect on transient failures."""
            print(f"Deepgram WebSocket error: {err}")
            # region agent log
            _agent_debug_log(
                run_id="manual-analysis",
                hypothesis_id="TRACE",
                location="deepgram_client.py:_deepgram_on_error",
                message="websocket error",
                data={"error": str(err)},
            )
            # endregion
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
            # region agent log
            _agent_debug_log(
                run_id="manual-analysis",
                hypothesis_id="TRACE",
                location="deepgram_client.py:_deepgram_worker",
                message="deepgram worker starting",
                data={
                    "language": str(lang_code),
                    "endpointing_ms": int(DEEPGRAM_ENDPOINTING_MS),
                    "utterance_end_ms": int(DEEPGRAM_UTTERANCE_END_MS),
                    "sample_rate": int(DEEPGRAM_SAMPLE_RATE),
                },
            )
            # endregion
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
            # region agent log
            _agent_debug_log(
                run_id="manual-analysis",
                hypothesis_id="TRACE",
                location="deepgram_client.py:_health_monitor",
                message="pipeline health snapshot",
                data={
                    "chunks_sent": int(self._chunks_sent_count),
                    "transcripts": int(self._transcripts_received),
                    "audio_q": int(audio_qsize),
                    "sys_q": int(sys_qsize),
                    "mic_q": int(mic_qsize),
                    "language": str(self._listen_language),
                },
            )
            # endregion
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
            if not hasattr(self, "_dg_receiver_allowed"):
                self._dg_receiver_allowed = False

    def reset_graceful_stop_state(self):
            """Reset graceful-stop flags for a new listening session."""
            self._ensure_graceful_stop_state()
            with self._graceful_stop_lock:
                self._graceful_stop_in_progress = False
                self._graceful_stop_completed = False
            self._dg_stop_sending_audio = False
            self._is_finalizing = False
            self._dg_receiver_allowed = False

    def request_finalize(self):
            """Send Deepgram Finalize control message; safe if socket is closed."""
            self._ensure_graceful_stop_state()
            ws = getattr(self, "_dg_ws", None)
            if ws is None:
                print("[STOP] finalize send failed: WebSocket not available")
                return False
            try:
                ws.send(json.dumps({"type": "Finalize"}))
                print("[STOP] finalize sent")
                return True
            except Exception as exc:
                print(f"[STOP] finalize send failed: {exc}")
                return False

    def request_close_stream(self):
            """Send Deepgram CloseStream control message; safe if socket is closed."""
            self._ensure_graceful_stop_state()
            ws = getattr(self, "_dg_ws", None)
            if ws is None:
                print("[STOP] close stream send failed: WebSocket not available")
                return False
            try:
                ws.send(json.dumps({"type": "CloseStream"}))
                print("[STOP] close stream sent")
                return True
            except Exception as exc:
                print(f"[STOP] close stream send failed: {exc}")
                return False

    def get_outgoing_audio_queue_size(self) -> int:
            """Safely return outgoing audio queue size."""
            try:
                audio_q = getattr(self, "_audio_q", None)
                if audio_q is None:
                    return 0
                return max(0, int(audio_q.qsize()))
            except Exception:
                return 0

    def _get_pipeline_queue_sizes(self) -> dict:
            """Return sizes for mixer/capture pipeline queues."""
            def _safe_qsize(queue_obj):
                try:
                    if queue_obj is None:
                        return 0
                    return max(0, int(queue_obj.qsize()))
                except Exception:
                    return 0

            return {
                "audio_q": _safe_qsize(getattr(self, "_audio_q", None)),
                "sys_q": _safe_qsize(getattr(self, "sys_audio_queue", None)),
                "mic_q": _safe_qsize(getattr(self, "mic_audio_queue", None)),
            }

    def _get_pipeline_queue_total(self) -> int:
            sizes = self._get_pipeline_queue_sizes()
            return int(sizes["audio_q"] + sizes["sys_q"] + sizes["mic_q"])

    def wait_for_outgoing_audio_flush(self, timeout_seconds=5.0) -> bool:
            """
            Wait for mixer/capture pipeline queues to empty without clearing/dropping.

            Returns True when all pipeline queues reach size 0, else False on timeout.
            """
            print("[STOP] waiting for outgoing audio queue flush")
            deadline = time.perf_counter() + max(0.0, float(timeout_seconds))
            while time.perf_counter() < deadline:
                sizes = self._get_pipeline_queue_sizes()
                if sizes["audio_q"] == 0 and sizes["sys_q"] == 0 and sizes["mic_q"] == 0:
                    print("[STOP] outgoing audio queue flushed")
                    # region agent log
                    _agent_debug_log(
                        run_id="post-fix",
                        hypothesis_id="H6",
                        location="deepgram_client.py:wait_for_outgoing_audio_flush",
                        message="pipeline queues flushed",
                        data=sizes,
                    )
                    # endregion
                    return True
                time.sleep(0.05)
            remaining = self._get_pipeline_queue_sizes()
            print(
                "[STOP] outgoing audio queue flush timeout, remaining: "
                f"{remaining['audio_q'] + remaining['sys_q'] + remaining['mic_q']}"
            )
            # region agent log
            _agent_debug_log(
                run_id="post-fix",
                hypothesis_id="H6",
                location="deepgram_client.py:wait_for_outgoing_audio_flush",
                message="pipeline flush timeout",
                data=remaining,
            )
            # endregion
            return False

    def _wait_capture_open_pipeline_drain(
            self,
            timeout_seconds=STOP_CAPTURE_OPEN_FLUSH_MAX_S,
            stop_capture_fn=None,
    ) -> bool:
            """
            While capture is still open, allow tail audio to enter pipeline queues.

            Keeps capture running for the bounded window so loopback can deliver
            trailing frames, then closes capture.
            """
            deadline = time.perf_counter() + max(0.0, float(timeout_seconds))
            saw_pipeline_audio = False
            while time.perf_counter() < deadline:
                sizes = self._get_pipeline_queue_sizes()
                total = sizes["audio_q"] + sizes["sys_q"] + sizes["mic_q"]
                if total > 0:
                    saw_pipeline_audio = True
                if saw_pipeline_audio and total == 0:
                    break
                time.sleep(0.05)

            if stop_capture_fn is not None:
                stop_capture_fn()
                print("[STOP] stopped accepting new audio")
                # region agent log
                _agent_debug_log(
                    run_id="post-fix",
                    hypothesis_id="H6",
                    location="deepgram_client.py:capture_closed_after_open_window",
                    message="capture stopped after open-window drain",
                    data={
                        "saw_pipeline_audio": bool(saw_pipeline_audio),
                        "pipeline_sizes": self._get_pipeline_queue_sizes(),
                    },
                )
                # endregion
            return self._get_pipeline_queue_total() == 0

    def _allow_outgoing_audio_drain(self, max_seconds=GRACEFUL_DRAIN_MAX_S):
            """
            Drain already-queued outgoing audio without stopping the receiver.

            Does not set _stop_event or close the socket.
            """
            print("[STOP] draining outgoing audio")
            deadline = time.perf_counter() + max(0.0, max_seconds)
            while time.perf_counter() < deadline:
                time.sleep(0.05)

            self._dg_stop_sending_audio = True
            remaining = max(0.0, deadline - time.perf_counter())
            if remaining > 0:
                self._drain_audio_queue_to_deepgram(max_seconds=remaining)

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
                print(f"[STOP] drained {sent} queued audio chunk(s)")
            return sent

    def _wait_bounded(self, seconds, deadline=None):
            """Sleep up to seconds; optional overall deadline caps the wait."""
            if deadline is not None:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return
                time.sleep(min(seconds, remaining))
                return
            time.sleep(max(0.0, seconds))

    def _wait_for_final_transcripts_after_finalize(self, max_seconds=GRACEFUL_FINALIZE_WAIT_S):
            """
            Keep the receiver alive while Deepgram flushes final transcript messages.

            Does not set _stop_event or close the socket.
            """
            print("[STOP] waiting for final transcripts")
            self._dg_receiver_allowed = True
            checker = getattr(self, "_dg_receiver_allowed_check", None)
            deadline = time.perf_counter() + max(0.0, max_seconds)
            while time.perf_counter() < deadline:
                if callable(checker):
                    checker(self)
                time.sleep(0.05)
            self._dg_receiver_allowed = False

    def stop_gracefully(
            self,
            timeout_seconds=GRACEFUL_STOP_DEFAULT_TIMEOUT_S,
            stop_capture_fn=None,
    ):
            """
            Finalize Deepgram, wait for pending finals, then close stream safely.

            Idempotent: repeated calls after completion are no-ops.
            The receiver stays alive until after the post-Finalize wait completes.
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

            print("[STOP] finalizing started")
            deadline = time.perf_counter() + max(0.1, float(timeout_seconds))
            finalized = False
            closed = False
            timed_out = False

            try:
                pipeline_start = self._get_pipeline_queue_sizes()
                # region agent log
                _agent_debug_log(
                    run_id="post-fix",
                    hypothesis_id="H1",
                    location="deepgram_client.py:stop_gracefully_start",
                    message="graceful stop started",
                    data={
                        "timeout_seconds": float(timeout_seconds),
                        "pipeline_start": pipeline_start,
                        "pipeline_total_start": int(
                            pipeline_start["audio_q"]
                            + pipeline_start["sys_q"]
                            + pipeline_start["mic_q"]
                        ),
                        "stop_event_start": bool(self._stop_event.is_set()),
                        "ws_present": bool(getattr(self, "_dg_ws", None) is not None),
                        "capture_deferred": bool(stop_capture_fn is not None),
                    },
                )
                # endregion
                self._dg_stop_sending_audio = False

                open_budget = min(
                    STOP_CAPTURE_OPEN_FLUSH_MAX_S,
                    max(0.0, deadline - time.perf_counter()),
                )
                if open_budget > 0 and stop_capture_fn is not None:
                    self._wait_capture_open_pipeline_drain(
                        timeout_seconds=open_budget,
                        stop_capture_fn=stop_capture_fn,
                    )
                elif stop_capture_fn is not None:
                    stop_capture_fn()
                    print("[STOP] stopped accepting new audio")

                pipeline_before = self._get_pipeline_queue_sizes()
                print(
                    "[STOP] outgoing queue size before flush: "
                    f"{pipeline_before['audio_q'] + pipeline_before['sys_q'] + pipeline_before['mic_q']}"
                )

                flush_budget = min(
                    STOP_QUEUE_FLUSH_MAX_S,
                    max(0.0, deadline - time.perf_counter()),
                )
                if flush_budget > 0:
                    flushed = self.wait_for_outgoing_audio_flush(timeout_seconds=flush_budget)
                    # region agent log
                    _agent_debug_log(
                        run_id="post-fix",
                        hypothesis_id="H3",
                        location="deepgram_client.py:stop_gracefully_after_flush_wait",
                        message="outgoing flush wait finished",
                        data={
                            "flush_budget": float(flush_budget),
                            "flushed": bool(flushed),
                            "pipeline_after_wait": self._get_pipeline_queue_sizes(),
                        },
                    )
                    # endregion

                settle_budget = min(
                    STOP_SETTLE_DELAY_S,
                    max(0.0, deadline - time.perf_counter()),
                )
                if settle_budget > 0:
                    self._wait_bounded(settle_budget)

                self._dg_stop_sending_audio = True
                print("[STOP] sending Finalize")
                # region agent log
                _agent_debug_log(
                    run_id="post-fix",
                    hypothesis_id="H5",
                    location="deepgram_client.py:stop_gracefully_before_finalize",
                    message="about to send finalize",
                    data={
                        "pipeline_before_finalize": self._get_pipeline_queue_sizes(),
                        "receiver_allowed_before_finalize": bool(self._dg_receiver_allowed),
                    },
                )
                # endregion
                finalized = self.request_finalize()

                finalize_budget = min(
                    STOP_FINALIZE_WAIT_MAX_S,
                    max(0.0, deadline - time.perf_counter()),
                )
                if finalize_budget > 0:
                    print("[STOP] waiting for final transcript messages")
                    self._wait_for_final_transcripts_after_finalize(
                        max_seconds=finalize_budget
                    )

                print("[STOP] sending CloseStream")
                closed = self.request_close_stream()

                close_budget = min(
                    STOP_CLOSE_WAIT_MAX_S,
                    max(0.0, deadline - time.perf_counter()),
                )
                if close_budget > 0:
                    self._wait_bounded(close_budget)

                ws = getattr(self, "_dg_ws", None)
                if ws is not None:
                    try:
                        ws.close()
                        print("[STOP] socket closed")
                    except Exception as exc:
                        print(f"[STOP] socket close error: {exc}")
                    self._dg_ws = None

                timed_out = time.perf_counter() >= deadline
                if timed_out:
                    print("Graceful stop timed out; socket closed safely.")
            finally:
                self._dg_receiver_allowed = False
                with self._graceful_stop_lock:
                    self._graceful_stop_in_progress = False
                    self._graceful_stop_completed = True
                self._is_finalizing = False
                self._stop_event.set()
                print("[STOP] finalizing completed")
                # region agent log
                _agent_debug_log(
                    run_id="post-fix",
                    hypothesis_id="H1",
                    location="deepgram_client.py:stop_gracefully_finally",
                    message="graceful stop finished",
                    data={
                        "finalized": bool(finalized),
                        "closed": bool(closed),
                        "timed_out": bool(timed_out),
                        "pipeline_end": self._get_pipeline_queue_sizes(),
                        "stop_event_end": bool(self._stop_event.is_set()),
                    },
                )
                # endregion

            return {
                "timed_out": timed_out,
                "finalized": finalized,
                "closed": closed,
                "skipped": False,
            }

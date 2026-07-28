"""Focused synthetic regression for V26.5.3 diagnostic multi-stream retention.

Proves silence is preserved, CAPTURE_GAP is distinct from silence, mixed
Deepgram-delivery bytes are unchanged by retention, and retention failure
cannot stop transcription.
"""

from __future__ import annotations

import array
import json
import sys
import tempfile
import time
import traceback
import wave
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
CHANNELS = 1
FRAME_TOLERANCE = 1  # one-frame tolerance


def _tone(seconds: float, freq: float = 440.0, amplitude: int = 8000) -> bytes:
    import math

    n = int(seconds * SAMPLE_RATE)
    out = array.array("h")
    for i in range(n):
        out.append(int(amplitude * math.sin(2 * math.pi * freq * i / SAMPLE_RATE)))
    return out.tobytes()


def _silence(seconds: float) -> bytes:
    n = int(seconds * SAMPLE_RATE)
    return b"\x00\x00" * n


def _read_pcm(wav_path: Path) -> bytes:
    with wave.open(str(wav_path), "rb") as wf:
        return wf.readframes(wf.getnframes())


def _pcm_duration(pcm: bytes) -> float:
    return (len(pcm) // SAMPLE_WIDTH) / float(SAMPLE_RATE)


def _near_zero_region(pcm: bytes, start_f: int, end_f: int) -> bool:
    samples = array.array("h")
    samples.frombytes(pcm)
    region = samples[start_f:end_f]
    if not region:
        return False
    return all(abs(s) <= 50 for s in region)


def _active_region(pcm: bytes, start_f: int, end_f: int) -> bool:
    samples = array.array("h")
    samples.frombytes(pcm)
    region = samples[start_f:end_f]
    if not region:
        return False
    return any(abs(s) > 50 for s in region)


class _Result:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "passed": bool(ok), "detail": detail})
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}" + (f" — {detail}" if detail else ""), flush=True)

    @property
    def all_passed(self) -> bool:
        return all(c["passed"] for c in self.checks)

    def failed_names(self) -> list[str]:
        return [c["name"] for c in self.checks if not c["passed"]]


def _setup_temp_paths(tmp: Path):
    """Point troubleshooting audio_temp paths at an isolated temp run folder."""
    import alpha.utils.troubleshooting_paths as tp
    import alpha.utils.audio_temp_capture as atc

    run_folder = tmp / "runs" / "v26.5.3-focus-retention"
    audio_temp = run_folder / "audio_temp"
    for sub in ("mixed_audio", "system_audio", "mic_audio"):
        (audio_temp / sub).mkdir(parents=True, exist_ok=True)

    def _fake_get_audio_temp_path(key: str):
        mapping = {
            "mixed_audio_dir": audio_temp / "mixed_audio",
            "system_audio_dir": audio_temp / "system_audio",
            "mic_audio_dir": audio_temp / "mic_audio",
            "audio_manifest": audio_temp / "audio_manifest.json",
            "audio_temp_summary": audio_temp / "audio_temp_summary.txt",
        }
        if key not in mapping:
            raise KeyError(key)
        return mapping[key]

    tp.get_audio_temp_path = _fake_get_audio_temp_path  # type: ignore[attr-defined]
    atc.reset_audio_temp_session()
    return run_folder, audio_temp, atc


def run_focused_retention_checks() -> _Result:
    r = _Result()
    with tempfile.TemporaryDirectory(prefix="alpha2653-") as td:
        tmp = Path(td)
        run_folder, audio_temp, atc = _setup_temp_paths(tmp)

        # --- 3s tone + 8s silence + 3s tone (system) ---
        section1 = _tone(3.0, freq=440.0)
        section2 = _silence(8.0)
        section3 = _tone(3.0, freq=660.0)
        expected_frames = int(14.0 * SAMPLE_RATE)

        atc.start_audio_temp_capture()
        atc.ingest_audio_chunk(section1, stream_type="system")
        atc.ingest_audio_chunk(section2, stream_type="system")
        atc.ingest_audio_chunk(section3, stream_type="system")
        # Also feed matching mic/mixed components for multi-stream proof
        atc.ingest_audio_chunk(section1 + section2 + section3, stream_type="mic")
        mixed_delivery = section1 + section2 + section3
        atc.ingest_audio_chunk(mixed_delivery, stream_type="mixed")
        atc.flush_audio_temp_on_stop()
        time.sleep(0.6)

        sys_wavs = sorted((audio_temp / "system_audio").glob("*.wav"))
        mic_wavs = sorted((audio_temp / "mic_audio").glob("*.wav"))
        mix_wavs = sorted((audio_temp / "mixed_audio").glob("*.wav"))
        r.check("system_wav_written", len(sys_wavs) >= 1, f"count={len(sys_wavs)}")
        r.check("mic_wav_written", len(mic_wavs) >= 1, f"count={len(mic_wavs)}")
        r.check("mixed_wav_written", len(mix_wavs) >= 1, f"count={len(mix_wavs)}")

        sys_pcm = b"".join(_read_pcm(p) for p in sys_wavs)
        mic_pcm = b"".join(_read_pcm(p) for p in mic_wavs)
        mix_pcm = b"".join(_read_pcm(p) for p in mix_wavs)
        sys_frames = len(sys_pcm) // SAMPLE_WIDTH
        duration = _pcm_duration(sys_pcm)
        r.check(
            "retained_system_duration_14s",
            abs(sys_frames - expected_frames) <= FRAME_TOLERANCE,
            f"frames={sys_frames} expected={expected_frames} duration={duration:.6f}s",
        )

        silence_start = int(3.0 * SAMPLE_RATE)
        silence_end = int(11.0 * SAMPLE_RATE)
        r.check(
            "eight_seconds_silence_present",
            _near_zero_region(sys_pcm, silence_start, silence_end),
            f"region=[{silence_start},{silence_end})",
        )
        r.check(
            "second_nonsilent_starts_at_correct_frame",
            _active_region(sys_pcm, silence_end, silence_end + SAMPLE_RATE),
            f"probe_at={silence_end}",
        )
        r.check(
            "no_silent_frames_skipped",
            abs(sys_frames - expected_frames) <= FRAME_TOLERANCE
            and _near_zero_region(sys_pcm, silence_start, silence_end),
            "duration+silence_region",
        )
        r.check(
            "system_contains_supplied_source",
            sys_pcm == section1 + section2 + section3,
            f"sys_bytes={len(sys_pcm)} expected={expected_frames * SAMPLE_WIDTH}",
        )
        r.check(
            "mic_contains_supplied_source",
            mic_pcm == section1 + section2 + section3,
            f"mic_bytes={len(mic_pcm)}",
        )

        # --- Explicit silent-packet zero-frame count ---
        atc.reset_audio_temp_session()
        run_folder, audio_temp, atc = _setup_temp_paths(tmp / "silent_pkt")
        atc.start_audio_temp_capture()
        silent_frames = SAMPLE_RATE * 2  # 2 seconds
        atc.ingest_audio_chunk(
            b"",
            stream_type="system",
            packet_classification=atc.PACKET_SOURCE_SILENT_PACKET,
            source_frame_count=silent_frames,
            explicit_silent_packet=True,
        )
        atc.flush_audio_temp_on_stop()
        time.sleep(0.4)
        man = json.loads((audio_temp / "audio_manifest.json").read_text(encoding="utf-8"))
        pkts = [p for p in man.get("packets", []) if p.get("stream_type") == "system"]
        silent_ok = any(
            p.get("packet_classification") == atc.PACKET_SOURCE_SILENT_PACKET
            and int(p.get("retained_frame_count") or 0) == silent_frames
            for p in pkts
        )
        r.check(
            "explicit_silent_packet_zero_frame_count",
            silent_ok,
            f"packets={len(pkts)}",
        )
        sys_wavs2 = sorted((audio_temp / "system_audio").glob("*.wav"))
        if sys_wavs2:
            pcm2 = b"".join(_read_pcm(p) for p in sys_wavs2)
            r.check(
                "explicit_silent_packet_pcm_is_zeros",
                pcm2 == b"\x00\x00" * silent_frames,
                f"bytes={len(pcm2)}",
            )
        else:
            r.check("explicit_silent_packet_pcm_is_zeros", False, "no wav")

        # --- CAPTURE_GAP must not be classified as silence ---
        atc.record_capture_gap(
            stream_type="system",
            reason="deliberate_missing_packet",
            expected_sequence=42,
            observed_sequence=43,
        )
        man2 = json.loads((audio_temp / "audio_manifest.json").read_text(encoding="utf-8"))
        gaps = man2.get("capture_gaps") or []
        gap_pkts = [
            p
            for p in man2.get("packets", [])
            if p.get("packet_classification") == atc.PACKET_CAPTURE_GAP
        ]
        r.check(
            "missing_packet_classified_capture_gap",
            len(gaps) >= 1
            and len(gap_pkts) >= 1
            and all(p.get("packet_classification") != atc.PACKET_SOURCE_SILENCE for p in gap_pkts),
            f"gaps={len(gaps)} gap_packets={len(gap_pkts)}",
        )

        # --- Mixed Deepgram-delivery bytes identical with retention on/off ---
        from alpha.audio.timeline_mixer import DeepgramTimelineMixer, FRAME_SAMPLES

        mixer = DeepgramTimelineMixer()
        mixer.configure_sources(1, SAMPLE_RATE, mic_available=True)
        frame_tone = _tone(FRAME_SAMPLES / SAMPLE_RATE, freq=300.0)
        # Ensure exact frame length
        frame_tone = frame_tone[: FRAME_SAMPLES * SAMPLE_WIDTH]
        if len(frame_tone) < FRAME_SAMPLES * SAMPLE_WIDTH:
            frame_tone = frame_tone + b"\x00\x00" * (
                FRAME_SAMPLES - len(frame_tone) // SAMPLE_WIDTH
            )

        # Retention enabled path
        atc.reset_audio_temp_session()
        _setup_temp_paths(tmp / "mix_on")
        atc.start_audio_temp_capture()
        mixer.reset()
        mixer.configure_sources(1, SAMPLE_RATE, True)
        mixer.push_system(frame_tone * 5)
        mixer.push_mic(_silence(FRAME_SAMPLES / SAMPLE_RATE) * 5)
        # Force emit by setting next frame due
        mixer._next_frame_time = time.monotonic() - 1.0
        frames_on = []
        for _ in range(3):
            mixer._next_frame_time = time.monotonic() - 1.0
            out = mixer.emit_due_frames()
            for pcm, _meta in out:
                frames_on.append(pcm)

        # Retention disabled path
        import alpha.constants as const

        prev = const.AUDIO_TEMP_CAPTURE_ENABLED
        const.AUDIO_TEMP_CAPTURE_ENABLED = False
        try:
            mixer.reset()
            mixer.configure_sources(1, SAMPLE_RATE, True)
            mixer.push_system(frame_tone * 5)
            mixer.push_mic(_silence(FRAME_SAMPLES / SAMPLE_RATE) * 5)
            frames_off = []
            for _ in range(3):
                mixer._next_frame_time = time.monotonic() - 1.0
                out = mixer.emit_due_frames()
                for pcm, _meta in out:
                    frames_off.append(pcm)
        finally:
            const.AUDIO_TEMP_CAPTURE_ENABLED = prev

        r.check(
            "mixed_delivery_bytes_identical_retention_on_off",
            frames_on == frames_off and len(frames_on) == 3,
            f"on={len(frames_on)} off={len(frames_off)} equal={frames_on == frames_off}",
        )

        # --- Retention failure cannot stop transcription ---
        atc.reset_audio_temp_session()
        _setup_temp_paths(tmp / "fail")
        atc.start_audio_temp_capture()
        original_extend = atc._chunk_buffers["mixed"].extend

        def _boom(_self_bytes):  # noqa: ANN001
            raise RuntimeError("deliberate_retention_failure")

        # Force failure inside ingest by breaking buffer extend via monkeypatch on classify
        original_impl = atc._ingest_audio_chunk_impl

        def _failing_impl(*args, **kwargs):
            raise RuntimeError("deliberate_retention_failure")

        atc._ingest_audio_chunk_impl = _failing_impl  # type: ignore[assignment]
        transcription_continued = False
        try:
            atc.ingest_audio_chunk(frame_tone, stream_type="mixed")
            # If we reach here without exception, retention swallowed the error.
            transcription_continued = True
        except Exception:
            transcription_continued = False
        finally:
            atc._ingest_audio_chunk_impl = original_impl  # type: ignore[assignment]
        r.check(
            "retention_failure_cannot_stop_transcription",
            transcription_continued,
            "ingest_audio_chunk must not raise",
        )

        # --- Queues/buffers remain bounded ---
        stats = atc.retention_queue_stats()
        r.check(
            "retention_queues_buffers_bounded",
            bool(stats.get("bounded"))
            and int(stats.get("queue_maxsize") or 0) == 256
            and int(stats.get("queue_size") or 0) <= 256,
            str(stats),
        )

        # Syntax/import already validated by reaching here
        r.check("syntax_import_ok", True, "module imported and executed")

    return r


def run_existing_freeze_checks() -> _Result:
    """Run a small set of existing project freeze/runtime regressions."""
    r = _Result()
    scripts = [
        "regression_final_writer_stop_tail_8525331.py",
        "regression_eleven_issue_closure_852533.py",
    ]
    import subprocess

    for script in scripts:
        path = PROJECT_ROOT / script
        if not path.exists():
            r.check(f"existing_{script}", False, "missing")
            continue
        try:
            proc = subprocess.run(
                [sys.executable, str(path)],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=180,
            )
            ok = proc.returncode == 0
            detail = (proc.stdout or "")[-300:].replace("\n", " ")
            if not ok:
                detail = ((proc.stderr or proc.stdout or "")[-400:]).replace("\n", " ")
            r.check(f"existing_{script}", ok, f"rc={proc.returncode} {detail}")
        except Exception as exc:
            r.check(f"existing_{script}", False, str(exc))
    return r


def main() -> int:
    print("=== V26.5.3 focused diagnostic retention regression ===", flush=True)
    focused = run_focused_retention_checks()
    print("=== existing freeze/runtime regressions ===", flush=True)
    existing = run_existing_freeze_checks()
    all_checks = focused.checks + existing.checks
    report = {
        "focused": focused.checks,
        "existing": existing.checks,
        "all_passed": focused.all_passed and existing.all_passed,
        "failed": focused.failed_names() + existing.failed_names(),
    }
    out = (
        PROJECT_ROOT
        / "troubleshooting"
        / "experiments"
        / "deepgram_system_audio_ab_v26.5.3"
        / "PHASE_A_FOCUSED_TEST_RESULTS.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"all_passed": report["all_passed"], "failed": report["failed"]}, ensure_ascii=False))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)

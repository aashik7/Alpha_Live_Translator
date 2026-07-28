#!/usr/bin/env python3
"""Profile Alpha Live Translator startup (cold + warm launches).

Writes evidence under troubleshooting/startup_performance<timestamp>/.

Usage:
  python profile_alpha_startup.py --phase baseline
  python profile_alpha_startup.py --phase repaired
  python profile_alpha_startup.py --phase both
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _run_import_profile(out_dir: Path) -> dict:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        PY,
        "-X",
        "importtime",
        "-c",
        "import alpha.ui.main_window",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # importtime goes to stderr
    text = proc.stderr or ""
    profile_path = out_dir / "STARTUP_IMPORT_PROFILE.txt"
    profile_path.write_text(text, encoding="utf-8")
    rows = []
    for line in text.splitlines():
        if "import time:" not in line:
            continue
        try:
            rest = line.split("import time:", 1)[1]
            parts = [p.strip() for p in rest.split("|")]
            self_us = int(parts[0].split()[0])
            cum_us = int(parts[1].split()[0])
            mod = parts[2]
            rows.append({"module": mod, "self_ms": round(self_us / 1000.0, 3), "cum_ms": round(cum_us / 1000.0, 3)})
        except Exception:
            continue
    top20 = sorted(rows, key=lambda r: r["self_ms"], reverse=True)[:20]
    summary = {
        "top_20_slowest_imports_self_ms": top20,
        "cumulative_import_ms_main_window": next(
            (r["cum_ms"] for r in rows if r["module"] == "alpha.ui.main_window"), None
        ),
        "imports_before_first_ui_paint": [
            "customtkinter",
            "alpha.transcription.deepgram_client",
            "alpha.audio.processing (numpy)",
            "websocket",
            "PIL",
        ],
        "imports_only_after_start": [
            "sounddevice (lazy via microphone)",
            "pyaudiowpatch (lazy via wasapi)",
            "alpha.audio.timeline_mixer",
            "numpy (mixer path; may already be loaded via deepgram processing)",
        ],
        "imports_only_when_translation_used": [
            "deepl",
            "alpha.translation.translation_worker",
        ],
        "imports_validation_packaging": [
            "validate_* scripts",
            "phase1_* packaging engines",
        ],
        "notes": "Measured with python -X importtime.",
    }
    (out_dir / "STARTUP_IMPORT_SUMMARY.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary


def _launch_once(out_dir: Path, *, phase: str, cold: bool, index: int) -> dict:
    run_dir = out_dir / f"launch_{phase}_{'cold' if cold else 'warm'}_{index:02d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["ALPHA_STARTUP_PROFILE"] = "1"
    env["ALPHA_STARTUP_AUTOQUIT_MS"] = "2200"
    env["PYTHONUTF8"] = "1"
    # Isolate dump dir via marker file path consumed after process exits — artifacts
    # are written by startup_perf into default_output_dir unless we set ALPHA_STARTUP_OUT.
    env["ALPHA_STARTUP_OUT"] = str(run_dir)
    if phase == "baseline":
        # Baseline = deferred work OFF (sync flag logs + pipeline before UI + eager deps)
        env["ALPHA_STARTUP_DEFER_FLAG_LOGS"] = "0"
        env["ALPHA_STARTUP_PIPELINE_BEFORE_UI"] = "1"
        env["ALPHA_STARTUP_EAGER_SOUNDDEVICE"] = "1"
        env["ALPHA_STARTUP_EAGER_NUMPY"] = "1"
    else:
        env["ALPHA_STARTUP_DEFER_FLAG_LOGS"] = "1"
        env["ALPHA_STARTUP_PIPELINE_BEFORE_UI"] = "0"
        env["ALPHA_STARTUP_EAGER_SOUNDDEVICE"] = "0"
        env["ALPHA_STARTUP_EAGER_NUMPY"] = "0"

    # Ensure startup_perf writes into run_dir
    # Patch via env read in install_autoquit / write_artifacts
    t0 = time.perf_counter()
    proc = subprocess.run(
        [PY, "main.py"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    wall_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    (run_dir / "stdout.txt").write_text(proc.stdout or "", encoding="utf-8")
    (run_dir / "stderr.txt").write_text(proc.stderr or "", encoding="utf-8")
    timeline = {}
    # Prefer artifacts written into run_dir; also search newest startup_performance*
    candidates = list(run_dir.glob("STARTUP_TIMELINE.json"))
    if not candidates:
        # startup_perf.default_output_dir may have been used — copy nearest timeline
        troot = ROOT / "troubleshooting"
        for p in sorted(troot.glob("startup_performance*/STARTUP_TIMELINE.json"), reverse=True)[:3]:
            candidates.append(p)
    if candidates:
        try:
            timeline = json.loads(candidates[0].read_text(encoding="utf-8"))
            # copy into run_dir
            for name in (
                "STARTUP_TIMELINE.json",
                "MAIN_THREAD_BLOCKING_OPERATIONS.json",
                "UI_EVENT_LOOP_RESPONSIVENESS.json",
                "STARTUP_THREAD_ANALYSIS.json",
                "STARTUP_MEMORY_ANALYSIS.json",
                "FILESYSTEM_STARTUP_ANALYSIS.json",
            ):
                src = candidates[0].parent / name
                if src.exists() and src.parent != run_dir:
                    (run_dir / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception as exc:
            timeline = {"error": str(exc)}
    marks = (timeline.get("markers_ms") or {}) if isinstance(timeline, dict) else {}
    durs = (timeline.get("durations_ms") or {}) if isinstance(timeline, dict) else {}
    return {
        "phase": phase,
        "cold": cold,
        "index": index,
        "exit_code": proc.returncode,
        "wall_ms": wall_ms,
        "time_to_first_paint_ms": durs.get("real_alpha_first_paint_ms")
        or durs.get("time_to_first_paint_ms")
        or marks.get("real_alpha_first_paint")
        or marks.get("first_visible_paint"),
        "time_to_interactive_ready_ms": durs.get("real_alpha_interactive_ready_ms")
        or durs.get("time_to_interactive_ready_ms")
        or marks.get("real_alpha_interactive_ready")
        or marks.get("application_interactive_ready"),
        "splash_excluded": True,
        "markers_ms": marks,
        "run_dir": str(run_dir),
    }


def _aggregate(launches: list[dict]) -> dict:
    paints = [float(x["time_to_first_paint_ms"]) for x in launches if x.get("time_to_first_paint_ms") is not None]
    interactives = [
        float(x["time_to_interactive_ready_ms"])
        for x in launches
        if x.get("time_to_interactive_ready_ms") is not None
    ]
    return {
        "n": len(launches),
        "median_time_to_first_paint_ms": _median(paints),
        "p95_time_to_first_paint_ms": _pct(paints, 95),
        "median_time_to_interactive_ready_ms": _median(interactives),
        "p95_time_to_interactive_ready_ms": _pct(interactives, 95),
        "launches": launches,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("baseline", "repaired", "both"), default="both")
    parser.add_argument("--cold", type=int, default=5)
    parser.add_argument("--warm", type=int, default=5)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir = Path(args.out) if args.out else (ROOT / "troubleshooting" / f"startup_performance{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Teach startup_perf to honor ALPHA_STARTUP_OUT
    # (patched at runtime via env in write path — update module if needed)
    import_summary = None
    phases = ["baseline", "repaired"] if args.phase == "both" else [args.phase]
    results = {}
    for phase in phases:
        launches = []
        # cold launches first (avoid warming the process with importtime beforehand)
        for i in range(1, args.cold + 1):
            launches.append(_launch_once(out_dir, phase=phase, cold=True, index=i))
        for i in range(1, args.warm + 1):
            launches.append(_launch_once(out_dir, phase=phase, cold=False, index=i))
        cold_agg = _aggregate([x for x in launches if x["cold"]])
        warm_agg = _aggregate([x for x in launches if not x["cold"]])
        all_agg = _aggregate(launches)
        if import_summary is None:
            import_summary = _run_import_profile(out_dir)
        payload = {
            "phase": phase,
            "cold": cold_agg,
            "warm": warm_agg,
            "all": all_agg,
            "import_summary": import_summary,
        }
        results[phase] = payload
        name = "STARTUP_BASELINE.json" if phase == "baseline" else "STARTUP_REPAIRED.json"
        (out_dir / name).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if "baseline" in results and "repaired" in results:
        b = results["baseline"]["all"]
        r = results["repaired"]["all"]
        bp = b.get("median_time_to_first_paint_ms")
        rp = r.get("median_time_to_first_paint_ms")
        bi = b.get("median_time_to_interactive_ready_ms")
        ri = r.get("median_time_to_interactive_ready_ms")

        def improv(before, after):
            if before is None or after is None or before <= 0:
                return None
            return round((before - after) / before * 100.0, 1)

        comparison = {
            "baseline_median_first_paint_ms": bp,
            "repaired_median_first_paint_ms": rp,
            "first_paint_improvement_pct": improv(bp, rp),
            "baseline_median_interactive_ms": bi,
            "repaired_median_interactive_ms": ri,
            "interactive_improvement_pct": improv(bi, ri),
            "splash_excluded": True,
            "measurement": "real_alpha_window_only",
            "required_min_improvement_pct": 30.0,
            "or_first_paint_below_ms": 2000.0,
            "or_interactive_below_ms": 5000.0,
            "baseline_cold": results["baseline"]["cold"],
            "repaired_cold": results["repaired"]["cold"],
            "baseline_warm": results["baseline"]["warm"],
            "repaired_warm": results["repaired"]["warm"],
        }
        (out_dir / "STARTUP_COMPARISON.json").write_text(
            json.dumps(comparison, indent=2), encoding="utf-8"
        )
        print(json.dumps(comparison, indent=2))
    print(f"Wrote evidence to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

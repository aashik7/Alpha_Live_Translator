"""Scan a completed live run's logs for interim-ghost-line fix evidence
(commit 78eb59e) and write a self-contained, graphical HTML report.

What it looks at
-----------------
Every interim/final decision made by `main_window.py::
_apply_final_interim_comparison` and every firing of
`_check_interim_ghost_watchdog` is logged as an `[INTERIM] ...` NDJSON
line in `logs/async_debug.log` under the run folder. This script reads
that file (stdlib only, no extra install needed) and reconstructs:

  * how many times each decision ("action") fired
  * a chronological timeline of every decision
  * every watchdog firing (an orphaned interim actually being caught)
  * for every decision that KEPT the interim on screen, how long that
    interim had gone unrefreshed at the moment of the decision -- this is
    the number that would blow past the 1500ms
    (INTERIM_GHOST_TTL_MS) ceiling if the bug had reproduced

It then renders one self-contained HTML file (inline SVG charts, no
CDN/network dependency) with a plain-English PASS/WARN verdict at the
top, so the report can be opened directly in a browser.

Usage
-----
    python tools/scan_interim_ghost_evidence.py [run_folder]

With no argument, the most recent run folder under
`troubleshooting/runs/` (excluding `_pending`) is used. The report is
written next to the source log, as
`<run_folder>/interim_ghost_report.html`, and the path is printed at the
end.
"""

from __future__ import annotations

import html
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = PROJECT_ROOT / "troubleshooting" / "runs"

# Mirrors alpha.constants.INTERIM_GHOST_TTL_MS. Kept as a plain literal
# (not imported) so this script stays runnable standalone, without
# pulling in the full alpha package / its side effects.
INTERIM_GHOST_TTL_MS = 1500

# Actions that clear the interim line -- the fix considers these "resolved".
CLEARING_ACTIONS = {
    "clear_interim",
    "clear_interim_unrelated",
    "clear_interim_same_utterance",
}
# Actions that intentionally keep a genuinely-different, still-live
# utterance's interim on screen -- expected, not a defect.
LIVE_KEEP_ACTIONS = {"keep_interim_other_utterance"}
# The pre-fix ambiguous default. Still legitimate for the "final is a
# subset of a longer live interim" case (A3 in the unit tests) -- only a
# problem if it correlates with a stale, unrefreshed interim (checked below).
AMBIGUOUS_KEEP_ACTIONS = {"keep_interim"}


def find_latest_run() -> Optional[Path]:
    if not RUNS_DIR.is_dir():
        return None
    candidates = [
        d
        for d in RUNS_DIR.iterdir()
        if d.is_dir() and d.name != "_pending" and (d / "logs" / "async_debug.log").exists()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.name)


def read_manifest(run_dir: Path) -> dict[str, Any]:
    manifest_path = run_dir / "RUN_MANIFEST.json"
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def iter_ndjson_lines(log_path: Path):
    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw or not raw.startswith("{"):
                continue  # skip the occasional plain-text prefix line
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue


def scan(run_dir: Path) -> dict[str, Any]:
    log_path = run_dir / "logs" / "async_debug.log"
    events: list[dict[str, Any]] = []  # unified timeline
    for row in iter_ndjson_lines(log_path):
        if row.get("hypothesisId") != "INTERIM":
            continue
        message = row.get("message", "")
        data = row.get("data") or {}
        ts = row.get("timestamp")
        if not isinstance(ts, (int, float)):
            continue
        if message == "[INTERIM] received":
            events.append({"kind": "received", "ts": ts, "data": data})
        elif message == "[INTERIM] final comparison":
            events.append({"kind": "comparison", "ts": ts, "data": data})
        elif message == "[INTERIM] ghost watchdog cleared":
            events.append({"kind": "watchdog", "ts": ts, "data": data})
        elif message == "[INTERIM] update_start":
            # Redundant with "received" for our purposes but timestamps
            # can be marginally earlier; ignore to avoid double-counting.
            continue

    events.sort(key=lambda e: e["ts"])

    action_counts: Counter = Counter()
    comparisons: list[dict[str, Any]] = []
    watchdog_fires: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []

    last_refresh_ts: Optional[float] = None  # last time the CURRENT interim was fed
    session_start_ts: Optional[float] = None
    session_end_ts: Optional[float] = None

    for ev in events:
        ts = ev["ts"]
        if session_start_ts is None:
            session_start_ts = ts
        session_end_ts = ts

        if ev["kind"] == "received":
            last_refresh_ts = ts
        elif ev["kind"] == "watchdog":
            watchdog_fires.append({"ts": ts, **ev["data"]})
            last_refresh_ts = None  # watchdog cleared it
        elif ev["kind"] == "comparison":
            data = ev["data"]
            action = str(data.get("action") or "unknown")
            action_counts[action] += 1
            age_ms = None
            if action in AMBIGUOUS_KEEP_ACTIONS or action in LIVE_KEEP_ACTIONS:
                if last_refresh_ts is not None:
                    age_ms = ts - last_refresh_ts
            record = {
                "ts": ts,
                "action": action,
                "age_ms": age_ms,
                "final_preview": data.get("final_preview", ""),
                "interim_preview": data.get("interim_preview", ""),
                "final_utterance_id": data.get("final_utterance_id", ""),
                "interim_utterance_id": data.get("interim_utterance_id", ""),
            }
            comparisons.append(record)
            if action in CLEARING_ACTIONS:
                last_refresh_ts = None
            elif action in AMBIGUOUS_KEEP_ACTIONS and age_ms is not None and age_ms > INTERIM_GHOST_TTL_MS:
                # keep_interim fired while the on-screen interim was
                # already older than the watchdog TTL and no watchdog
                # firing intervened before this decision -- worth a look.
                anomalies.append(
                    {
                        "ts": ts,
                        "reason": "keep_interim decided on an interim already past the ghost TTL",
                        "age_ms": age_ms,
                        "final_preview": data.get("final_preview", ""),
                        "interim_preview": data.get("interim_preview", ""),
                    }
                )

    return {
        "run_dir": run_dir,
        "manifest": read_manifest(run_dir),
        "action_counts": action_counts,
        "comparisons": comparisons,
        "watchdog_fires": watchdog_fires,
        "anomalies": anomalies,
        "session_start_ts": session_start_ts,
        "session_end_ts": session_end_ts,
        "total_comparisons": sum(action_counts.values()),
    }


# --------------------------------------------------------------------
# HTML / inline-SVG rendering (no external libraries or network calls)
# --------------------------------------------------------------------

_PALETTE = {
    "clear_interim": "#2e8b57",
    "clear_interim_unrelated": "#3aa15c",
    "clear_interim_same_utterance": "#4caf78",
    "keep_interim_other_utterance": "#3b82c4",
    "keep_interim": "#d9a441",
    "no_interim": "#9aa0a6",
    "unknown": "#c0392b",
}


def _color_for(action: str) -> str:
    return _PALETTE.get(action, "#8e44ad")


def _bar_chart_svg(counts: Counter, width: int = 720, bar_h: int = 34, gap: int = 12) -> str:
    if not counts:
        return "<p><em>No [INTERIM] final comparison events found in this run.</em></p>"
    items = sorted(counts.items(), key=lambda kv: -kv[1])
    max_count = max(c for _, c in items) or 1
    label_w = 260
    chart_w = width - label_w - 60
    height = len(items) * (bar_h + gap) + gap
    rows = []
    for i, (action, count) in enumerate(items):
        y = gap + i * (bar_h + gap)
        bw = max(2, int(chart_w * count / max_count))
        color = _color_for(action)
        rows.append(
            f'<text x="0" y="{y + bar_h * 0.68:.1f}" font-size="13" '
            f'font-family="monospace" fill="#dcdcdc">{html.escape(action)}</text>'
            f'<rect x="{label_w}" y="{y}" width="{bw}" height="{bar_h}" rx="4" fill="{color}"/>'
            f'<text x="{label_w + bw + 8}" y="{y + bar_h * 0.68:.1f}" font-size="13" '
            f'font-family="monospace" fill="#dcdcdc">{count}</text>'
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'style="max-width:{width}px" role="img" aria-label="Action distribution">'
        + "".join(rows)
        + "</svg>"
    )


def _timeline_svg(
    comparisons: list[dict[str, Any]],
    watchdog_fires: list[dict[str, Any]],
    session_start: Optional[float],
    session_end: Optional[float],
    width: int = 900,
    height: int = 170,
) -> str:
    if not comparisons or session_start is None or session_end is None or session_end <= session_start:
        return "<p><em>Not enough events to draw a timeline.</em></p>"
    pad_l, pad_r, pad_t, pad_b = 50, 20, 20, 30
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    span = max(1, session_end - session_start)

    def x_of(ts: float) -> float:
        return pad_l + (ts - session_start) / span * plot_w

    dots = []
    for c in comparisons:
        x = x_of(c["ts"])
        color = _color_for(c["action"])
        r = 5 if c["action"] not in CLEARING_ACTIONS else 3.5
        title = html.escape(f"{c['action']} — final: {c['final_preview'][:60]!r}")
        dots.append(
            f'<circle cx="{x:.1f}" cy="{pad_t + plot_h * 0.35:.1f}" r="{r}" '
            f'fill="{color}" fill-opacity="0.9"><title>{title}</title></circle>'
        )
    for w in watchdog_fires:
        x = x_of(w["ts"])
        dots.append(
            f'<path d="M {x:.1f} {pad_t + plot_h * 0.75 - 7:.1f} '
            f'l 7 7 l -7 7 l -7 -7 z" fill="#ff8c00" stroke="#ffffff" stroke-width="0.5">'
            f'<title>watchdog cleared, stale_ms={w.get("stale_ms")}</title></path>'
        )
    axis = (
        f'<line x1="{pad_l}" y1="{pad_t + plot_h * 0.35:.1f}" x2="{width - pad_r}" '
        f'y2="{pad_t + plot_h * 0.35:.1f}" stroke="#555" stroke-width="1"/>'
        f'<text x="{pad_l}" y="{pad_t + plot_h * 0.35 - 12:.1f}" font-size="11" '
        f'fill="#9aa0a6" font-family="monospace">final/interim decisions</text>'
        f'<line x1="{pad_l}" y1="{pad_t + plot_h * 0.75:.1f}" x2="{width - pad_r}" '
        f'y2="{pad_t + plot_h * 0.75:.1f}" stroke="#555" stroke-width="1"/>'
        f'<text x="{pad_l}" y="{pad_t + plot_h * 0.75 - 12:.1f}" font-size="11" '
        f'fill="#9aa0a6" font-family="monospace">watchdog firings (diamonds)</text>'
        f'<text x="{pad_l}" y="{height - 6}" font-size="11" fill="#9aa0a6" '
        f'font-family="monospace">session start</text>'
        f'<text x="{width - pad_r}" y="{height - 6}" font-size="11" fill="#9aa0a6" '
        f'font-family="monospace" text-anchor="end">session end (+{span / 1000.0:.0f}s)</text>'
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px" '
        f'role="img" aria-label="Decision timeline">' + axis + "".join(dots) + "</svg>"
    )


def _legend_html() -> str:
    chips = []
    labels = {
        "clear_interim": "cleared (equal / interim ⊂ final)",
        "clear_interim_unrelated": "cleared (unrelated, no identity — ghost pattern)",
        "clear_interim_same_utterance": "cleared (unrelated text, same utterance id)",
        "keep_interim_other_utterance": "kept (different, still-live utterance)",
        "keep_interim": "kept (final ⊂ interim, or ambiguous)",
        "no_interim": "no interim was on screen",
    }
    for action, label in labels.items():
        chips.append(
            f'<span class="chip"><span class="dot" style="background:{_color_for(action)}"></span>'
            f"{html.escape(label)}</span>"
        )
    return '<div class="legend">' + "".join(chips) + "</div>"


def build_verdict(result: dict[str, Any]) -> tuple[str, str, str]:
    """Returns (level, headline, explanation). level in {"pass","warn","info"}."""
    total = result["total_comparisons"]
    if total == 0:
        return (
            "info",
            "No interim activity recorded in this run.",
            "Either the run was very short, DEBUG_DIAGNOSTICS was off, or "
            "no speech was captured. Run a longer live session and re-scan.",
        )
    if result["anomalies"]:
        n = len(result["anomalies"])
        return (
            "warn",
            f"{n} decision(s) found where the interim was already stale (past "
            f"{INTERIM_GHOST_TTL_MS}ms) but not yet cleared.",
            "This does not necessarily mean a ghost line was visible -- the "
            "watchdog runs on its own 100ms tick and may have caught it a "
            "moment later -- but it is worth checking the flagged "
            "timestamps below against what was on screen at that moment.",
        )
    watchdog_n = len(result["watchdog_fires"])
    unrelated_n = sum(
        result["action_counts"].get(a, 0)
        for a in ("clear_interim_unrelated", "clear_interim_same_utterance", "keep_interim_other_utterance")
    )
    if watchdog_n:
        return (
            "pass",
            f"No stuck/ghost interim detected. Watchdog caught {watchdog_n} "
            f"orphaned interim(s) as designed.",
            "The identity gate handled most decisions directly; the "
            "liveness watchdog backstopped the rest. This is exactly the "
            "intended two-layer behavior.",
        )
    if unrelated_n:
        return (
            "pass",
            f"No stuck/ghost interim detected. Identity gate resolved all "
            f"{unrelated_n} unrelated-text case(s) without needing the watchdog.",
            "This is the best-case outcome: the identity information was "
            "available every time it was needed.",
        )
    return (
        "pass",
        "No stuck/ghost interim detected in this run.",
        "No unrelated-text or watchdog cases occurred at all in this "
        "session -- every final lined up cleanly with its own interim. "
        "That's a valid, healthy run; it just didn't exercise the "
        "unrelated-case branches. Longer sessions with more overlapping "
        "speech will exercise them more.",
    )


def render_html(result: dict[str, Any]) -> str:
    manifest = result["manifest"]
    level, headline, explanation = build_verdict(result)
    verdict_color = {"pass": "#2e8b57", "warn": "#d9a441", "info": "#3b82c4"}[level]
    verdict_label = {"pass": "PASS", "warn": "REVIEW", "info": "INFO"}[level]

    action_table_rows = "".join(
        f"<tr><td>{html.escape(a)}</td><td style='text-align:right'>{c}</td></tr>"
        for a, c in sorted(result["action_counts"].items(), key=lambda kv: -kv[1])
    )

    watchdog_rows = "".join(
        f"<tr><td>{i + 1}</td><td>{w.get('stale_ms')}</td>"
        f"<td>{html.escape(str(w.get('text_len', '')))}</td>"
        f"<td>{html.escape(str(w.get('interim_utterance_id', '')))}</td></tr>"
        for i, w in enumerate(result["watchdog_fires"])
    ) or "<tr><td colspan='4'><em>none</em></td></tr>"

    anomaly_rows = "".join(
        f"<tr><td>{i + 1}</td><td>{a['age_ms']:.0f} ms</td>"
        f"<td>{html.escape(a['final_preview'][:80])}</td>"
        f"<td>{html.escape(a['interim_preview'][:80])}</td></tr>"
        for i, a in enumerate(result["anomalies"])
    ) or "<tr><td colspan='4'><em>none</em></td></tr>"

    duration_s = 0.0
    if result["session_start_ts"] and result["session_end_ts"]:
        duration_s = (result["session_end_ts"] - result["session_start_ts"]) / 1000.0

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Interim Ghost-Line Evidence Report</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{
    background:#111418; color:#e8eaed; margin:0; padding:32px;
    font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .sub {{ color:#9aa0a6; font-size:13px; margin-bottom:24px; }}
  .card {{
    background:#181c20; border:1px solid #2a2f36; border-radius:10px;
    padding:20px 24px; margin-bottom:22px;
  }}
  .verdict {{
    display:flex; align-items:flex-start; gap:16px; border-left:5px solid {verdict_color};
  }}
  .verdict .badge {{
    background:{verdict_color}; color:#0b0d10; font-weight:700; font-size:12px;
    padding:3px 10px; border-radius:999px; white-space:nowrap; margin-top:2px;
  }}
  .verdict h2 {{ margin:0 0 6px; font-size:17px; }}
  .verdict p {{ margin:0; color:#c7cad1; font-size:14px; line-height:1.5; }}
  h3 {{ font-size:14px; text-transform:uppercase; letter-spacing:.04em; color:#9aa0a6; margin:0 0 14px; }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; }}
  th, td {{ text-align:left; padding:6px 10px; border-bottom:1px solid #2a2f36; }}
  th {{ color:#9aa0a6; font-weight:600; }}
  .meta-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px 24px; font-size:13px; }}
  .meta-grid div span {{ display:block; color:#9aa0a6; font-size:11px; text-transform:uppercase; }}
  .legend {{ display:flex; flex-wrap:wrap; gap:10px 18px; margin-top:10px; font-size:12px; color:#c7cad1; }}
  .legend .chip {{ display:flex; align-items:center; gap:6px; }}
  .legend .dot {{ width:10px; height:10px; border-radius:50%; display:inline-block; }}
  code {{ background:#20242a; padding:1px 5px; border-radius:4px; }}
</style>
</head>
<body>
  <h1>Interim Ghost-Line Evidence Report</h1>
  <div class="sub">Fix: commit 78eb59e &middot; Run: {html.escape(result['run_dir'].name)}</div>

  <div class="card verdict">
    <span class="badge">{verdict_label}</span>
    <div>
      <h2>{html.escape(headline)}</h2>
      <p>{html.escape(explanation)}</p>
    </div>
  </div>

  <div class="card">
    <h3>Run details</h3>
    <div class="meta-grid">
      <div><span>Run type</span>{html.escape(str(manifest.get('run_type', 'unknown')))}</div>
      <div><span>Language</span>{html.escape(str(manifest.get('selected_language', 'unknown')))}</div>
      <div><span>App version</span>{html.escape(str(manifest.get('app_version', 'unknown')))}</div>
      <div><span>Run timestamp</span>{html.escape(str(manifest.get('run_timestamp', 'unknown')))}</div>
      <div><span>Session duration (observed)</span>{duration_s:.0f} s</div>
      <div><span>Total final/interim decisions</span>{result['total_comparisons']}</div>
      <div><span>Watchdog firings</span>{len(result['watchdog_fires'])}</div>
      <div><span>Flagged anomalies</span>{len(result['anomalies'])}</div>
    </div>
  </div>

  <div class="card">
    <h3>Decision distribution</h3>
    {_bar_chart_svg(result['action_counts'])}
    {_legend_html()}
  </div>

  <div class="card">
    <h3>Timeline</h3>
    {_timeline_svg(result['comparisons'], result['watchdog_fires'], result['session_start_ts'], result['session_end_ts'])}
    <p style="color:#9aa0a6;font-size:12px;margin-top:8px">Hover a marker for details. Small dots = cleared, larger dots = kept, orange diamonds = watchdog firings.</p>
  </div>

  <div class="card">
    <h3>Action counts (raw)</h3>
    <table><tr><th>Action</th><th style="text-align:right">Count</th></tr>{action_table_rows}</table>
  </div>

  <div class="card">
    <h3>Watchdog firings</h3>
    <table><tr><th>#</th><th>Stale for (ms)</th><th>Text length</th><th>Utterance id</th></tr>{watchdog_rows}</table>
  </div>

  <div class="card">
    <h3>Flagged anomalies</h3>
    <table><tr><th>#</th><th>Age at decision</th><th>Final preview</th><th>Interim preview</th></tr>{anomaly_rows}</table>
  </div>
</body>
</html>
"""


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        run_dir = Path(argv[1]).resolve()
    else:
        run_dir = find_latest_run()
        if run_dir is None:
            print("No run folder with logs/async_debug.log found under troubleshooting/runs/.")
            return 2
        print(f"No run folder given -- using most recent: {run_dir}")

    log_path = run_dir / "logs" / "async_debug.log"
    if not log_path.exists():
        print(f"Not found: {log_path}")
        return 2

    result = scan(run_dir)
    out_path = run_dir / "interim_ghost_report.html"
    out_path.write_text(render_html(result), encoding="utf-8")

    level, headline, _ = build_verdict(result)
    print(f"Scanned {result['total_comparisons']} decision(s), "
          f"{len(result['watchdog_fires'])} watchdog firing(s), "
          f"{len(result['anomalies'])} flagged anomaly(ies).")
    print(f"Verdict: {level.upper()} — {headline}")
    print(f"Report written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

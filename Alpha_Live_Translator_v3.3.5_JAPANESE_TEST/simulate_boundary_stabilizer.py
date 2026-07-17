"""Offline boundary stabilizer simulation (8.5.24)."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from alpha.constants import APP_VERSION
from alpha.transcription.japanese_boundary_stabilizer import (
    JapaneseBoundaryStabilizer,
    duplicate_continuation_ratio,
    is_leading_fragment_line,
)


def _load_lines(path: Path) -> list[str]:
  text = path.read_text(encoding="utf-8")
  return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _count_leading_fragments(lines: list[str]) -> int:
  out = 0
  for ln in lines:
    body = re.sub(r"^\[Speaker\s+\d+\]\s*", "", ln.strip())
    if is_leading_fragment_line(body)[0]:
      out += 1
  return out


def _count_punct_artifacts(lines: list[str]) -> int:
  return sum(1 for ln in lines if re.search(r"。、|、。|\.\.|、、", ln))


def _count_duplicates(lines: list[str]) -> int:
  total = 0
  prev = ""
  for ln in lines:
    if prev and duplicate_continuation_ratio(prev, ln) >= 0.7:
      total += 1
    prev = ln
  return total


def _estimate_translation_ready(lines: list[str]) -> float:
  if not lines:
    return 0.0
  stabilizer = JapaneseBoundaryStabilizer()
  ready = 0
  for ln in lines:
    if stabilizer._estimate_translation_ready(ln):
      ready += 1
  return round(ready / len(lines), 4)


def main() -> int:
  parser = argparse.ArgumentParser(description="Simulate Japanese boundary stabilizer")
  parser.add_argument("--latest", action="store_true")
  parser.add_argument("--input", type=str, default="")
  parser.add_argument("--reference", type=str, default="")
  args = parser.parse_args()

  input_path = Path(args.input)
  if args.latest or not input_path.exists():
    input_path = Path("troubleshooting/latest/latest_live_alpha_output.txt")
  if not input_path.exists():
    print(f"Input not found: {input_path}")
    return 1

  reference_path = args.reference or ""
  if reference_path and not Path(reference_path).exists():
    alt = Path("troubleshooting/accuracy_benchmark/reference_transcripts/test01.txt")
    reference_path = str(alt) if alt.exists() else ""

  print("BOUNDARY_SIMULATION_STARTED")
  before_lines = _load_lines(input_path)
  stabilizer = JapaneseBoundaryStabilizer()
  after_lines, decisions = stabilizer.simulate_lines(before_lines)
  metrics = stabilizer.get_metrics()

  examples = []
  bi = 0
  ai = 0
  while bi < len(before_lines) and len(examples) < 12:
    before = before_lines[bi]
    after = after_lines[ai] if ai < len(after_lines) else ""
    if before != after or (decisions[bi].get("action", "") not in ("emit_unchanged", "")):
      examples.append(
        {
          "before": before[:120],
          "after": after[:120] if after else "",
          "action": decisions[bi].get("action", "") if bi < len(decisions) else "",
          "reason": decisions[bi].get("reason", "") if bi < len(decisions) else "",
        }
      )
    bi += 1
    if after:
      ai += 1

  report = {
    "app_version": APP_VERSION,
    "input_path": str(input_path),
    "reference_path": reference_path,
    "input_line_count": len(before_lines),
    "output_line_count": len(after_lines),
    "leading_fragment_before_count": _count_leading_fragments(before_lines),
    "leading_fragment_after_count": _count_leading_fragments(after_lines),
    "duplicate_before_count": _count_duplicates(before_lines),
    "duplicate_after_count": _count_duplicates(after_lines),
    "punctuation_artifact_before_count": _count_punct_artifacts(before_lines),
    "punctuation_artifact_after_count": _count_punct_artifacts(after_lines),
    "estimated_translation_ready_before": _estimate_translation_ready(before_lines),
    "estimated_translation_ready_after": _estimate_translation_ready(after_lines),
    "stabilizer_metrics": metrics,
    "examples_before_after": examples,
    "simulated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
  }

  out_dir = Path("troubleshooting/accuracy_benchmark/boundary_simulation")
  out_dir.mkdir(parents=True, exist_ok=True)
  ts = time.strftime("%Y%m%d_%H%M%S")
  sim_alpha = out_dir / f"{ts}_simulated_alpha_output.txt"
  sim_json = out_dir / f"{ts}_boundary_simulation_report.json"
  sim_txt = out_dir / f"{ts}_boundary_simulation_report.txt"

  sim_alpha.write_text("\n".join(after_lines) + ("\n" if after_lines else ""), encoding="utf-8")
  sim_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
  sim_txt.write_text(
    "\n".join(
      [
        "BOUNDARY SIMULATION REPORT",
        f"app_version={APP_VERSION}",
        f"input_lines={report['input_line_count']}",
        f"output_lines={report['output_line_count']}",
        f"leading_fragment_before={report['leading_fragment_before_count']}",
        f"leading_fragment_after={report['leading_fragment_after_count']}",
        f"translation_ready_before={report['estimated_translation_ready_before']}",
        f"translation_ready_after={report['estimated_translation_ready_after']}",
        f"simulated_alpha={sim_alpha}",
      ]
    )
    + "\n",
    encoding="utf-8",
  )

  if reference_path:
    report["simulated_score"] = False
    report["simulated_score_note"] = "reference_provided_scoring_deferred_to_user_cli"
    sim_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

  print("BOUNDARY_SIMULATION_COMPLETED")
  print(f"simulated_alpha={sim_alpha}")
  print(f"report_json={sim_json}")
  print("BOUNDARY_SIMULATION_REPORT_WRITTEN")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())

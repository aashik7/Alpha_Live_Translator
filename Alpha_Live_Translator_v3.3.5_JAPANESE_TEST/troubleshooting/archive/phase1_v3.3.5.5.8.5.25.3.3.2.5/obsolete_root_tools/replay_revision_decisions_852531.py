"""Offline replay of safe revision decisions against preserved live evidence."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from alpha.constants import APP_VERSION
from alpha.transcription.stable_revision_decision import decide_stable_revision_action
from alpha.utils.cer_backtracking import stage_metrics_from_normalized

VALIDATION_DIR = Path("troubleshooting/validation/v3.3.5.5.8.5.25.3.1")


def _normalize_text(text: str) -> str:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[Speaker \d+\]\s*(.+)$", line)
        if m:
            line = m.group(1).strip()
        lines.append(line)
    body = "".join(lines)
    return re.sub(r"\s+", "", body)


def _latest_live_run_folder(root: Path) -> Path | None:
    runs = root / "troubleshooting" / "runs"
    if not runs.exists():
        return None
    candidates = [
        p
        for p in runs.iterdir()
        if p.is_dir() and p.name != "_pending" and (p / "RUN_MANIFEST.json").exists()
    ]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for folder in candidates:
        try:
            manifest = json.loads((folder / "RUN_MANIFEST.json").read_text(encoding="utf-8"))
            if manifest.get("run_type") == "live" and str(manifest.get("final_status", "")).startswith("completed"):
                return folder
        except Exception:
            continue
    return candidates[0] if candidates else None


def replay_events(events_path: Path) -> dict[str, Any]:
    events = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))

    active_lines: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    destructive_old = 0
    destructive_safe_rejected = 0

    for event in events:
        old_action = str(event.get("action") or "append")
        text = str(event.get("assembler_text") or "")
        update_previous = bool(event.get("update_previous"))
        previous_record = None
        if active_lines:
            prev = active_lines[-1]
            previous_record = {
                "line_id": prev.get("line_id", ""),
                "text": prev.get("text", ""),
                "source_raw_event_ids": list(prev.get("source_raw_event_ids") or []),
            }

        decision = decide_stable_revision_action(
            previous_record=previous_record,
            candidate_text=text,
            update_previous_requested=update_previous,
            candidate_raw_event_ids=list(event.get("source_raw_event_ids") or []),
            candidate_metadata=event,
        )
        safe_action = str(decision.get("action") or "append")
        prev_text = str(previous_record.get("text") if previous_record else "")
        chars_deleted = len(prev_text) if old_action == "revise_previous" and safe_action == "append" else 0
        chars_preserved = len(prev_text) if chars_deleted > 0 else 0

        if old_action == "revise_previous" and safe_action == "append":
            destructive_old += 1
            if decision.get("reason") in (
                "completed_previous_sentence_protected",
                "destructive_content_loss_prevented",
                "revision_lineage_missing",
                "revision_lineage_disjoint",
                "revision_lineage_not_proven",
                "uncertain_default_append",
            ):
                destructive_safe_rejected += 1

        comparisons.append(
            {
                "event_id": event.get("stable_stage_event_id"),
                "old_action": old_action,
                "safe_action": safe_action,
                "update_previous": update_previous,
                "decision_reason": decision.get("reason"),
                "previous_text": prev_text,
                "candidate_text": text,
                "chars_preserved": chars_preserved,
                "chars_deleted_if_old_action": len(prev_text) if old_action == "revise_previous" else 0,
                "previous_raw_event_ids": list(previous_record.get("source_raw_event_ids") if previous_record else []),
                "candidate_raw_event_ids": list(event.get("source_raw_event_ids") or []),
            }
        )

        if safe_action == "no_op":
            continue
        if safe_action == "revise_previous" and active_lines:
            active_lines[-1] = {
                "line_id": active_lines[-1].get("line_id", ""),
                "text": text,
                "source_raw_event_ids": list(event.get("source_raw_event_ids") or []),
            }
        elif safe_action == "append":
            line_id = f"replay-line-{len(active_lines) + 1:06d}"
            active_lines.append(
                {
                    "line_id": line_id,
                    "text": text,
                    "source_raw_event_ids": list(event.get("source_raw_event_ids") or []),
                }
            )

    reconstructed = "\n".join(line["text"] for line in active_lines if line.get("text"))
    return {
        "comparisons": comparisons,
        "reconstructed_transcript": reconstructed,
        "destructive_old_revision_count": destructive_old,
        "destructive_safe_rejected_count": destructive_safe_rejected,
        "active_line_count": len(active_lines),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest-live-run", action="store_true")
    parser.add_argument("--run-folder", type=str, default="")
    parser.add_argument("--reference", type=str, default="")
    args = parser.parse_args()

    project = Path(__file__).resolve().parent
    run_folder = Path(args.run_folder) if args.run_folder else _latest_live_run_folder(project)
    if run_folder is None or not run_folder.exists():
        print("No run folder found")
        return 1

    events_path = run_folder / "accuracy_stage_compare" / "stable_assembler_events.jsonl"
    if not events_path.exists():
        print(f"Missing events: {events_path}")
        return 1

    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    replay = replay_events(events_path)
    report = {
        "app_version": APP_VERSION,
        "run_folder": str(run_folder),
        "source_events": str(events_path),
        "destructive_old_revision_count": replay["destructive_old_revision_count"],
        "destructive_safe_rejected_count": replay["destructive_safe_rejected_count"],
        "comparisons": replay["comparisons"],
        "active_line_count": replay["active_line_count"],
    }

    reference_path = Path(args.reference) if args.reference else None
    if reference_path and reference_path.exists():
        ref_norm = _normalize_text(reference_path.read_text(encoding="utf-8"))
        hyp_norm = _normalize_text(replay["reconstructed_transcript"])
        old_stable_path = run_folder / "accuracy_stage_compare" / "stable_assembler_only.txt"
        old_norm = _normalize_text(old_stable_path.read_text(encoding="utf-8")) if old_stable_path.exists() else ""
        raw_path = run_folder / "accuracy_stage_compare" / "raw_deepgram.txt"
        raw_norm = _normalize_text(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else ""
        report["scoring"] = {
            "reconstructed": stage_metrics_from_normalized(hyp_norm, ref_norm),
            "old_stable": stage_metrics_from_normalized(old_norm, ref_norm) if old_norm else {},
            "raw_deepgram": stage_metrics_from_normalized(raw_norm, ref_norm) if raw_norm else {},
        }

    json_path = VALIDATION_DIR / "revision_replay_report.json"
    txt_path = VALIDATION_DIR / "revision_replay_report.txt"
    recon_path = VALIDATION_DIR / "reconstructed_safe_transcript.txt"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    recon_path.write_text(replay["reconstructed_transcript"], encoding="utf-8")

    lines = [
        "REVISION REPLAY REPORT",
        f"Run: {run_folder.name}",
        f"Destructive old revisions: {replay['destructive_old_revision_count']}",
        f"Rejected to append: {replay['destructive_safe_rejected_count']}",
        "",
    ]
    for item in replay["comparisons"]:
        if item["old_action"] == "revise_previous":
            lines.append(
                f"{item['event_id']}: {item['old_action']} -> {item['safe_action']} ({item['decision_reason']})"
            )
    if report.get("scoring"):
        lines.extend(
            [
                "",
                f"Reconstructed accuracy: {report['scoring']['reconstructed'].get('accuracy_percent')}%",
                f"Old stable accuracy: {report['scoring'].get('old_stable', {}).get('accuracy_percent')}%",
                f"Raw Deepgram accuracy: {report['scoring'].get('raw_deepgram', {}).get('accuracy_percent')}%",
            ]
        )
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {json_path}")
    print(f"Wrote {txt_path}")
    print(f"Wrote {recon_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

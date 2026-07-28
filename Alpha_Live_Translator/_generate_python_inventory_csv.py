# -*- coding: utf-8 -*-
"""Generate PYTHON_SOURCE_FILE_INVENTORY.csv for Alpha_Live_Translator."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "PYTHON_SOURCE_FILE_INVENTORY.csv"

CRITICAL: dict[str, tuple[str, str, str, str]] = {
    "main.py": (
        "Application entry point launched by the user",
        "Without it Alpha cannot start",
        "Boots the UI and live listening session",
        "Critical — deleting breaks the product completely",
    ),
    "alpha/ui/main_window.py": (
        "Main CustomTkinter application window and session lifecycle",
        "Core UI + Start/Stop/transcript/translation wiring",
        "Owns Start/Stop, transcript display, translation UI, session factory calls",
        "Critical — deleting removes the entire product UI",
    ),
    "alpha/constants.py": (
        "Global feature flags and frozen production constants",
        "Controls enabled features, labels, timeouts, and safety switches",
        "Central configuration consumed across the app",
        "Critical — deleting or wrong values break runtime behaviour",
    ),
    "alpha/config.py": (
        "Loads environment/config such as API keys and settings",
        "Required for Deepgram/DeepL credentials and runtime config",
        "Provides configuration helpers used at startup",
        "Critical — missing config prevents live STT/translation",
    ),
    "alpha/stt_settings.py": (
        "Frozen Deepgram STT model and endpointing settings",
        "Defines production Japanese/English STT parameters",
        "Keeps STT request settings consistent",
        "Critical — changing/deleting impacts recognition quality and freeze rules",
    ),
    "alpha/transcription/deepgram_client.py": (
        "Deepgram live streaming STT client and audio send path",
        "Primary speech-to-text provider integration",
        "Connects audio capture to live transcripts",
        "Critical — deleting removes live transcription",
    ),
    "alpha/transcription/canonical_transcript_ledger.py": (
        "Authoritative canonical Raw/Stable/Final transcript ledger",
        "Single source of truth for accepted Stable commits and export freeze",
        "Prevents noncanonical mutations and supports Start/Stop session isolation",
        "Critical — deleting breaks canonical transcript integrity",
    ),
    "alpha/transcription/japanese_sentence_assembler.py": (
        "Japanese Stable sentence assembler / continuity engine",
        "Builds accepted Japanese Stable sentences from Deepgram events",
        "Controls Japanese utterance boundaries and commits",
        "Critical for Japanese mode — deleting collapses JA accuracy pipeline",
    ),
    "alpha/transcription/duplicate_protection.py": (
        "Transcript display dedup and store-to-UI path",
        "Stops duplicate/progressive lines and gates English Stable to translation",
        "Protects UI transcript quality and Stable-only translation authority",
        "High — deleting causes duplicate permanent lines and translation wiring loss",
    ),
    "alpha/transcription/pipeline_commit_transaction.py": (
        "Atomic pipeline commit into the canonical ledger",
        "Ensures Stable commits are transactional and auditable",
        "Gates UI/translation on successful ledger commit",
        "Critical — deleting breaks Stable commit authority",
    ),
    "alpha/translation/translation_worker.py": (
        "Async DeepL translation queue, ordering, and UI callbacks",
        "Performs ordered JA to EN / EN to JA translation without blocking the UI",
        "Sparse sequence ordering, loading lifecycle, provider requests",
        "Critical — deleting removes live translation",
    ),
    "alpha/translation/deepl_client.py": (
        "DeepL API client wrapper",
        "Provider connection for translation requests",
        "Sends text to DeepL and returns translations",
        "Critical — deleting blocks all DeepL calls",
    ),
    "alpha/translation/language_map.py": (
        "Maps listen language to DeepL source/target codes",
        "Keeps JA to EN-US and EN to JA mapping correct",
        "Language routing for translation jobs",
        "Critical — deleting breaks bilingual translation routing",
    ),
    "alpha/audio/wasapi.py": (
        "WASAPI system-audio loopback capture",
        "Captures Teams/Zoom/YouTube system audio",
        "One half of dual-audio capture",
        "Critical for meetings — deleting loses system audio input",
    ),
    "alpha/audio/microphone.py": (
        "Microphone capture path",
        "Captures local mic speech",
        "Other half of dual-audio capture",
        "Critical — deleting loses microphone input",
    ),
    "alpha/audio/timeline_mixer.py": (
        "Mixes system + mic audio onto one PCM timeline",
        "Produces the mixed stream Deepgram consumes",
        "Echo-aware dual capture mixing",
        "Critical — deleting breaks dual-audio pipeline",
    ),
    "alpha/audio/processing.py": (
        "PCM conversion/normalisation helpers",
        "Keeps audio format compatible with Deepgram",
        "Chunk processing and format conversion",
        "High — deleting breaks audio formatting",
    ),
    "alpha/summary/transcript_store.py": (
        "In-memory transcript segment store for UI/summary",
        "Holds current session segments for display and summary",
        "Backs incremental transcript UI and export helpers",
        "High — deleting breaks store-backed UI rendering",
    ),
    "alpha/utils/session_runtime.py": (
        "Creates a fresh live-session runtime on every Start",
        "Fixes Start-Stop-Start frozen-ledger reuse",
        "Resets session ID, ledger, translation registries, interim state",
        "Critical for multi-session use without restarting Alpha",
    ),
    "alpha/utils/run_identity.py": (
        "Creates per-run IDs and folders for live evidence",
        "Isolates each listening session artifacts",
        "Prevents session A evidence mixing with session B",
        "Critical — deleting breaks run folders and session isolation",
    ),
    "alpha/utils/ui_event_bus.py": (
        "Thread-safe UI event bus drained on Tk main thread",
        "Marshals background callbacks safely into the UI",
        "Delivers translation/UI updates without freezing",
        "Critical — deleting can lose UI callback execution",
    ),
    "alpha/ui/theme.py": (
        "UI colors/theme tokens",
        "Keeps visual styling consistent",
        "Shared COLORS and styling constants",
        "Medium — deleting breaks imports/styling (app may fail to load)",
    ),
    "alpha/core/event_bus.py": (
        "In-app event bus for status/transcript/translation events",
        "Decouples producers and UI subscribers",
        "Publishes listening/transcript/translation lifecycle events",
        "High — deleting breaks event-driven UI updates",
    ),
    "alpha/core/events.py": (
        "Event type definitions for the app event bus",
        "Shared event contracts for UI and workers",
        "Defines transcript/translation/status event payloads",
        "High — deleting breaks event imports",
    ),
    "alpha/core/models.py": (
        "Shared data models used by core/UI paths",
        "Typed structures for app state/events",
        "Helps consistent data passing across modules",
        "High — deleting can break imports on live path",
    ),
    "alpha/utils/stop_finalize_worker.py": (
        "Background Stop finalisation worker",
        "Finalises Deepgram, drains translation, exports transcript",
        "Keeps Stop responsive and completes session safely",
        "Critical — deleting breaks Stop/export lifecycle",
    ),
}


def describe(rel: str) -> tuple[str, str, str, str]:
    if rel in CRITICAL:
        return CRITICAL[rel]

    name = Path(rel).name
    low = name.lower()

    if name == "__init__.py":
        pkg = Path(rel).parent.as_posix()
        return (
            f"Package marker for `{pkg}`",
            "Makes the folder an importable Python package",
            "Enables import of sibling modules",
            "Medium — deleting can break package imports",
        )

    if rel.startswith("tools/"):
        return (
            "Validation/repair/packaging tool for engineers",
            "Not required to run Alpha live; required for QA gates",
            "Runs deterministic checks or packages evidence",
            "Low for live use — High for repair/validation workflow if this tool is the gate",
        )

    if rel.startswith("tests/"):
        return (
            "Automated unit/regression test",
            "Protects against regressions; not loaded by main.py",
            "Verifies a specific behaviour",
            "Low for live runtime — Medium for engineering safety",
        )

    if rel.startswith("alpha/utils/"):
        if any(
            k in low
            for k in (
                "packag",
                "zip",
                "stage",
                "bundle",
                "upload",
                "phase1",
                "benchmark",
                "graphify",
                "wiki",
                "cleanup_executor",
            )
        ):
            return (
                "Evidence/packaging/maintenance helper",
                "Supports troubleshooting packages or audits, not core listening",
                "Builds reports/packages or maintenance checks",
                "Low-Medium — live Alpha usually still runs; packaging/audit tools break",
            )
        if any(
            k in low
            for k in (
                "session",
                "run_identity",
                "ui_event",
                "stop_finalize",
                "watchdog",
                "freeze_guard",
                "canonical",
                "live_pipeline",
                "ui_thread",
                "ui_speaker",
                "english_deepgram",
                "japanese_accuracy",
                "troubleshooting",
            )
        ):
            return (
                "Production utility used by live runtime",
                "Shared helper for runs, Stop, evidence, logging, or UI safety",
                "Supports reliability, evidence, or session safety",
                "High — likely imported on live Start/Stop path; deleting can crash or degrade live use",
            )
        return (
            "Alpha utility module",
            "Shared helper used by app or tooling",
            "Supports logging, evidence, metrics, or helpers",
            "Medium — impact depends on whether live path imports it",
        )

    if rel.startswith("alpha/transcription/"):
        return (
            "Transcription/STT pipeline support module",
            "Supports Stable assembly, cleanup, lineage, or accuracy helpers",
            "Improves transcript quality or evidence",
            "High — JA/EN accuracy or lineage may degrade or crash if imported",
        )

    if rel.startswith("alpha/translation/"):
        return (
            "Translation subsystem support module",
            "Supports DeepL worker/acceptance",
            "Helps translation correctness",
            "High — translation path may fail if imported at runtime",
        )

    if rel.startswith("alpha/audio/"):
        return (
            "Audio capture/processing module",
            "Part of dual-audio pipeline",
            "Helps capture or mix audio",
            "High — audio path impact if imported",
        )

    if rel.startswith("alpha/ui/"):
        return (
            "UI support module",
            "Supports main window/widgets",
            "UI behaviour or layout helpers",
            "High — UI import failures if deleted",
        )

    if rel.startswith("alpha/core/"):
        return (
            "Core event/model module",
            "Shared app core types/bus",
            "Supports event architecture",
            "High — core imports fail without it",
        )

    if rel.startswith("alpha/summary/"):
        return (
            "Summary/transcript store support",
            "Meeting summary or store helpers",
            "Helps summary panel/store",
            "Medium-High — summary features break",
        )

    if rel.startswith("alpha/"):
        return (
            "Alpha package module",
            "Part of the production application library",
            "Supports app features via imports",
            "Medium-High — impact depends on whether main path imports it",
        )

    # Root scripts
    if rel.startswith("_") or "patch" in low:
        return (
            "Temporary/local patch or helper script",
            "Usually not part of normal launch path",
            "One-off repair or experiment helper",
            "Low — safe to ignore for normal use unless you rely on that script",
        )

    if (
        name.startswith("run_")
        or name.startswith("validate_")
        or name.startswith("score_")
        or name.startswith("audit_")
        or name.startswith("package_")
        or name.startswith("regression_")
    ):
        return (
            "Standalone validation/run/report script",
            "Engineering/troubleshooting workflow helper",
            "Runs gates, scores evidence, or packages results",
            "Low for live Alpha — High only if you use this script for acceptance",
        )

    if "smoke" in low or "demo" in low or "experiment" in low:
        return (
            "Demo/experiment/smoke script",
            "Not required for production listening",
            "Manual experiments or smoke checks",
            "Low — deleting does not affect normal Alpha use",
        )

    return (
        "Project Python script at repository root",
        "Supporting/tooling script outside the core alpha package",
        "Automation, validation, or maintenance",
        "Low-Medium — check whether you launch it; main.py does not need most root scripts",
    )


def importance_tier(impact: str) -> str:
    impact_l = impact.lower()
    if impact_l.startswith("critical"):
        return "Critical"
    if impact_l.startswith("high"):
        return "High"
    if impact_l.startswith("medium"):
        return "Medium"
    return "Low"


def main() -> None:
    files = sorted(
        p.relative_to(ROOT).as_posix()
        for p in ROOT.rglob("*.py")
        if "__pycache__" not in p.parts and "graphify-out" not in p.parts
    )
    rows = []
    for rel in files:
        why, importance, helps, impact = describe(rel)
        rows.append(
            {
                "File_Name": rel,
                "Why_I_Need_This_File": why,
                "Importance_In_This_Project": importance,
                "What_It_Helps_For": helps,
                "How_Important_Deletion_Impact": impact,
                "Importance_Tier": importance_tier(impact),
                "Area": rel.split("/")[0] if "/" in rel else "root",
            }
        )

    order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    rows.sort(key=lambda r: (order.get(r["Importance_Tier"], 9), r["File_Name"]))

    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "File_Name",
                "Why_I_Need_This_File",
                "Importance_In_This_Project",
                "What_It_Helps_For",
                "How_Important_Deletion_Impact",
                "Importance_Tier",
                "Area",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"WROTE {OUT}")
    print(f"ROWS {len(rows)}")
    print("Tiers:", dict(Counter(r["Importance_Tier"] for r in rows)))
    print("Areas:", dict(Counter(r["Area"] for r in rows)))


if __name__ == "__main__":
    main()

"""Independent pre-live verifier for v3.3.5.5.8.5.26.4.1.1 evidence repair (85264).

Python standard library only. Does not import scorer or acceptance-builder
functions; every metric is recomputed from physical files. Exit code 0 only
when every check in scope passes.

Usage:
  python verify_multidomain_evidence_repair_85264.py --project-root . --smoke-root <dir> [--check-package]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPAIR_VERSION = "3.3.5.5.8.5.26.4.1.1"
STALE_VERSION = "3.3.5.5.8.5.26.2"
EVIDENCE_REL = Path("troubleshooting/implementation_evidence/v3.3.5.5.8.5.26.4.1.1")

AUTHORIZED_CHANGED_FILES = [
    "alpha/constants.py",
    "alpha/utils/multidomain_gate_evidence.py",
    "run_multidomain_gate_85262.py",
    "verify_multidomain_gate_85262.py",
    "run_multidomain_evidence_repair_85264.py",
    "regression_multidomain_evidence_repair_85264.py",
    "verify_multidomain_evidence_repair_85264.py",
]
EXPECTED_NEW_SOURCE_FILES: list[str] = []
AUDIO_FILE_SUFFIXES = (".wav", ".pcm", ".mp3", ".flac", ".ogg", ".m4a", ".raw")

REQUIRED_EVIDENCE_FILES = [
    "raw_deepgram.txt",
    "stable_transcript.txt",
    "final_alpha_output.txt",
    "audio_delivery_events.jsonl",
    "audio_delivery_summary.json",
    "deepgram_request_actual.json",
    "TRANSCRIPT_STAGE_LINEAGE.json",
    "STOP_EVIDENCE_RECONCILIATION.json",
    "stage_manifest.json",
    "reference_isolation_actual.json",
]

# Independent re-implementation of the documented scoring rules
# (SCORING_RULES_CONTRACT.json). Deliberately NOT imported from the scorer.
MEANING_PAIRS: list[tuple[str, str]] = [
    ("120万円", "百二十万円"),
    ("3.2%", "三点二パーセント"),
    ("午前10時", "午前十時"),
    ("5,000件", "五千件"),
    ("API", "エーピーアイ"),
    ("CSV", "シーエスブイ"),
    ("SSO", "エスエスオー"),
    ("JSON", "ジェイソン"),
    ("MFA", "エムエフエー"),
]

SECRET_KEY_SUBSTRINGS = (
    "api_key", "apikey", "authorization", "access_token",
    "auth_token", "secret", "password", "bearer",
)
SECRET_KEY_ALLOWED = ("forbidden_secret_fields_present", "secret_scan")
SECRET_VALUE_RE = re.compile(r"(?i)(?:token|bearer|authorization)\s*[=:]\s*[A-Za-z0-9_\-\.]{12,}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_text(text: str) -> str:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[Speaker \d+\]\s*(.+)$", line)
        if m:
            line = m.group(1).strip()
        lines.append(line)
    return re.sub(r"\s+", "", "".join(lines))


def apply_meaning_equivalent(text: str) -> str:
    out = text
    for a, b in MEANING_PAIRS:
        if a in out and b not in out:
            out = out.replace(a, b)
    for a, b in MEANING_PAIRS:
        if a in out:
            out = out.replace(a, b)
    return out


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def strict_cer(reference: str, hypothesis: str) -> tuple[float, float]:
    ref_norm = normalize_text(reference)
    hyp_norm = normalize_text(hypothesis)
    distance = levenshtein(ref_norm, hyp_norm)
    ref_len = max(len(ref_norm), 1)
    cer = distance / ref_len * 100.0
    return cer, max(0.0, 100.0 - cer)


def extract_numeric_entities(text: str) -> dict[str, list[str]]:
    money = re.findall(
        r"\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:円|万円|億円)|[一二三四五六七八九十百千万億兆]+円", text
    )
    percents = re.findall(r"\d+(?:\.\d+)?\s*%|パーセント", text)
    percents += re.findall(r"\d+\.\d+%", text)
    dates = re.findall(
        r"\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日|午前\d{1,2}時(?:\d{1,2}分)?|午後\d{1,2}時(?:\d{1,2}分)?",
        text,
    )
    numbers = re.findall(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:件|名|社|回|人|台)?", text)

    def _uniq(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            item = item.strip()
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out

    return {
        "numeric_entities": _uniq(numbers),
        "dates_times": _uniq(dates),
        "money_percentages": _uniq(money + percents),
    }


def term_found(term: str, hyp_norm: str) -> bool:
    term_norm = normalize_text(term)
    if not term_norm:
        return False
    if term_norm in hyp_norm:
        return True
    return apply_meaning_equivalent(term_norm) in apply_meaning_equivalent(hyp_norm)


def category_stats(terms: list[str], hyp_norm: str) -> tuple[int, int, float]:
    if not terms:
        return 0, 0, 100.0
    found = sum(1 for t in terms if term_found(t, hyp_norm))
    return len(terms), found, found / len(terms) * 100.0


def recompute_categories(reference_text: str, truth: dict[str, Any], hyp_text: str) -> dict[str, Any]:
    hyp_norm = normalize_text(hyp_text)
    extracted = extract_numeric_entities(reference_text)
    keys = {
        "participant_name": list(truth.get("participant_and_person_names") or []),
        "company_name": list(truth.get("company_names") or []),
        "it_term": list(truth.get("it_terms") or []),
        "sales_term": list(truth.get("sales_terms") or []),
        "marketing_term": list(truth.get("marketing_terms") or []),
        "general_business_term": list(truth.get("general_business_terms") or []),
        "number": list(extracted["numeric_entities"]),
        "date_time": list(extracted["dates_times"]),
        "money_percentage": list(extracted["money_percentages"]),
    }
    out: dict[str, Any] = {"categories": {}}
    total_expected = 0
    total_found = 0
    for cat, terms in keys.items():
        expected, found, acc = category_stats(terms, hyp_norm)
        matched_items = [t for t in terms if term_found(t, hyp_norm)]
        out["categories"][cat] = {
            "expected_items": terms,
            "expected_count": expected,
            "matched_items": matched_items,
            "matched_count": found,
            "missed_items": [t for t in terms if t not in matched_items],
            "unrounded_accuracy": acc,
            "reported_accuracy_percent": round(acc, 2),
            "normalization_rules_version": "mdg_meaning_equiv_v1",
        }
        total_expected += expected
        total_found += found
    name_exp = out["categories"]["participant_name"]["expected_count"] + out["categories"]["company_name"]["expected_count"]
    name_found = out["categories"]["participant_name"]["matched_count"] + out["categories"]["company_name"]["matched_count"]
    out["combined_name_accuracy_percent"] = name_found / max(name_exp, 1) * 100.0
    out["combined_critical_entity_accuracy_percent"] = (
        total_found / max(total_expected, 1) * 100.0 if total_expected else 100.0
    )
    field_map = {
        "participant_name_accuracy_percent": "participant_name",
        "company_name_accuracy_percent": "company_name",
        "dates_times_accuracy_percent": "date_time",
        "numbers_accuracy_percent": "number",
        "money_percentage_accuracy_percent": "money_percentage",
        "it_term_accuracy_percent": "it_term",
        "sales_term_accuracy_percent": "sales_term",
        "marketing_term_accuracy_percent": "marketing_term",
        "general_business_term_accuracy_percent": "general_business_term",
    }
    for field, cat in field_map.items():
        out[field] = out["categories"][cat]["unrounded_accuracy"]
    return out


def scan_for_secrets(payload: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_l = str(key).lower()
            if any(sub in key_l for sub in SECRET_KEY_SUBSTRINGS) and not any(
                key_l.startswith(a) for a in SECRET_KEY_ALLOWED
            ):
                if isinstance(value, str) and value.strip():
                    findings.append(f"secret_bearing_key:{path}.{key}")
            findings.extend(scan_for_secrets(value, f"{path}.{key}"))
    elif isinstance(payload, list):
        for idx, value in enumerate(payload):
            findings.extend(scan_for_secrets(value, f"{path}[{idx}]"))
    elif isinstance(payload, str):
        if SECRET_VALUE_RE.search(payload):
            findings.append(f"secret_token_pattern:{path}")
    return findings


class Verifier:
    def __init__(self, project_root: Path, smoke_root: Path, check_package: bool):
        self.project_root = project_root
        self.smoke_root = smoke_root
        self.check_package = check_package
        self.evidence_dir = project_root / EVIDENCE_REL
        self.checks: list[dict[str, Any]] = []
        self.tolerance = 0.01
        try:
            contract = read_json(self.evidence_dir / "SCORING_RULES_CONTRACT.json")
            self.tolerance = float(
                contract.get("mismatch_tolerance", {}).get("accuracy_percent_tolerance", 0.01)
            )
        except Exception:
            pass

    def record(self, name: str, passed: bool, detail: Any = None) -> bool:
        self.checks.append({"check": name, "passed": bool(passed), "detail": detail})
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
        return bool(passed)

    # ------------------------------------------------------------------
    def check_phase_artifacts(self) -> None:
        required = [
            "FIXED_ACCEPTANCE_CONTRACT.json",
            "SOURCE_DISCOVERY_MAP.json",
            "SOURCE_SCOPE_DECISION.json",
            "PRE_CHANGE_SOURCE_SNAPSHOT.json",
            "PRE_CHANGE_SOURCE_SNAPSHOT.json.sha256",
            "POST_CHANGE_SOURCE_SNAPSHOT.json",
            "SOURCE_CHANGE_PROOF.json",
            "SOURCE_CHANGE_PROOF.json.sha256",
            "SCORING_RULES_CONTRACT.json",
            "VERSION_CONSISTENCY.json",
            "COMPILE_CHECK.json",
            "RUNTIME_BINDING_PROOF.json",
        ]
        missing = [n for n in required if not (self.evidence_dir / n).exists()]
        self.record("evidence_artifacts_present", not missing, {"missing": missing})

        ok = True
        details = {}
        for name in ("PRE_CHANGE_SOURCE_SNAPSHOT.json", "SOURCE_CHANGE_PROOF.json"):
            sidecar = self.evidence_dir / (name + ".sha256")
            if sidecar.exists():
                recorded = sidecar.read_text(encoding="utf-8").strip().split()[0]
                actual = sha256_file(self.evidence_dir / name)
                details[name] = actual == recorded
                ok = ok and actual == recorded
        self.record("sha256_sidecars_match", ok, details)

    def check_source_scope(self) -> None:
        pre = read_json(self.evidence_dir / "PRE_CHANGE_SOURCE_SNAPSHOT.json")
        post = read_json(self.evidence_dir / "POST_CHANGE_SOURCE_SNAPSHOT.json")
        proof = read_json(self.evidence_dir / "SOURCE_CHANGE_PROOF.json")
        pre_map = {f["relative_path"].replace("\\", "/"): f["sha256"] for f in pre["files"]}

        # POST snapshot must match physical disk right now.
        post_ok = True
        for entry in post["files"]:
            path = self.project_root / entry["relative_path"]
            if not path.exists() or sha256_file(path) != entry["sha256"]:
                post_ok = False
                break
        self.record("post_change_snapshot_matches_disk", post_ok)

        # Recompute the changed set independently.
        changed = []
        for rel, digest in pre_map.items():
            path = self.project_root / rel
            current = sha256_file(path) if path.exists() else ""
            if current != digest:
                changed.append(rel)
        unauthorized = [rel for rel in changed if rel not in AUTHORIZED_CHANGED_FILES]
        self.record(
            "only_authorized_files_changed",
            not unauthorized and sorted(changed) == sorted(AUTHORIZED_CHANGED_FILES),
            {"changed": changed, "unauthorized": unauthorized},
        )
        protected_unchanged = [rel for rel in pre_map if rel not in AUTHORIZED_CHANGED_FILES]
        self.record(
            "protected_runtime_files_unchanged",
            all(rel not in changed for rel in protected_unchanged),
            None,
        )
        self.record(
            "source_change_proof_flags",
            proof.get("unauthorized_existing_changes") == []
            and proof.get("unexpected_new_source_files") == []
            and proof.get("missing_expected_changes") == []
            and proof.get("recognition_behavior_changed") is False
            and proof.get("audio_content_changed") is False
            and proof.get("transcript_content_changed") is False
            and proof.get("Stop_behavior_changed") is False
            and proof.get("UI_changed") is False
            and proof.get("source_scope_passed") is True,
            None,
        )
        new_ok = all((self.project_root / rel).exists() for rel in EXPECTED_NEW_SOURCE_FILES)
        self.record("expected_new_source_files_exist", new_ok, None)

        scope = read_json(self.evidence_dir / "SOURCE_SCOPE_DECISION.json")
        scope_ok = True
        for section in ("production_files_modified", "harness_files_modified"):
            for entry in scope.get(section, []):
                path = self.project_root / entry["file"]
                if not path.exists() or entry.get("after_sha256") != sha256_file(path):
                    scope_ok = False
        self.record("scope_decision_after_hashes_match_disk", scope_ok, None)

    def check_version_consistency(self) -> None:
        consistency = read_json(self.evidence_dir / "VERSION_CONSISTENCY.json")
        self.record(
            "version_consistency_document",
            consistency.get("all_versions_match") is True
            and consistency.get("expected_version") == REPAIR_VERSION
            and not consistency.get("stale_version_fields"),
            consistency.get("version_sources"),
        )
        constants_text = (self.project_root / "alpha" / "constants.py").read_text(encoding="utf-8")
        m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', constants_text)
        self.record(
            "application_version_source",
            bool(m) and m.group(1) == REPAIR_VERSION,
            m.group(1) if m else "",
        )
        # Version fields of generated artifacts must never be the stale version.
        stale_hits = []
        for path in self.evidence_dir.glob("*.json"):
            try:
                doc = read_json(path)
            except Exception:
                continue
            for field in ("app_version", "harness_version", "gate_version", "VERSION"):
                if isinstance(doc, dict) and doc.get(field) == STALE_VERSION:
                    stale_hits.append(f"{path.name}:{field}")
        self.record("no_stale_version_in_generated_artifacts", not stale_hits, stale_hits)

    # ------------------------------------------------------------------
    def _verify_run_evidence(self, run_folder: Path, label: str, *, expect_scored: bool) -> None:
        stage = run_folder / "accuracy_stage_compare"
        missing = [n for n in REQUIRED_EVIDENCE_FILES if not (stage / n).exists()]
        self.record(f"{label}:required_files_present", not missing, {"missing": missing})
        if missing:
            return

        # JSONL parse + derived audio counts vs summary.
        parse_errors = 0
        queued = sent = 0
        for line in (stage / "audio_delivery_events.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                parse_errors += 1
                continue
            if row.get("event") == "normalized_chunk_queued":
                queued += 1
            elif row.get("event") == "normalized_chunk_sent":
                sent += 1
        summary = read_json(stage / "audio_delivery_summary.json")
        self.record(
            f"{label}:audio_counts_recompute",
            parse_errors == 0
            and queued == int(summary.get("queued_chunk_count") or -1)
            and sent == int(summary.get("sent_chunk_count") or -1)
            and summary.get("derived_from_physical_jsonl") is True,
            {"queued": queued, "sent": sent, "parse_errors": parse_errors},
        )

        # Request secret scan.
        request = read_json(stage / "deepgram_request_actual.json")
        findings = scan_for_secrets(request)
        self.record(
            f"{label}:request_sanitized",
            not findings and request.get("sanitized") is True
            and request.get("forbidden_secret_fields_present") is False
            and str(request.get("language")) == "ja",
            findings,
        )

        # Transcript byte preservation via lineage.
        lineage = read_json(stage / "TRANSCRIPT_STAGE_LINEAGE.json")
        lineage_ok = True
        for stage_name in ("raw", "stable", "final"):
            entry = lineage.get(stage_name) or {}
            snap_rel = str(entry.get("evidence_snapshot_path") or "")
            snap_path = Path(snap_rel)
            if not snap_path.is_absolute():
                snap_path = run_folder / snap_rel
            if (
                not snap_path.exists()
                or sha256_file(snap_path) != entry.get("evidence_snapshot_sha256")
                or entry.get("content_modified_during_copy") is not False
            ):
                lineage_ok = False
        self.record(f"{label}:transcript_bytes_preserved", lineage_ok, None)

        # Stop reconciliation.
        stop = read_json(stage / "STOP_EVIDENCE_RECONCILIATION.json")
        self.record(
            f"{label}:stop_evidence_verified",
            stop.get("stop_evidence_verified") is True and not stop.get("conflicts"),
            {"status": stop.get("status")},
        )

        if expect_scored:
            gate = read_json(stage / "PRE_SCORE_EVIDENCE_GATE.json")
            decision = read_json(stage / "SCORING_DECISION.json")
            self.record(
                f"{label}:gate_passed_and_scored",
                gate.get("scoring_permitted") is True
                and decision.get("status") == "SCORED"
                and decision.get("real_benchmark_completed") is False,
                {"gate_status": gate.get("status")},
            )

    def _recompute_scores(self, run_folder: Path, reference_path: Path, truth_path: Path, label: str) -> None:
        stage = run_folder / "accuracy_stage_compare"
        strict = read_json(stage / "strict_score.json")
        reference = reference_path.read_text(encoding="utf-8")
        truth = read_json(truth_path)
        ok = True
        detail = {}
        for stage_name, filename in (
            ("raw", "raw_deepgram.txt"),
            ("stable", "stable_transcript.txt"),
            ("final", "final_alpha_output.txt"),
        ):
            hyp = (stage / filename).read_text(encoding="utf-8")
            cer, acc = strict_cer(reference, hyp)
            reported = strict.get(stage_name) or {}

            def _num(value: Any) -> float:
                return float(value) if isinstance(value, (int, float)) else float("nan")

            cer_delta = abs(cer - _num(reported.get("cer_percent")))
            acc_delta = abs(acc - _num(reported.get("accuracy_percent")))
            rounded_ok = (
                abs(round(cer, 2) - _num(reported.get("cer_percent_display"))) <= self.tolerance
                and abs(round(acc, 2) - _num(reported.get("accuracy_percent_display"))) <= self.tolerance
            )
            detail[stage_name] = {"recomputed_cer": cer, "reported_cer": reported.get("cer_percent")}
            ok = ok and cer_delta <= self.tolerance and acc_delta <= self.tolerance and rounded_ok
        # Stable->Final loss.
        _, stable_acc = strict_cer(reference, (stage / "stable_transcript.txt").read_text(encoding="utf-8"))
        _, final_acc = strict_cer(reference, (stage / "final_alpha_output.txt").read_text(encoding="utf-8"))
        loss = max(0.0, stable_acc - final_acc)
        reported_loss = strict.get("stable_to_final_loss_percent")
        loss_ok = isinstance(reported_loss, (int, float)) and abs(loss - float(reported_loss)) <= self.tolerance
        ok = ok and loss_ok
        self.record(f"{label}:strict_scores_recompute", ok, detail)

        domain = read_json(stage / "domain_category_score.json")
        recomputed = recompute_categories(reference, truth, (stage / "stable_transcript.txt").read_text(encoding="utf-8"))
        cat_ok = True
        cat_detail = {}
        for field in (
            "participant_name_accuracy_percent",
            "company_name_accuracy_percent",
            "combined_name_accuracy_percent",
            "dates_times_accuracy_percent",
            "numbers_accuracy_percent",
            "money_percentage_accuracy_percent",
            "it_term_accuracy_percent",
            "sales_term_accuracy_percent",
            "marketing_term_accuracy_percent",
            "general_business_term_accuracy_percent",
            "combined_critical_entity_accuracy_percent",
        ):
            reported_value = domain.get(field)
            delta = (
                abs(float(recomputed[field]) - float(reported_value))
                if isinstance(reported_value, (int, float))
                else float("nan")
            )
            cat_detail[field] = {"recomputed": recomputed[field], "reported": domain.get(field)}
            cat_ok = cat_ok and delta <= self.tolerance
        self.record(f"{label}:category_scores_recompute", cat_ok, cat_detail)

    # ------------------------------------------------------------------
    def check_binding_probe(self) -> None:
        proof_path = self.smoke_root / "runtime_binding_probe" / "RUNTIME_BINDING_PROOF.json"
        if not proof_path.exists():
            self.record("probe:proof_exists", False, str(proof_path))
            return
        proof = read_json(proof_path)
        self.record("probe:proof_exists", True, None)
        self.record(
            "probe:flags",
            proof.get("binding_verified") is True
            and proof.get("external_network_used") is False
            and proof.get("Alpha_UI_launched") is False
            and proof.get("benchmark_reference_opened") is False
            and proof.get("latest_pointer_unchanged") is True,
            None,
        )
        probe_root = Path(proof["probe_root"])
        hashes_ok = True
        audio_files = []
        for row in proof.get("physical_outputs") or []:
            path = probe_root / row["path"]
            if not path.exists() or sha256_file(path) != row["sha256"]:
                hashes_ok = False
            if row["path"].lower().endswith(AUDIO_FILE_SUFFIXES):
                audio_files.append(row["path"])
        self.record("probe:physical_output_hashes_recompute", hashes_ok, None)
        self.record("probe:no_audio_pcm_files", not audio_files, audio_files)

        run_folder = probe_root / "runs" / proof["probe_run_id"]
        self._verify_run_evidence(run_folder, "probe", expect_scored=True)
        self._recompute_scores(
            run_folder,
            probe_root / "probe_reference.txt",
            probe_root / "probe_truth.json",
            "probe",
        )
        bindings_ok = all(
            Path(b["source_file"]).exists()
            and sha256_file(Path(b["source_file"])) == b["source_sha256"]
            for b in proof.get("production_function_bindings") or []
        )
        self.record("probe:production_function_source_hashes", bindings_ok, None)

    # ------------------------------------------------------------------
    def check_fixtures(self) -> None:
        fixtures_root = self.smoke_root / "fixtures"
        summary_path = fixtures_root / "regression_summary.json"
        if not summary_path.exists():
            self.record("fixtures:summary_exists", False, str(summary_path))
            return
        summary = read_json(summary_path)
        self.record(
            "fixtures:summary",
            summary.get("fixture_count") == 20
            and summary.get("regression_passed") is True
            and summary.get("failed_count") == 0
            and summary.get("passed_count") == 20
            and (
                not summary.get("focused_v2641")
                or summary.get("focused_v2641", {}).get("focused_passed") is True
            ),
            {
                "fixture_count": summary.get("fixture_count"),
                "failed": summary.get("failed_count"),
                "focused": summary.get("focused_v2641"),
            },
        )

        required_files = [
            "expected_result.json", "actual_result.json", "fixture_manifest.json",
            "SHA256SUMS.txt", "command.txt", "stdout.txt", "stderr.txt",
            "exit_code.txt", "subprocess_metadata.json",
        ]
        all_ok = True
        per_fixture: dict[str, Any] = {}
        scored_fixtures: list[tuple[str, Path]] = []
        for row in summary.get("results") or []:
            name = row["fixture_name"]
            fdir = fixtures_root / name
            problems: list[str] = []
            for req in required_files:
                if not (fdir / req).exists():
                    problems.append(f"missing:{req}")
            if problems:
                per_fixture[name] = problems
                all_ok = False
                continue
            # SHA256SUMS recompute.
            sums_ok = True
            for line in (fdir / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                digest, rel = line.split(None, 1)
                target = fdir / rel.strip()
                if not target.exists() or sha256_file(target) != digest:
                    sums_ok = False
                    problems.append(f"hash_mismatch:{rel.strip()}")
                    break
            exit_code = int((fdir / "exit_code.txt").read_text(encoding="utf-8").strip())
            actual = read_json(fdir / "actual_result.json")
            manifest = read_json(fdir / "fixture_manifest.json")
            expected = read_json(fdir / "expected_result.json")
            if exit_code != 0:
                problems.append(f"exit_code:{exit_code}")
            if actual.get("matches_expected") is not True:
                problems.append("actual_does_not_match_expected")
            # Independent expected-vs-actual comparison (do not trust matches_expected).
            if actual.get("scoring_permitted") != expected.get("scoring_permitted"):
                problems.append("scoring_permitted_mismatch")
            if actual.get("status") != expected.get("status"):
                problems.append("status_mismatch")
            for item in expected.get("blocked_contains") or []:
                if item not in (actual.get("blocked_reasons") or []):
                    problems.append(f"blocked_reason_missing:{item}")
            if manifest.get("fixture_mode") is not True or manifest.get("real_benchmark_completed") is not False:
                problems.append("fixture_mode_flags")
            run_folder = fdir / manifest["run_folder"]
            decision_path = run_folder / "accuracy_stage_compare" / "SCORING_DECISION.json"
            if expected.get("scoring_permitted") is False:
                # Fail-closed: no strict score, null metrics in the decision.
                if (run_folder / "accuracy_stage_compare" / "strict_score.json").exists():
                    problems.append("strict_score_written_despite_block")
                if decision_path.exists():
                    decision = read_json(decision_path)
                    if decision.get("scoring_permitted") is not False:
                        problems.append("decision_not_fail_closed")
                    for key in (
                        "raw_cer_percent", "stable_cer_percent", "final_cer_percent",
                        "raw_accuracy_percent", "stable_accuracy_percent", "final_accuracy_percent",
                    ):
                        if decision.get(key) is not None:
                            problems.append(f"decision_metric_not_null:{key}")
                else:
                    problems.append("scoring_decision_missing")
            else:
                scored_fixtures.append((name, fdir))
            if not sums_ok:
                pass  # already recorded in problems
            if problems:
                per_fixture[name] = problems
                all_ok = False
        self.record("fixtures:each_fixture_physically_verified", all_ok, per_fixture)

        for name, fdir in scored_fixtures:
            manifest = read_json(fdir / "fixture_manifest.json")
            run_folder = fdir / manifest["run_folder"]
            self._verify_run_evidence(run_folder, f"fixture:{name}", expect_scored=True)
            self._recompute_scores(run_folder, fdir / "reference.txt", fdir / "truth.json", f"fixture:{name}")

        # Fixture 16: tampered category values must genuinely differ.
        f16 = fixtures_root / "16_category_score_mismatch"
        tampered_path = f16 / "tampered_domain_category_score.json"
        detected = False
        if tampered_path.exists():
            manifest = read_json(f16 / "fixture_manifest.json")
            genuine = read_json(
                f16 / manifest["run_folder"] / "accuracy_stage_compare" / "domain_category_score.json"
            )
            tampered = read_json(tampered_path)
            detected = any(
                key.endswith("_accuracy_percent")
                and key in genuine
                and abs(float(tampered[key]) - float(genuine[key])) > self.tolerance
                for key in tampered
            )
        self.record("fixtures:16_tamper_detectable", detected, None)

        # Fixture 18: the planted secret must be detectable by an independent scan.
        f18 = fixtures_root / "18_request_secret_exposed"
        secret_detected = False
        if f18.exists():
            manifest = read_json(f18 / "fixture_manifest.json")
            request_path = (
                f18 / manifest["run_folder"] / "accuracy_stage_compare" / "deepgram_request_actual.json"
            )
            if request_path.exists():
                secret_detected = bool(scan_for_secrets(read_json(request_path)))
        self.record("fixtures:18_secret_detectable", secret_detected, None)

    # ------------------------------------------------------------------
    def check_reference_isolation(self) -> None:
        proof = read_json(self.evidence_dir / "RUNTIME_BINDING_PROOF.json")
        probe_ok = proof.get("benchmark_reference_opened") is False
        # Every scored fixture/probe strict score must reference a synthetic local file.
        local_refs = True
        for strict_path in self.smoke_root.rglob("strict_score.json"):
            doc = read_json(strict_path)
            ref = Path(str(doc.get("reference_path") or ""))
            try:
                ref.relative_to(self.smoke_root)
            except ValueError:
                local_refs = False
        self.record("reference_truth_isolated_from_runtime", probe_ok and local_refs, None)

    def check_no_packaged_audio(self) -> None:
        offenders = [
            str(p) for p in self.smoke_root.rglob("*")
            if p.is_file() and p.suffix.lower() in AUDIO_FILE_SUFFIXES
        ]
        self.record("no_audio_pcm_files_in_evidence", not offenders, offenders)

    # ------------------------------------------------------------------
    def check_inner_package(self) -> None:
        sealed_dir = self.evidence_dir / "FINAL_UPLOAD" / "sealed"
        inner = sealed_dir / f"MULTIDOMAIN_EVIDENCE_REPAIR_INNER_v{REPAIR_VERSION}.zip"
        if not inner.exists():
            self.record("package:inner_zip_exists", False, str(inner))
            return
        self.record("package:inner_zip_exists", True, None)
        recorded_sha = (sealed_dir / (inner.name + ".sha256")).read_text(encoding="utf-8").strip()
        recorded_size = int((sealed_dir / (inner.name + ".size.txt")).read_text(encoding="utf-8").strip())
        seal = read_json(sealed_dir / "SEAL.json")
        actual_sha = sha256_file(inner)
        self.record(
            "package:inner_zip_hash_and_size",
            actual_sha == recorded_sha == seal.get("inner_zip_sha256")
            and inner.stat().st_size == recorded_size == seal.get("inner_zip_size_bytes"),
            {"sha256": actual_sha, "size": inner.stat().st_size},
        )
        entries_doc = read_json(sealed_dir / (inner.name + ".entries.json"))
        actual_entries = {}
        audio_entries = []
        with zipfile.ZipFile(inner, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                with zf.open(info) as handle:
                    digest = hashlib.sha256()
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
                actual_entries[info.filename] = (info.file_size, digest.hexdigest())
                if info.filename.lower().endswith(AUDIO_FILE_SUFFIXES):
                    audio_entries.append(info.filename)
        recorded_entries = {
            e["name"]: (e["size"], e["sha256"]) for e in entries_doc.get("entries") or []
        }
        self.record("package:entry_manifest_matches", actual_entries == recorded_entries,
                    {"entry_count": len(actual_entries)})
        self.record("package:no_audio_entries", not audio_entries, audio_entries)
        required_entries = [
            "evidence/FIXED_ACCEPTANCE_CONTRACT.json",
            "evidence/SOURCE_DISCOVERY_MAP.json",
            "evidence/SOURCE_SCOPE_DECISION.json",
            "evidence/PRE_CHANGE_SOURCE_SNAPSHOT.json",
            "evidence/POST_CHANGE_SOURCE_SNAPSHOT.json",
            "evidence/SOURCE_CHANGE_PROOF.json",
            "evidence/SCORING_RULES_CONTRACT.json",
            "evidence/RUNTIME_BINDING_PROOF.json",
        ]
        missing = [n for n in required_entries if n not in actual_entries]
        fixture_entries = [n for n in actual_entries if n.startswith("smoke/fixtures/")]
        diff_entries = [n for n in actual_entries if n.startswith("evidence/diffs/")]
        verifier_entries = [n for n in actual_entries if n.startswith("smoke/verifier_run1/")]
        regression_entries = [n for n in actual_entries if n.startswith("smoke/regression_run/")]
        self.record(
            "package:required_entries_present",
            not missing and bool(fixture_entries) and bool(diff_entries)
            and bool(verifier_entries) and bool(regression_entries),
            {"missing": missing},
        )

    # ------------------------------------------------------------------
    def run(self) -> int:
        self.check_phase_artifacts()
        self.check_source_scope()
        self.check_version_consistency()
        self.check_binding_probe()
        self.check_fixtures()
        self.check_reference_isolation()
        self.check_no_packaged_audio()
        if self.check_package:
            self.check_inner_package()

        all_passed = all(c["passed"] for c in self.checks)
        payload = {
            "app_version": REPAIR_VERSION,
            "harness_version": REPAIR_VERSION,
            "verifier": "verify_multidomain_evidence_repair_85264.py",
            "stdlib_only": True,
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "project_root": str(self.project_root),
            "smoke_root": str(self.smoke_root),
            "package_checks_included": self.check_package,
            "check_count": len(self.checks),
            "passed_count": sum(1 for c in self.checks if c["passed"]),
            "failed_count": sum(1 for c in self.checks if not c["passed"]),
            "failed_checks": [c["check"] for c in self.checks if not c["passed"]],
            "checks": self.checks,
            "independent_verification_passed": all_passed,
        }
        out = self.evidence_dir / "INDEPENDENT_PRE_LIVE_VERIFICATION.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(out)
        print(
            f"independent_verification_passed={'true' if all_passed else 'false'} "
            f"({payload['passed_count']}/{payload['check_count']})"
        )
        return 0 if all_passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="85264 independent pre-live verifier (stdlib only)")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--smoke-root", required=True)
    parser.add_argument("--check-package", action="store_true")
    args = parser.parse_args(argv)
    verifier = Verifier(
        Path(args.project_root).resolve(), Path(args.smoke_root).resolve(), args.check_package
    )
    return verifier.run()


if __name__ == "__main__":
    raise SystemExit(main())

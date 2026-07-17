"""Regression: full-span financial number safety (8.5.25.2.1)."""

from __future__ import annotations

from pathlib import Path

from alpha.transcription.financial_number_safety import (
    apply_safe_financial_number_correction,
    audit_financial_text,
    detect_malformed_numeric_output,
)


def main() -> int:
    # Fixture C — unsafe substring must never produce 十11億
    input_c = "経常利益は十一億二千八百万円と、前年同期比で増益となりました。"
    out_c, dec_c = apply_safe_financial_number_correction(
        input_c,
        alias="一億二千八百万円",
        expected="11億2800万円",
        label="ordinary_income",
        context_terms=["経常利益"],
    )
    blocked_substring = "十11億" not in out_c
    no_unsafe = out_c != input_c.replace("一億二千八百万円", "11億2800万円") or dec_c is not None

    # Fixture D — malformed decimal detection
    input_d = "前年同期9。5の増益となりました。"
    malformed_d = detect_malformed_numeric_output(input_d)

    # Known bad output must be detected
    bad = "経常利益は十11億2800万円と、前年同期9。5の増益となりました。"
    audit_bad = audit_financial_text(bad)

    checks = {
        "no_ten11_oku": blocked_substring,
        "substring_blocked_or_safe": dec_c is None or dec_c.get("validation_status") != "legacy_substring",
        "malformed_decimal_detected": len(malformed_d) > 0,
        "audit_detects_ten11": audit_bad.get("malformed_numeric_output_count", 0) > 0,
    }
    failed = [k for k, ok in checks.items() if not ok]
    lines = [
        "VALIDATE_FINANCIAL_NUMBER_SAFETY_852521",
        f"Result: {'PASSED' if not failed else 'FAILED'}",
        f"fixture_c_output_preview={out_c[:80]}",
        f"fixture_d_malformed_count={len(malformed_d)}",
    ]
    if failed:
        lines.append("Failed: " + ", ".join(failed))
    out = Path("troubleshooting/validation/validate_financial_number_safety_852521_output.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

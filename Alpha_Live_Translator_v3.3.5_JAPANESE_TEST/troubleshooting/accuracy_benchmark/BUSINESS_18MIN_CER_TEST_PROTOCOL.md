# 18-Minute Japanese Business Video CER Test Protocol

## Purpose

Reliable 18-minute Japanese business video CER benchmark for Alpha Live Translator.
This protocol ensures Alpha output, reference transcript, analyzer reports, and upload package
all refer to the same audio section and the same file hashes.

## Rules

1. Use one Japanese business/meeting/explanation video.
2. Use the exact same 18-minute section in Alpha and reference.
3. Start Alpha 2–3 seconds before video speech begins.
4. Stop Alpha 2–5 seconds after the selected section ends.
5. Do not let unrelated audio play during the test.
6. Use Japanese-only or mostly Japanese business content.
7. Avoid mixed Korean/English creator content for business benchmark.
8. Create reference transcript from the same exact section.
9. Remove timestamps if they are separate lines.
10. Keep actual spoken Japanese only.
11. No markdown headings.
12. No bullet points.
13. No summary notes.
14. No cleaned or rephrased text.
15. Save reference as:
    `troubleshooting/accuracy_benchmark/reference_transcripts/business_18min_test01.txt`

## Commands After Live Run

```powershell
python reference_transcript_quality_check.py --alpha troubleshooting/latest/latest_live_alpha_output.txt --reference troubleshooting/accuracy_benchmark/reference_transcripts/business_18min_test01.txt

python analyze_alpha_vs_reference.py --latest --reference troubleshooting/accuracy_benchmark/reference_transcripts/business_18min_test01.txt

python score_latest_accuracy.py --alpha troubleshooting/latest/latest_live_alpha_output.txt --reference troubleshooting/accuracy_benchmark/reference_transcripts/business_18min_test01.txt

python package_latest_troubleshooting_run.py
```

## Optional Benchmark Manifest

```powershell
python create_benchmark_manifest.py --name business_18min_test01 --reference troubleshooting/accuracy_benchmark/reference_transcripts/business_18min_test01.txt --notes "18-minute Japanese business video"
```

## Trusted CER Requirements (8.5.23.4)

Trusted CER requires ALL of:

- `reference_quality_verdict == valid_for_cer`
- Alpha and reference SHA-256 hashes recorded in reports
- Alignment coverage passes (`unaligned_alpha_ratio <= 0.25`)
- Average section overlap >= 0.50
- No qualitative-only alignment mode
- Report set consistent in `latest_reports/LATEST_REPORT_SET_INDEX.json`

Do not use CER for product decisions if `score_should_be_used_for_decision == false`.

## Expected Uploads

- Generated upload package zip
- `troubleshooting/latest/latest_accuracy_evidence_index.json`
- `troubleshooting/accuracy_benchmark/latest_reports/LATEST_REPORT_SET_INDEX.json`
- `troubleshooting/accuracy_benchmark/latest_reports/latest_accuracy_score_report.json`
- `troubleshooting/accuracy_benchmark/latest_reports/latest_reference_quality_report.json`
- `troubleshooting/accuracy_benchmark/latest_reports/latest_alignment_report.json`
- `troubleshooting/accuracy_benchmark/latest_reports/latest_alignment_report.txt`
- `troubleshooting/accuracy_benchmark/latest_reports/latest_boundary_error_report.json`
- `troubleshooting/accuracy_benchmark/latest_reports/latest_business_term_risk_report.json`
- `troubleshooting/accuracy_benchmark/latest_reports/latest_glossary_candidates.json`
- `troubleshooting/Cursor final report.txt`

Do NOT upload WAV/audio unless specifically requested.

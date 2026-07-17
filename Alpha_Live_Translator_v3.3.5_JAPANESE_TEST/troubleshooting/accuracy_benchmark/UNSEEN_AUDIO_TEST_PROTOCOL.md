# Unseen Audio Test Protocol

## Rule

**Do not use the same lesson repeatedly as the only test.** That causes overfitting and phrase-specific rules that fail on real speech.

## Recommended test cycle

1. Pick **unseen** Japanese audio (3–8 minutes).
2. Run Alpha (Start → play → Stop → Close).
3. Save `Alpha.txt` and audio evidence (auto-exported).
4. If possible, create a reference transcript manually.
5. Run `python score_latest_accuracy.py` (if reference exists).
6. Review `visible_error_audit.json` / `.txt`.
7. **Do not** add code corrections from one sample.
8. Approve correction rules only after **multiple benchmark samples** pass `CORRECTION_RULE_APPROVAL_POLICY.md`.

## Suggested benchmark set (build over time)

| Sample type | Notes |
|-------------|-------|
| Japanese business lesson (unseen) | Different from any tuning lesson |
| Real meeting style speech | Natural pace, fillers |
| Polite customer conversation | Keigo, formal closings |
| Fast casual Japanese | Stress-test endpointing |
| Noisy laptop audio | Mic + room noise |
| Long 20–30 minute speech | Stability + retention |
| Multi-speaker meeting | Future; diarization not active yet |

## Reference transcript format

Save as:

`troubleshooting/accuracy_benchmark/reference_transcripts/<test_id>.txt`

Plain text, one utterance per line. Speaker labels optional:

```
[Speaker 1] いつもお世話になっております。
[Speaker 2] こちらこそよろしくお願いいたします。
```

## Validation after test

```powershell
python package_latest_troubleshooting_run.py
python validate_accuracy_85231.py
```

## What to look for

- `raw_mutation_count = 0`
- `punctuation_start_count = 0`
- `visible_error_count` trending down across benchmarks (not one lesson)
- CER improving on unseen samples when reference exists
- No new lesson-specific auto-corrections added without approval

# Alpha Live Translator Cleanup Dry Run

## Decision

**PASS — safe deletion may proceed.**

## Authoritative inputs

- Project root: `C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0`
- Audit package: `C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0\_cleanup_audit\ALPHA_REAUDIT_CLEANUP_PACKAGE`
- All three authoritative files were readable.
- SHA-256 checksums matched `SHA256SUMS.txt`.
- Workbook sheets: Summary, Delete Now, Delete After Validation, Optional, Keep, Large Items, Exact Duplicates
- Keep rows cross-checked: 27

## Counts and recoverable size

- Total Delete Now rows: 65
- Existing manual-delete candidates: 64
- Existing Git-prune candidates: 1
- Already-missing paths: 0
- Unsafe/unvalidated paths: 0
- Recoverable bytes: 182263775
- Recoverable MiB: 173.82

## Safety validation

- Every candidate was required to start with `Alpha_Translator V 1.0\`.
- Lexical and resolved paths were required to remain inside the project root.
- Symlinks, junctions, and other reparse points were rejected, including inside candidate folders.
- Keep-sheet entries, `_cleanup_audit`, `.env`, source/tests/configuration, and the latest run were protected.
- `.git\cursor` is the only `.git` path eligible for manual deletion.
- Stale worktree metadata is eligible only through `git worktree prune`.

## Candidate results

| Excel row | Path | Status | Type | Size bytes | Route / reason |
|---:|---|---|---|---:|---|
| 5 | `Alpha_Translator V 1.0\.cursor` | validated_delete | Folder | 1313 | Validated Delete Now entry |
| 6 | `Alpha_Translator V 1.0\.git\cursor` | validated_delete | Folder | 6122563 | Validated Delete Now entry |
| 7 | `Alpha_Translator V 1.0\troubleshooting` | validated_delete | Folder | 53586 | Validated Delete Now entry |
| 8 | `Alpha_Translator V 1.0\Alpha_Live_Translator\debug` | validated_delete | Folder | 192 | Validated Delete Now entry |
| 9 | `Alpha_Translator V 1.0\Alpha_Live_Translator\logs` | validated_delete | Folder | 11325 | Validated Delete Now entry |
| 10 | `Alpha_Translator V 1.0\Alpha_Live_Translator\docs\archive` | validated_delete | Folder | 1326 | Validated Delete Now entry |
| 11 | `Alpha_Translator V 1.0\Alpha_Live_Translator\PYTHON_SOURCE_FILE_INVENTORY.csv` | validated_delete | File | 64283 | Validated Delete Now entry |
| 12 | `Alpha_Translator V 1.0\Alpha_Live_Translator\Cursor final report.txt` | validated_delete | File | 11423 | Validated Delete Now entry |
| 13 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\LIVE_UTTERANCE_RETEST_20260728-171333.zip` | validated_delete | File | 24770016 | Validated Delete Now entry |
| 14 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\_importtime_main_window.txt` | validated_delete | File | 56426 | Validated Delete Now entry |
| 15 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\final_stabilization` | validated_delete | Folder | 0 | Validated Delete Now entry |
| 16 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\implementation_evidence` | validated_delete | Folder | 0 | Validated Delete Now entry |
| 17 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\issue12_readiness` | validated_delete | Folder | 12071 | Validated Delete Now entry |
| 18 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\live_bilingual_test_report` | validated_delete | Folder | 29952 | Validated Delete Now entry |
| 19 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\live_pipeline_repair` | validated_delete | Folder | 0 | Validated Delete Now entry |
| 20 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\live_pipeline_repair20260728T022226Z` | validated_delete | Folder | 0 | Validated Delete Now entry |
| 21 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\live_pipeline_repair20260728T022238Z` | validated_delete | Folder | 0 | Validated Delete Now entry |
| 22 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\live_pipeline_repair20260728T022254Z` | validated_delete | Folder | 8975 | Validated Delete Now entry |
| 23 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\live_pipeline_repair20260728T022338Z` | validated_delete | Folder | 8978 | Validated Delete Now entry |
| 24 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\live_pipeline_repair20260728T024536Z` | validated_delete | Folder | 7886 | Validated Delete Now entry |
| 25 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\live_pipeline_repair20260728T024626Z` | validated_delete | Folder | 7458 | Validated Delete Now entry |
| 26 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\live_pipeline_repair20260728T024638Z` | validated_delete | Folder | 7457 | Validated Delete Now entry |
| 27 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\live_pipeline_repair20260728T025437Z` | validated_delete | Folder | 5929 | Validated Delete Now entry |
| 28 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\live_translation_repair` | validated_delete | Folder | 79083 | Validated Delete Now entry |
| 29 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\live_translation_repair20260728T022349Z` | validated_delete | Folder | 57506 | Validated Delete Now entry |
| 30 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\repair_task1` | validated_delete | Folder | 22433 | Validated Delete Now entry |
| 31 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\session_lifecycle_repair20260728T035856Z` | validated_delete | Folder | 119953 | Validated Delete Now entry |
| 32 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\session_lifecycle_repair20260728T035953Z` | validated_delete | Folder | 119953 | Validated Delete Now entry |
| 33 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\session_lifecycle_repair20260728T040053Z` | validated_delete | Folder | 119953 | Validated Delete Now entry |
| 34 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\session_lifecycle_repair20260728T051242Z` | validated_delete | Folder | 119953 | Validated Delete Now entry |
| 35 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\startup_importtime_before.txt` | validated_delete | File | 19696 | Validated Delete Now entry |
| 36 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\startup_performance` | validated_delete | Folder | 82313 | Validated Delete Now entry |
| 37 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\tools` | validated_delete | Folder | 32043 | Validated Delete Now entry |
| 38 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\translation_beta` | validated_delete | Folder | 23099 | Validated Delete Now entry |
| 39 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\translation_beta_repair` | validated_delete | Folder | 260488 | Validated Delete Now entry |
| 40 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\utterance_revision_repair` | validated_delete | Folder | 120575 | Validated Delete Now entry |
| 41 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\utterance_revision_repair20260728T064242Z` | validated_delete | Folder | 28027 | Validated Delete Now entry |
| 42 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\utterance_revision_repair20260728T064335Z` | validated_delete | Folder | 28089 | Validated Delete Now entry |
| 43 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\utterance_revision_repair20260728T064538Z` | validated_delete | Folder | 27218 | Validated Delete Now entry |
| 44 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\utterance_revision_repair20260728T064649Z` | validated_delete | Folder | 27258 | Validated Delete Now entry |
| 45 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\utterance_revision_repair20260728T064748Z` | validated_delete | Folder | 27216 | Validated Delete Now entry |
| 46 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\utterance_revision_repair20260728T073715Z` | validated_delete | Folder | 27213 | Validated Delete Now entry |
| 47 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\utterance_revision_repair20260728T073759Z` | validated_delete | Folder | 27213 | Validated Delete Now entry |
| 48 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\utterance_revision_repair20260728T073932Z` | validated_delete | Folder | 27214 | Validated Delete Now entry |
| 49 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\v3.3.5.5.8.5.26.5.3-20260728-142845.zip` | validated_delete | File | 15913 | Validated Delete Now entry |
| 50 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\v3.3.5.5.8.5.26.5.3-20260728-142846.zip` | validated_delete | File | 10743994 | Validated Delete Now entry |
| 51 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\validation` | validated_delete | Folder | 249451 | Validated Delete Now entry |
| 52 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\runs\_pending` | validated_delete | Folder | 13095099 | Validated Delete Now entry |
| 53 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\runs\log.zip` | validated_delete | File | 10778691 | Validated Delete Now entry |
| 54 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\runs\v3.3.5.5.8.5.26.5.3-20260728-125856` | validated_delete | Folder | 36875 | Validated Delete Now entry |
| 55 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\runs\v3.3.5.5.8.5.26.5.3-20260728-125857` | validated_delete | Folder | 202582 | Validated Delete Now entry |
| 56 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\runs\v3.3.5.5.8.5.26.5.3-20260728-125953` | validated_delete | Folder | 95789 | Validated Delete Now entry |
| 57 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\runs\v3.3.5.5.8.5.26.5.3-20260728-125954` | validated_delete | Folder | 149922 | Validated Delete Now entry |
| 58 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\runs\v3.3.5.5.8.5.26.5.3-20260728-130053` | validated_delete | Folder | 172320 | Validated Delete Now entry |
| 59 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\runs\v3.3.5.5.8.5.26.5.3-20260728-130054` | validated_delete | Folder | 62411 | Validated Delete Now entry |
| 60 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\runs\v3.3.5.5.8.5.26.5.3-20260728-141243` | validated_delete | Folder | 240044 | Validated Delete Now entry |
| 61 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\runs\v3.3.5.5.8.5.26.5.3-20260728-142237` | validated_delete | Folder | 1525595 | Validated Delete Now entry |
| 62 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\runs\v3.3.5.5.8.5.26.5.3-20260728-142321` | validated_delete | Folder | 18113812 | Validated Delete Now entry |
| 63 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\runs\v3.3.5.5.8.5.26.5.3-20260728-142845` | validated_delete | Folder | 22832 | Validated Delete Now entry |
| 64 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\runs\v3.3.5.5.8.5.26.5.3-20260728-142846` | validated_delete | Folder | 29824794 | Validated Delete Now entry |
| 65 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\runs\v3.3.5.5.8.5.26.5.3-20260728-170236` | validated_delete | Folder | 4123878 | Validated Delete Now entry |
| 66 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\runs\v3.3.5.5.8.5.26.5.3-20260728-170336` | validated_delete | Folder | 14391583 | Validated Delete Now entry |
| 67 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\runs\v3.3.5.5.8.5.26.5.3-20260728-170540` | validated_delete | Folder | 33850175 | Validated Delete Now entry |
| 68 | `Alpha_Translator V 1.0\Alpha_Live_Translator\troubleshooting\runs\v3.3.5.5.8.5.26.5.3-20260731-145108` | validated_delete | Folder | 11953047 | Validated Delete Now entry |
| 69 | `Alpha_Translator V 1.0\.git\worktrees\check-folder-details-JxWVDy` | validated_git_prune | Folder | 37313 | Explicitly listed stale worktree metadata; remove only via git worktree prune |

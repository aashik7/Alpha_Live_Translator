# Current 23.4 Score Interpretation

## Summary

V3.3.5.5.8.5.23.4 produced a useful CER measurement of approximately **18.9%** on the 18-minute business lesson test (`test01.txt` reference).

## Why trusted_score was downgraded

The score was **downgraded** because V1 alignment coverage failed:

- `unaligned_alpha_ratio` was very high (~92.9% by line count)
- `extra_alpha_sections_count` was high (117 extra sections)
- `average_section_overlap_score` was low
- `alignment_integrity_verdict` = **invalid**

V1 alignment treated each Alpha line as a separate section and compared against long reference paragraphs. This is a **structural mismatch**, not necessarily a content mismatch.

## Manual review finding

Manual review suggests Alpha and reference likely cover the **same business lesson flow**. Alpha outputs many short fragmented lines while the reference uses longer paragraph-style lines.

## What 23.4.1 changes

V3.3.5.5.8.5.23.4.1 introduces **paragraph/sliding-window alignment V2**:

- Character-window overlap comparison
- Sliding windows over Alpha lines (3/5/8/12-line windows)
- Paragraph-aware reference chunking (250–400 Japanese characters)
- Monotonic sequence matching
- Char coverage used for trust decisions instead of line count alone

## Guidance

- **Do not** treat the 23.4 score as final product accuracy until V2 alignment confirms trust.
- **Do not** add correction rules from the 23.4 score alone.
- Next accuracy work remains **Japanese Boundary Stabilizer (V3.3.5.5.8.5.24)** after trust is confirmed.

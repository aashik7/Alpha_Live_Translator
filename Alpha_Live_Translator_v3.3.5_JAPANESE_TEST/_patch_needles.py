# -*- coding: utf-8 -*-
from pathlib import Path

p = Path("alpha/utils/scoring_window_v265.py")
t = p.read_text(encoding="utf-8")
start = t.index("def _candidate_needles(")
end = t.index("def _completion_hits(")
new = '''def _candidate_needles(sentence: str) -> list[str]:
    """Longest-first distinctive needles derived only from the reference sentence."""
    full = _norm_compact(sentence)
    if not full:
        return []
    needles: list[str] = [full]
    clauses = [c.strip() for c in re.split(r"[。！？!?]", sentence) if c.strip()]
    for clause in clauses:
        n = _norm_compact(clause)
        if n and n not in needles:
            needles.append(n)
    for length in (min(24, len(full)), min(16, len(full)), min(12, len(full)), _MIN_NEEDLE):
        if length < _MIN_NEEDLE:
            continue
        prefix = full[:length]
        suffix = full[-length:]
        if prefix not in needles:
            needles.append(prefix)
        if suffix not in needles:
            needles.append(suffix)
    return needles


'''
# Also replace _longest_unique_needles
start2 = t.index("def _longest_unique_needles(")
end2 = t.index("def _find_unique_completion(")
new2 = '''def _longest_unique_needles(sentence: str, stream: str) -> list[str]:
    """Deterministic unique substrings using a small fixed set of windows."""
    full = _norm_compact(sentence)
    if not full or not stream:
        return []
    found: list[str] = []
    lengths = sorted({len(full), min(20, len(full)), min(14, len(full)), _MIN_NEEDLE}, reverse=True)
    for length in lengths:
        if length < _MIN_NEEDLE or length > len(full):
            continue
        mid = max(0, (len(full) - length) // 2)
        for needle in (full[:length], full[-length:], full[mid : mid + length]):
            if stream.count(needle) == 1 and needle not in found:
                found.append(needle)
                if len(found) >= 3:
                    return found
    return found


'''
t2 = t[:start] + new + t[end:start2] + new2 + t[end2:]
p.write_text(t2, encoding="utf-8")
print("patched", p.stat().st_size)

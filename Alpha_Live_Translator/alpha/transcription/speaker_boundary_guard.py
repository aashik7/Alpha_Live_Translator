"""Shared speaker-identity boundary check for Japanese transcript decision points.

fixes TASK_2C_REPORT.md: a speaker-blind revision/merge decision let two
different speakers' turns collapse into one canonical line. Every decision
point that revises, extends, or supersedes a transcript line must call
`speakers_confirmed_same` BEFORE any text-adjacency/candidate-extends-previous
logic runs, and must refuse to merge/revise when it returns False.

Fail-closed by design: if either speaker is unknown (None), the speakers are
NOT considered confirmed-same, so the caller must treat it as a boundary
(create a new line) rather than risk a wrong merge.
"""

from __future__ import annotations

from typing import Any


def speakers_confirmed_same(active_speaker: Any, candidate_speaker: Any) -> bool:
    """True only when both speakers are known and identical.

    Used as a first-class precondition, not a downstream filter: call this
    before evaluating any text-similarity/extension/duplicate heuristic, and
    skip the merge/revise path entirely when it returns False.
    """
    if active_speaker is None or candidate_speaker is None:
        return False
    return active_speaker == candidate_speaker

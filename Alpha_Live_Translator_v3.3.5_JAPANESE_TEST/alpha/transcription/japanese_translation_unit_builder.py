"""Build translation-ready Japanese units from stable commits (no DeepL yet)."""

from __future__ import annotations

import re
import time
import uuid
from typing import Any, Optional

from alpha.utils.japanese_accuracy_log import jp_accuracy_log

_MAX_UNIT_CHARS = 480
_TARGET_UNIT_CHARS = 320
_STRONG_END_RE = re.compile(r"[。！？!?]\s*$")
_SUSPICIOUS_KANTOKU = re.compile(r"な(?:と|んと)か監督")
_RIIFU_UNFIXED = "リーフが浮かんでこない"


class JapaneseTranslationUnitBuilder:
    """Groups adjacent same-speaker stable commits into translation units."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._open_unit: Optional[dict[str, Any]] = None
        self._units: list[dict[str, Any]] = []
        self._unit_seq = 0

    def _new_unit_id(self) -> str:
        self._unit_seq += 1
        return f"tu-{self._unit_seq}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _join_text(parts: list[str]) -> str:
        merged: list[str] = []
        for part in parts:
            segment = (part or "").strip()
            if not segment:
                continue
            if merged and not merged[-1].endswith(("。", "！", "？", "!", "?", "、")):
                merged.append(segment)
            else:
                merged.append(segment)
        return "".join(merged).strip()

    @staticmethod
    def _score_unit(
        text: str,
        *,
        risky_reasons: list[str],
        source_scores: list[float],
    ) -> tuple[float, bool, list[str]]:
        reasons: list[str] = []
        score = 0.82
        if source_scores:
            score = round(sum(source_scores) / len(source_scores), 2)
        if _SUSPICIOUS_KANTOKU.search(text):
            score = min(score, 0.30)
            reasons.append("suspicious_kantoku_in_unit")
        if _RIIFU_UNFIXED in text:
            score = min(score, 0.40)
            reasons.append("unfixed_riifu_in_unit")
        if text.startswith("よ私が"):
            score = min(score, 0.42)
            reasons.append("leading_yo_in_unit")
        for risky in risky_reasons:
            if risky not in reasons:
                reasons.append(risky)
        ready = score >= 0.75 and not reasons
        if ready:
            reasons.append("unit_ready_for_translation")
        return max(0.0, min(1.0, round(score, 2))), ready, reasons

    def _flush_open_unit(self, *, reason: str) -> None:
        unit = self._open_unit
        if not unit:
            return
        self._open_unit = None
        unit_text = self._join_text(list(unit.get("parts") or []))
        if not unit_text:
            return
        unit_score, unit_ready, risky_reasons = self._score_unit(
            unit_text,
            risky_reasons=list(unit.get("risky_reasons") or []),
            source_scores=list(unit.get("source_scores") or []),
        )
        record = {
            "translation_unit_id": unit["translation_unit_id"],
            "speaker": unit.get("speaker"),
            "unit_text": unit_text,
            "source_commit_count": int(unit.get("source_commit_count") or 0),
            "unit_chars": len(unit_text),
            "unit_score": unit_score,
            "unit_ready_for_translation": unit_ready,
            "risky_reasons": risky_reasons,
            "flush_reason": reason,
        }
        self._units.append(record)
        preview = unit_text if len(unit_text) <= 180 else unit_text[:180] + "…"
        jp_accuracy_log(
            "TRANSLATION_UNIT_FLUSHED",
            translation_unit_id=record["translation_unit_id"],
            speaker=record["speaker"],
            source_commit_count=record["source_commit_count"],
            unit_chars=record["unit_chars"],
            unit_score=unit_score,
            unit_ready_for_translation=unit_ready,
            risky_reasons=risky_reasons,
            unit_text_preview=preview,
            flush_reason=reason,
        )

    def ingest_stable_commit(
        self,
        *,
        text: str,
        speaker: int,
        commit_reason: str,
        translation_ready_score: float,
        ready_for_translation: bool,
        cleanup_applied: bool = False,
        risky_flags: Optional[list[str]] = None,
        stable_text_original: str = "",
    ) -> None:
        segment = (text or "").strip()
        if not segment:
            return
        risky = list(risky_flags or [])
        if not ready_for_translation:
            if "segment_not_ready" not in risky:
                risky.append("segment_not_ready")
        if _SUSPICIOUS_KANTOKU.search(segment):
            risky.append("suspicious_kantoku_segment")
        if _RIIFU_UNFIXED in (stable_text_original or segment):
            risky.append("unfixed_riifu_segment")

        open_unit = self._open_unit
        should_split = False
        if open_unit is not None:
            if int(open_unit.get("speaker") or 0) != int(speaker or 0):
                should_split = True
            elif len(self._join_text(list(open_unit.get("parts") or []) + [segment])) > _MAX_UNIT_CHARS:
                should_split = True
            elif _STRONG_END_RE.search(self._join_text(list(open_unit.get("parts") or []))):
                prev = self._join_text(list(open_unit.get("parts") or []))
                if len(prev) >= 80 and _STRONG_END_RE.search(prev):
                    should_split = True
        if should_split:
            self._flush_open_unit(reason="topic_or_size_boundary")

        if self._open_unit is None:
            unit_id = self._new_unit_id()
            self._open_unit = {
                "translation_unit_id": unit_id,
                "speaker": speaker,
                "parts": [segment],
                "source_commit_count": 1,
                "source_scores": [float(translation_ready_score)],
                "risky_reasons": risky,
                "opened_mono": time.monotonic(),
            }
            jp_accuracy_log(
                "TRANSLATION_UNIT_OPENED",
                translation_unit_id=unit_id,
                speaker=speaker,
                unit_text_preview=segment[:120],
                commit_reason=commit_reason,
            )
            return

        open_unit = self._open_unit
        open_unit["parts"].append(segment)
        open_unit["source_commit_count"] = int(open_unit.get("source_commit_count") or 0) + 1
        open_unit.setdefault("source_scores", []).append(float(translation_ready_score))
        for flag in risky:
            if flag not in open_unit.setdefault("risky_reasons", []):
                open_unit["risky_reasons"].append(flag)
        joined = self._join_text(list(open_unit.get("parts") or []))
        jp_accuracy_log(
            "TRANSLATION_UNIT_EXTENDED",
            translation_unit_id=open_unit["translation_unit_id"],
            speaker=speaker,
            source_commit_count=open_unit["source_commit_count"],
            unit_chars=len(joined),
            commit_reason=commit_reason,
            cleanup_applied=cleanup_applied,
        )
        if _STRONG_END_RE.search(joined) and len(joined) >= _TARGET_UNIT_CHARS:
            self._flush_open_unit(reason="strong_sentence_boundary")

    def flush(self, *, reason: str = "stop") -> None:
        self._flush_open_unit(reason=reason)

    def summary_counts(self) -> dict[str, Any]:
        ready_count = sum(1 for unit in self._units if unit.get("unit_ready_for_translation"))
        total = len(self._units)
        ratio = round(ready_count / total, 3) if total > 0 else 0.0
        return {
            "translation_unit_count": total,
            "ready_translation_unit_count": ready_count,
            "TRANSLATION_UNIT_READY_RATIO": ratio,
        }

    def units_preview(self, limit: int = 3) -> list[str]:
        previews: list[str] = []
        for unit in self._units[:limit]:
            text = str(unit.get("unit_text") or "")
            if len(text) > 100:
                text = text[:100] + "…"
            previews.append(text)
        return previews

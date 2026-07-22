"""Protected-path policy for conservative project cleanup (V25.3.3.2.4)."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any, Iterable, Optional

from alpha.utils.cleanup_build_identity import PATCH_VERSION

AUTHORITATIVE_RUN_REL = Path("troubleshooting") / "runs" / "v3.3.5.5.8.5.25.3.3.1-20260714-111519"
AUTHORITATIVE_REFERENCE_REL = (
    Path("troubleshooting") / "accuracy_benchmark" / "reference_transcripts" / "test01.txt"
)

_INFRA_DIR_NAMES = frozenset({".git", ".venv", "venv", "env"})
_INFRA_FILE_GLOBS = (
    ".env",
    ".env.*",
    "requirements*.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "pytest.ini",
    "mypy.ini",
    "ruff.toml",
)
_INFRA_EXT = frozenset({".yaml", ".yml", ".toml", ".ini", ".cfg"})
_USER_DIR_PREFIXES = (
    "UI Design",
    "docs",
    "documentation",
)
_USER_FILE_GLOBS = ("README*", "LICENSE*")


def _norm(rel: str) -> str:
    return rel.replace("\\", "/").lstrip("./")


def rel_of(project_root: Path, path: Path) -> str:
    try:
        return _norm(str(path.resolve().relative_to(project_root.resolve())))
    except Exception:
        return _norm(str(path))


def is_under(rel: str, prefix: str) -> bool:
    r = _norm(rel)
    p = _norm(prefix)
    return r == p or r.startswith(p.rstrip("/") + "/")


class CleanupProtectionPolicy:
    def __init__(
        self,
        project_root: Path,
        *,
        build_id: str,
        selected_source_bundle: Optional[Path] = None,
        selected_sidecar: Optional[Path] = None,
        extra_protected: Optional[Iterable[str]] = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.build_id = build_id
        self.selected_source_bundle = (
            selected_source_bundle.resolve() if selected_source_bundle else None
        )
        self.selected_sidecar = selected_sidecar.resolve() if selected_sidecar else None
        self.extra_protected = {_norm(x) for x in (extra_protected or [])}
        self.current_build_rel = _norm(
            str(
                Path("troubleshooting")
                / "project_cleanup"
                / f"v{PATCH_VERSION}"
                / "builds"
                / build_id
            )
        )
        self.authoritative_run_rel = _norm(str(AUTHORITATIVE_RUN_REL))
        self.authoritative_reference_rel = _norm(str(AUTHORITATIVE_REFERENCE_REL))

    def reasons_for(self, path: Path) -> list[str]:
        reasons: list[str] = []
        rel = rel_of(self.project_root, path)
        name = path.name
        parts = Path(rel).parts

        if rel in self.extra_protected or any(is_under(rel, p) for p in self.extra_protected):
            reasons.append("extra_protected")

        if parts and parts[0] in _INFRA_DIR_NAMES:
            reasons.append(f"infra_dir:{parts[0]}")
        if name in _INFRA_DIR_NAMES and path.is_dir():
            reasons.append(f"infra_dir:{name}")

        for glob in _INFRA_FILE_GLOBS:
            if fnmatch.fnmatch(name, glob):
                reasons.append(f"infra_file:{glob}")
                break
        if path.suffix.lower() in _INFRA_EXT:
            reasons.append(f"infra_ext:{path.suffix.lower()}")

        if parts and parts[0] == "alpha":
            reasons.append("active_source_alpha")
        if rel == "main.py":
            reasons.append("active_entrypoint_main")

        for prefix in _USER_DIR_PREFIXES:
            if is_under(rel, prefix):
                reasons.append(f"user_work:{prefix}")
        for glob in _USER_FILE_GLOBS:
            if fnmatch.fnmatch(name, glob):
                reasons.append(f"user_file:{glob}")

        if is_under(rel, self.authoritative_reference_rel) or rel == self.authoritative_reference_rel:
            reasons.append("authoritative_reference")
        if is_under(rel, self.authoritative_run_rel):
            reasons.append("authoritative_run")

        if self.selected_source_bundle is not None:
            try:
                if path.resolve() == self.selected_source_bundle:
                    reasons.append("selected_source_bundle")
            except Exception:
                pass
        if self.selected_sidecar is not None:
            try:
                if path.resolve() == self.selected_sidecar:
                    reasons.append("selected_source_sidecar")
            except Exception:
                pass

        if is_under(rel, self.current_build_rel):
            reasons.append("current_cleanup_build")

        # Never touch active cleanup tooling outputs at version root either
        cleanup_root = _norm(str(Path("troubleshooting") / "project_cleanup" / f"v{PATCH_VERSION}"))
        if rel == cleanup_root or is_under(rel, cleanup_root):
            # Still allow quarantine targets outside this build? Protect whole cleanup tree of this version.
            reasons.append("cleanup_version_tree")

        return sorted(set(reasons))

    def is_protected(self, path: Path) -> bool:
        return bool(self.reasons_for(path))

    def is_class_a_cache_shape(self, path: Path) -> bool:
        """Disposable cache shapes may be cleaned even under active source trees."""
        name = path.name
        if name in {
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".coverage",
            "Thumbs.db",
            ".DS_Store",
        }:
            return True
        if path.suffix.lower() in {".pyc", ".pyo"}:
            return True
        if name.endswith(".swp") or name.endswith(".swo") or name.startswith(".~"):
            return True
        return False

    def may_delete(self, path: Path) -> tuple[bool, str]:
        """Return (allowed, reason). Authoritative run/ref/env/git/venv never deletable."""
        rel = rel_of(self.project_root, path)
        hard_block_prefixes = (
            self.authoritative_run_rel,
            self.authoritative_reference_rel,
            ".git",
            ".venv",
            "venv",
            "env",
            self.current_build_rel,
        )
        for prefix in hard_block_prefixes:
            if rel == prefix or is_under(rel, prefix):
                return False, f"hard_protected:{prefix}"
        if path.name in {".env"} or path.name.startswith(".env."):
            return False, "hard_protected:env_file"
        if rel == "main.py":
            return False, "hard_protected:main"
        if self.selected_source_bundle and path.resolve() == self.selected_source_bundle:
            return False, "hard_protected:selected_source_bundle"
        if self.selected_sidecar and path.resolve() == self.selected_sidecar:
            return False, "hard_protected:selected_sidecar"
        # Class A caches under alpha/ are allowed
        if self.is_class_a_cache_shape(path):
            return True, "class_a_cache"
        if self.is_protected(path):
            return False, "protected:" + ",".join(self.reasons_for(path))
        return True, "unprotected"

    def to_report(self) -> dict[str, Any]:
        return {
            "authoritative_run": self.authoritative_run_rel,
            "authoritative_reference": self.authoritative_reference_rel,
            "current_cleanup_build": self.current_build_rel,
            "selected_source_bundle": (
                str(self.selected_source_bundle) if self.selected_source_bundle else None
            ),
            "selected_sidecar": str(self.selected_sidecar) if self.selected_sidecar else None,
            "infra_dirs": sorted(_INFRA_DIR_NAMES),
            "user_dir_prefixes": list(_USER_DIR_PREFIXES),
            "active_source": ["alpha/**", "main.py"],
            "extra_protected": sorted(self.extra_protected),
            "policy_note": (
                "Protected source/config/run paths must never be deleted. "
                "Class A cache shapes under alpha/ may be quarantined then deleted."
            ),
        }


def build_protection_policy(
    project_root: Path,
    *,
    build_id: str,
    selected_source_bundle: Optional[Path] = None,
    selected_sidecar: Optional[Path] = None,
    extra_protected: Optional[Iterable[str]] = None,
) -> CleanupProtectionPolicy:
    return CleanupProtectionPolicy(
        project_root,
        build_id=build_id,
        selected_source_bundle=selected_source_bundle,
        selected_sidecar=selected_sidecar,
        extra_protected=extra_protected,
    )

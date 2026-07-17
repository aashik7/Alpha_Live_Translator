"""Practical project dependency / reference analyzer for cleanup safety."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))",
    re.MULTILINE,
)
_PATH_STR_RE = re.compile(
    r"""(?:Path\s*\(\s*|open\s*\(\s*|[\w_]+\s*=\s*)['"]([^'"]+\.(?:py|json|txt|yml|yaml|toml|ini|cfg|ps1|bat|cmd))['"]""",
    re.IGNORECASE,
)
_SCRIPT_CALL_RE = re.compile(
    r"""['"]((?:regression_|run_|validate_|prepare_|collect_|repair_|runtime_)[^'"]+\.py)['"]"""
)

SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
    }
)


def _iter_python_files(project_root: Path) -> list[Path]:
    out: list[Path] = []
    for p in project_root.rglob("*.py"):
        try:
            if any(part in SKIP_DIR_NAMES for part in p.parts):
                continue
        except Exception:
            continue
        out.append(p)
    return out


def _module_to_paths(project_root: Path, module: str) -> list[Path]:
    if not module or module.startswith("."):
        return []
    parts = module.split(".")
    candidates = [
        project_root.joinpath(*parts).with_suffix(".py"),
        project_root.joinpath(*parts, "__init__.py"),
    ]
    return [c for c in candidates if c.is_file()]


def analyze_project_dependencies(project_root: Path) -> dict[str, Any]:
    project_root = project_root.resolve()
    py_files = _iter_python_files(project_root)
    imports: dict[str, list[str]] = {}
    imported_by: dict[str, list[str]] = {}
    path_refs: dict[str, list[str]] = {}
    entrypoints: list[str] = []
    referenced_scripts: set[str] = set()

    root_scripts = sorted(
        p.name
        for p in project_root.glob("*.py")
        if p.name.startswith(
            (
                "regression_",
                "run_",
                "validate_",
                "prepare_",
                "collect_",
                "repair_",
                "runtime_",
                "main",
            )
        )
        or p.name == "main.py"
    )
    entrypoints.extend(root_scripts)

    for py in py_files:
        try:
            rel = py.relative_to(project_root).as_posix()
        except Exception:
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        local_imports: list[str] = []
        # Prefer AST for accuracy
        try:
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        local_imports.append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        local_imports.append(node.module)
        except Exception:
            for m in _IMPORT_RE.finditer(text):
                local_imports.append(m.group(1) or m.group(2) or "")

        resolved: list[str] = []
        for mod in local_imports:
            for target in _module_to_paths(project_root, mod):
                trel = target.relative_to(project_root).as_posix()
                resolved.append(trel)
                imported_by.setdefault(trel, []).append(rel)
        imports[rel] = sorted(set(resolved))

        refs = set(_PATH_STR_RE.findall(text)) | set(_SCRIPT_CALL_RE.findall(text))
        cleaned: list[str] = []
        for r in refs:
            rr = r.replace("\\", "/")
            cleaned.append(rr)
            referenced_scripts.add(Path(rr).name)
            path_refs.setdefault(rr, []).append(rel)
        if cleaned:
            path_refs.setdefault(rel, [])

    # Possibly unused: project .py under root/alpha not imported and not entrypoint
    all_py_rels = []
    for py in py_files:
        try:
            all_py_rels.append(py.relative_to(project_root).as_posix())
        except Exception:
            continue

    entry_set = set(entrypoints)
    for name in referenced_scripts:
        entry_set.add(name)

    possibly_unused: list[dict[str, Any]] = []
    for rel in all_py_rels:
        name = Path(rel).name
        if name in entry_set or rel in entry_set:
            continue
        if rel.startswith("alpha/") or rel.startswith("tests/"):
            # Keep alpha / tests unless clearly unused leaf
            if imported_by.get(rel):
                continue
            # Still don't mark alpha modules as safe delete — quarantine only report
            if rel.startswith("alpha/"):
                possibly_unused.append(
                    {
                        "relative_path": rel,
                        "reason": "no_static_importers_found",
                        "disposition": "protect_or_quarantine_only",
                        "confidence": "low",
                    }
                )
                continue
        if not imported_by.get(rel) and name not in referenced_scripts:
            if rel.startswith("troubleshooting/"):
                continue
            possibly_unused.append(
                {
                    "relative_path": rel,
                    "reason": "no_static_importers_or_entrypoint_refs",
                    "disposition": "quarantine_only_never_permanent_delete",
                    "confidence": "medium",
                }
            )

    graph = {
        "file_count_analyzed": len(py_files),
        "imports": imports,
        "imported_by": {k: sorted(set(v)) for k, v in imported_by.items()},
        "path_references": {k: sorted(set(v)) for k, v in path_refs.items()},
        "entrypoints": sorted(set(entrypoints)),
        "referenced_scripts": sorted(referenced_scripts),
    }
    return {
        "graph": graph,
        "possibly_unused": sorted(possibly_unused, key=lambda x: x["relative_path"]),
        "broken_import_candidates": [],
        "note": (
            "Practical scan: AST imports, Path/open string refs, regression/run entrypoints. "
            "Unused classification never authorizes permanent delete of Python source."
        ),
    }

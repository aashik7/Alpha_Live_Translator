"""Find state and threads that cannot recover from a single failure in a session.

This is the audit that produced `mitigation.md`. It is kept as a tool, not left
in a scratch directory, because step 4 of that plan turns it into a repo test and
because the finding list is not reproducible without it.

Two scans, both AST -- grep cannot answer either question:

1. ONE-WAY STATE. A `self.*` attribute or module global whose "bad" value is set
   on a live path, read from a DIFFERENT function, and cleared only in
   `__init__` / `reset*` / `start` -- or never cleared at all. That is item 94's
   shape: the assembler's commit gate scoped itself to
   `_current_canonical_utterance_id`, and the only site that mints a new one sat
   below the gate's own `return`.

2. UNSUPERVISED THREAD LOOPS. A thread target whose OUTERMOST loop body cannot
   swallow an exception, so one raise ends the thread for the rest of the
   session. `crash_guard_log._writer_loop` and
   `diagnostic_test_log._writer_loop` were both confirmed dead-on-first-error
   this way, and neither can be restarted afterwards.

Two corrections are baked in, because the first drafts of both scans were wrong
in ways that mattered:

* Scan 1 originally required a clear site to exist before reporting, which
  SKIPPED every flag that is never cleared -- the worst case.
* Scan 2 originally examined every loop in the function, including inner ones, so
  a correctly guarded outer loop containing a bare inner loop was reported as a
  risk. Only the outermost loop decides whether the thread survives.

Run:  python tools/audit_unrecoverable_latches.py [--json]
Exit: 0 always -- this is a report, not a gate. Step 4 of mitigation.md adds the
      gate, with an allowlist carrying a written reason per entry.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Clearing state here is setup, not recovery: it happens between sessions, which
# is exactly the window a mid-session failure cannot reach.
RESET_FUNCS = frozenset({
    "__init__", "reset", "reset_session", "reset_for_new_run", "reset_state",
    "_reset", "clear", "start", "_start", "reset_stall_classification",
    "reset_utterance_lifecycle", "reset_japanese_sentence_assembler",
})


def source_files() -> list[Path]:
    files = [
        p for p in sorted(REPO_ROOT.glob("alpha/**/*.py"))
        if "build" not in p.parts and "_archive" not in p.parts
    ]
    main = REPO_ROOT / "main.py"
    if main.is_file():
        files.append(main)
    return files


# --------------------------------------------------------------------------
# Scan 1 - one-way state
# --------------------------------------------------------------------------

class _StateVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.fn: list[str] = []
        self.globals_declared: set[str] = set()
        self.sets: dict[tuple[str, bool], list[tuple[str, int]]] = defaultdict(list)
        self.readers: dict[str, set[str]] = defaultdict(set)

    def visit_FunctionDef(self, node):  # noqa: N802
        self.fn.append(node.name)
        self.generic_visit(node)
        self.fn.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Global(self, node):  # noqa: N802
        self.globals_declared.update(node.names)
        self.generic_visit(node)

    @staticmethod
    def _name(target) -> str | None:
        if (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                and target.value.id == "self"):
            return "self." + target.attr
        if isinstance(target, ast.Name):
            return target.id
        return None

    def _where(self) -> str:
        return self.fn[-1] if self.fn else "<module>"

    def visit_Assign(self, node):  # noqa: N802
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, bool):
            for target in node.targets:
                name = self._name(target)
                if name:
                    self.sets[(name, node.value.value)].append((self._where(), node.lineno))
        self.generic_visit(node)

    def visit_Attribute(self, node):  # noqa: N802
        name = self._name(node)
        if name and isinstance(node.ctx, ast.Load):
            self.readers[name].add(self._where())
        self.generic_visit(node)

    def visit_Name(self, node):  # noqa: N802
        if isinstance(node.ctx, ast.Load):
            self.readers[node.id].add(self._where())
        self.generic_visit(node)


def scan_one_way_state() -> list[dict]:
    found: list[dict] = []
    for path in source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        visitor = _StateVisitor()
        visitor.visit(tree)
        rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")

        for (name, value), sites in sorted(visitor.sets.items()):
            # Cross-call state only. A function local cannot latch a session.
            if not (name.startswith("self.") or name in visitor.globals_declared):
                continue
            live = [(f, ln) for f, ln in sites if f not in RESET_FUNCS]
            if not live:
                continue
            # Somebody other than the setter has to consult it, or it gates nothing.
            consulted = visitor.readers.get(name, set()) - {f for f, _ in sites}
            if not consulted:
                continue
            clears = visitor.sets.get((name, not value), [])
            if clears and any(f not in RESET_FUNCS for f, _ in clears):
                continue                      # cleared on a live path -- recoverable
            found.append({
                "file": rel,
                "name": name,
                "latched_to": value,
                "set_on_live_path": [f"{f}:{ln}" for f, ln in live],
                "cleared": [f"{f}:{ln}" for f, ln in clears] or None,
                "consulted_by": sorted(consulted)[:6],
            })
    return found


# --------------------------------------------------------------------------
# Scan 2 - unsupervised thread loops
# --------------------------------------------------------------------------

def _outermost_loops(fn: ast.AST) -> list[ast.AST]:
    """Top-level loops of the body, seeing through a wrapping Try/With/If.

    Only these decide whether the thread survives: an exception escaping one of
    them leaves the target function, and the target function returning IS the
    thread ending.
    """
    found: list[tuple[ast.AST, int]] = []

    def walk(statements, depth: int = 0) -> None:
        for statement in statements:
            if isinstance(statement, (ast.While, ast.For)):
                found.append((statement, depth))
            elif isinstance(statement, (ast.Try, ast.With, ast.If)) and depth < 3:
                walk(statement.body, depth + 1)
                for handler in getattr(statement, "handlers", []):
                    walk(handler.body, depth + 1)
                walk(getattr(statement, "orelse", []), depth + 1)

    walk(list(fn.body))
    if not found:
        return []
    top = min(depth for _, depth in found)
    return [loop for loop, depth in found if depth == top]


def _catches_everything(try_node: ast.Try) -> bool:
    for handler in try_node.handlers:
        if handler.type is None:
            return True
        names = [n.id for n in ast.walk(handler.type) if isinstance(n, ast.Name)]
        if "Exception" in names or "BaseException" in names:
            return True
    return False


def _body_swallows(loop) -> bool:
    for statement in loop.body:
        if isinstance(statement, ast.Try) and _catches_everything(statement):
            return True
        if isinstance(statement, (ast.With, ast.If)):
            for inner in statement.body:
                if isinstance(inner, ast.Try) and _catches_everything(inner):
                    return True
    return False


def scan_unsupervised_threads() -> list[dict]:
    found: list[dict] = []
    for path in source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        targets: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "Thread":
                for keyword in node.keywords:
                    if keyword.arg != "target":
                        continue
                    value = keyword.value
                    if isinstance(value, ast.Attribute):
                        targets.add(value.attr)
                    elif isinstance(value, ast.Name):
                        targets.add(value.id)

        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if fn.name not in targets:
                continue
            loops = _outermost_loops(fn)
            if not loops:
                continue          # one-shot worker: finishing is not a failure
            if all(_body_swallows(loop) for loop in loops):
                continue
            found.append({
                "file": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
                "function": fn.name,
                "loop_line": loops[0].lineno,
            })
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    state = scan_one_way_state()
    threads = scan_unsupervised_threads()

    if args.json:
        print(json.dumps({"one_way_state": state, "unsupervised_threads": threads}, indent=2))
        return

    print("=" * 72)
    print(f"ONE-WAY STATE ({len(state)}) -- set on a live path, cleared only in a reset")
    print("=" * 72)
    for row in state:
        print(f"{row['file']}")
        print(f"    {row['name']} = {row['latched_to']}")
        print(f"      set     : {', '.join(row['set_on_live_path'])}")
        print(f"      cleared : {', '.join(row['cleared']) if row['cleared'] else 'NEVER CLEARED'}")
        print(f"      read by : {', '.join(row['consulted_by'])}")
        print()

    print("=" * 72)
    print(f"UNSUPERVISED THREAD LOOPS ({len(threads)}) -- one raise ends the thread")
    print("=" * 72)
    for row in threads:
        print(f"  {row['file']}:{row['loop_line']}  in {row['function']}()")

    print()
    print("Neither list is a verdict. Read the code before calling any entry a bug --")
    print("some one-way state is correct (a one-shot startup guard), and some loops")
    print("are meant to end. See mitigation.md for what was confirmed and how.")


if __name__ == "__main__":
    main()

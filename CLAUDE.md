# Project Context

This project has known architectural bugs documented in:
- ROOT_CAUSE.md — confirmed root-cause audit
- REPAIR_PLAN.md — phased repair plan (follow task order strictly)

Active app code is in Alpha_Live_Translator/. Do not modify anything
under _archive/.

Do not spawn Explore subagents. Read only these exact files directly: [file list]. Do not search or explore the rest of the repo.

# graphify — supporting tool

graphify (`graphifyy` on PyPI) is installed and available as a codebase
knowledge-graph tool, installed into `.venv` (also present on the machine's
system Python). A git post-commit hook auto-rebuilds the graph after every
commit that touches non-`graphify-out/` files — no manual step needed to
keep it current.

Use it to navigate/understand the codebase (architecture, call graphs, file
relationships) alongside — not instead of — the CLAUDE.md file-list
restriction above:

```
"c:\Users\haquemdshafieh\Documents\Tariqul\Alpha_Translator V 1.0\.venv\Scripts\graphify.exe" query "<question>"
"c:\Users\haquemdshafieh\Documents\Tariqul\Alpha_Translator V 1.0\.venv\Scripts\graphify.exe" path "<SymbolA>" "<SymbolB>"
"c:\Users\haquemdshafieh\Documents\Tariqul\Alpha_Translator V 1.0\.venv\Scripts\graphify.exe" explain "<Symbol>"
```

Output: `graphify-out/graph.json` (GraphRAG-ready), `graph.html`
(interactive), `GRAPH_REPORT.md` (plain-language summary + community hubs).
Check `GRAPH_REPORT.md`'s "Graph Freshness" line against `git rev-parse HEAD`
if in doubt — the hook keeps them in sync automatically after every commit.
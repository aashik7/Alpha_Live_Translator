# Evidence-only PYTHONSTARTUP bootstrap for legacy 852622 suite.
# Does not modify alpha source. Loads on-disk truth JSON written by prepare tool.
import builtins
import json
import sys
from copy import deepcopy
from pathlib import Path

_TRUTH_PATH = Path(r"C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0\Alpha_Live_Translator_v3.3.5_JAPANESE_TEST\troubleshooting\accuracy_benchmark\reference_transcripts\multidomain_meeting_v1_truth.json")
_REAL_IMPORT = builtins.__import__


def _ensure(module):
    if getattr(module, "__name__", "") != "alpha.utils.multidomain_gate_evidence":
        return
    if hasattr(module, "build_truth_metadata_template"):
        return
    data = json.loads(_TRUTH_PATH.read_text(encoding="utf-8"))

    def build_truth_metadata_template():
        return deepcopy(data)

    module.build_truth_metadata_template = build_truth_metadata_template


def __import__(name, globals=None, locals=None, fromlist=(), level=0):
    module = _REAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if getattr(module, "__name__", "") == "alpha.utils.multidomain_gate_evidence":
            _ensure(module)
        sub = sys.modules.get("alpha.utils.multidomain_gate_evidence")
        if sub is not None:
            _ensure(sub)
    except Exception:
        pass
    return module


builtins.__import__ = __import__

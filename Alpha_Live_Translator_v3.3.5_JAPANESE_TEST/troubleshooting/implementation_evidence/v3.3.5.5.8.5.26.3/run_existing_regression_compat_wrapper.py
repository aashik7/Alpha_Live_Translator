from __future__ import annotations
import json
import runpy
import sys
from copy import deepcopy
from pathlib import Path
sys.path.insert(0, r'''C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0\Alpha_Live_Translator_v3.3.5_JAPANESE_TEST''')
import alpha.utils.multidomain_gate_evidence as _mge
_data = json.loads(Path(r'''C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0\Alpha_Live_Translator_v3.3.5_JAPANESE_TEST\troubleshooting\accuracy_benchmark\reference_transcripts\multidomain_meeting_v1_truth.json''').read_text(encoding='utf-8'))
_mge.build_truth_metadata_template = lambda: deepcopy(_data)
sys.argv = [
    r'''C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0\Alpha_Live_Translator_v3.3.5_JAPANESE_TEST\regression_multidomain_gate_evidence_852622.py''',
    '--project-root', r'''C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0\Alpha_Live_Translator_v3.3.5_JAPANESE_TEST''',
    '--fixture-root', r'''C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0\Alpha_Live_Translator_v3.3.5_JAPANESE_TEST\troubleshooting\smoke_tests\multidomain_gate_evidence_852622_hardfix_20260716-095745\fixtures''',
    '--results-json', r'''C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0\Alpha_Live_Translator_v3.3.5_JAPANESE_TEST\troubleshooting\implementation_evidence\v3.3.5.5.8.5.26.3\existing_regression_results.json''',
]
runpy.run_path(r'''C:\Users\islamm\Documents\Tariqul\Alpha_Translator V 1.0\Alpha_Live_Translator_v3.3.5_JAPANESE_TEST\regression_multidomain_gate_evidence_852622.py''', run_name='__main__')

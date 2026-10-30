"""Verify retained historical CI evidence without running a local benchmark."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
manifest = json.loads((ROOT / 'manifest.json').read_text())
for name, digest in manifest['files'].items():
    path = (ROOT / name).resolve()
    if not path.is_relative_to(ROOT) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('Evidence path/hash mismatch: ' + name)
sys.path.insert(0, str(ROOT / 'validator/ci'))
import evidence

summaries = []
for name in ('live-' + suite for suite in evidence.SUITES):
    folder = ROOT / 'raw' / name
    actual = evidence.summarize(folder)
    if actual != json.loads((folder / 'summary.json').read_text()) or actual['qualified']:
        raise ValueError('Historical summary/gate mismatch: ' + name)
    summaries.append(actual)
decision = json.loads((ROOT / 'raw/advantage-decision/advantage.json').read_text())
if decision['suites'] != summaries or decision['qualified'] or decision['merge_allowed']:
    raise ValueError('Final three-suite decision mismatch')
print(f"PASS {len(manifest['files'])} hashes; all three summaries and final decision agree; advantage gate false")

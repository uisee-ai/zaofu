"""Lossless report projection over already relocated, complete Plan ports."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from zf.runtime.call_result_envelope import canonical_json_bytes
from zf.runtime.plan_candidate_ports import read_plan_port_bytes
from zf.runtime.task_map import resolve_artifact_file

REFERENCE_PORTS = ('source_inventory', 'capability_matrix', 'acceptance_matrix',
                   'test_matrix', 'real_e2e_matrix', 'source_index')


def compact_report(report: dict, *, state_dir: Path, project_root: Path) -> dict:
    """Remove only exact inline duplicates; retain disagreements for review.

    The admitted producer result is untouched. This projection is persisted by
    the existing handoff writer, and consumers hydrate the original full port.
    """
    result = dict(report)
    for name in REFERENCE_PORTS:
        body, ref = report.get(name), report.get(name + '_ref')
        if not isinstance(body, dict) or not body or not isinstance(ref, str) or not ref:
            continue
        try:
            path = resolve_artifact_file(ref, project_root=project_root, state_dir=state_dir)
            # Only compact copies already owned by runtime, never author paths.
            path.resolve().relative_to(state_dir.resolve())
            value = json.loads(read_plan_port_bytes(path, name))
            if canonical_json_bytes(value) == canonical_json_bytes(body):
                result.pop(name)
        except (OSError, ValueError, TypeError):
            continue
    ports = report.get('plan_ports')
    if isinstance(ports, list):
        compacted = []
        for port in ports:
            item = dict(port) if isinstance(port, dict) else port
            if (not isinstance(item, dict) or not isinstance(item.get('body'), dict)
                    or (item.get('logical_name') not in REFERENCE_PORTS and not isinstance(item.get('ref'), str))):
                compacted.append(item)
                continue
            name = str(item.get('logical_name') or '')
            # Some producer adapters emit ports[].body plus artifact_refs,
            # without the scalar reference. Exact contents, not the filename,
            # establish equivalence; filenames only bound candidate discovery.
            refs = [item.get('ref'), report.get(name + '_ref')]
            basename = Path(item['ref']).name if item.get('ref') else name + '.json'
            refs.extend(ref for ref in report.get('artifact_refs', []) if isinstance(ref, str)
                        and Path(ref).name == basename)
            for ref in dict.fromkeys(ref for ref in refs if isinstance(ref, str) and ref):
                try:
                    path = resolve_artifact_file(ref, project_root=project_root, state_dir=state_dir)
                    path.resolve().relative_to(state_dir.resolve())
                    raw = read_plan_port_bytes(path, name)
                    digest = hashlib.sha256(raw).hexdigest()
                    if item.get('sha256') and item['sha256'] != digest:
                        continue
                    if canonical_json_bytes(json.loads(raw)) != canonical_json_bytes(item['body']):
                        continue
                    item = {key: value for key, value in item.items() if key != 'body'}
                    item.update(ref=ref, sha256=digest)
                    break
                except (OSError, ValueError, TypeError):
                    continue
            compacted.append(item)
        result['plan_ports'] = compacted
    return result

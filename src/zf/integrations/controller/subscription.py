"""Extract only recent numeric Codex windows for an already observed native execution."""

from datetime import datetime
import json
from pathlib import Path
import re

_TAIL = 256 * 1024


def load_windows(service, provider, session_id, project_root, snapshot):
    if provider != 'codex' or snapshot.get('status') != 'observed' or not re.fullmatch(r'[a-zA-Z0-9_-]{1,160}', session_id or ''):
        return {}
    # Some native counter sources have no file-backed rate limit observations.
    root = getattr(service, 'codex_sessions_root', None)
    if root is None:
        return {}
    try:
        root = Path(root).resolve()
        paths = [p for p in root.glob(f'*/*/*/rollout-*-{session_id}.jsonl') if root in p.resolve().parents]
        if len(paths) != 1:
            return {}
        with paths[0].open('rb') as stream:
            header = json.loads(stream.readline(65536))
            meta = header.get('payload') or {}
            if (header.get('type') != 'session_meta' or meta.get('id') != session_id or not meta.get('cwd')
                    or Path(meta['cwd']).resolve() != Path(project_root).resolve()):
                return {}
            end = stream.seek(0, 2)
            stream.seek(max(0, end - _TAIL))
            if end > _TAIL:
                stream.readline(_TAIL)
            records = stream.read(_TAIL).splitlines()
    except (OSError, ValueError, TypeError, AttributeError):
        return {}
    windows = {}
    for line in records:
        try:
            record = json.loads(line)
            event = record.get('payload') or {}
            if record.get('type') != 'event_msg' or event.get('type') != 'token_count':
                continue
            limits = event.get('rate_limits')
            if not isinstance(limits, dict) or limits.get('limit_id') not in (None, 'codex'):
                continue
            stamp = datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00'))
            if stamp.utcoffset() is None:
                continue
            observed = stamp.timestamp()
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError):
            continue
        for name in ('primary', 'secondary'):
            data = limits.get(name)
            if not isinstance(data, dict):
                continue
            used, minutes, reset = data.get('used_percent'), data.get('window_minutes'), data.get('resets_at')
            if (type(used) not in (int, float) or not 0 <= used <= 100 or type(minutes) is not int
                    or not 1 <= minutes <= 525600 or (reset is not None and (type(reset) is not int or not 0 < reset <= 253402300799))):
                continue
            if name not in windows or observed > windows[name]['observed_at']:
                windows[name] = {'observed_at':observed, 'name':name, 'minutes':minutes,
                                 'used_percent':used, 'resets_at':reset}
    return windows

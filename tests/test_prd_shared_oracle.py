"""Calibrate product-specific oracle with a correct implementation and single defects."""
from pathlib import Path

import pytest

from tests.e2e.prd_ui.verify_shared import verify

SOURCE = '''import pathlib, sys
if len(sys.argv) != 2:
    print('usage: tool path', file=sys.stderr)
    sys.exit(2)
p = pathlib.Path(sys.argv[1])
try:
    data = p.read_bytes()
except OSError as e:
    print(f'{p}: {e.strerror}', file=sys.stderr)
    sys.exit(1)
{output}
'''


def product(root: Path):
    (root / 'read_file.py').write_text(SOURCE.replace('{output}', 'sys.stdout.buffer.write(data)'))
    (root / 'file_size.py').write_text(SOURCE.replace('{output}', 'print(len(data))'))


def test_correct_product_preserves_bytes_and_counts_bytes(tmp_path):
    product(tmp_path)
    result = verify(tmp_path)
    assert result['passed']
    assert len(result['cases']) == 22


@pytest.mark.parametrize('entry,old,new,failed_case', [
    ('file_size.py', 'print(len(data))', 'print(len(data.decode(errors="replace")))', 'bytes-3'),
    ('read_file.py', 'sys.stdout.buffer.write(data)', 'sys.stdout.buffer.write(data + b"\\n")', 'bytes-1'),
    ('read_file.py', 'data = p.read_bytes()', 'data = p.read_bytes(); p.write_bytes(b"changed")', 'bytes-0'),
    ('file_size.py', "print(f'{p}: {e.strerror}', file=sys.stderr)", "print('failed', file=sys.stderr)", 'missing'),
])
def test_single_defects_produce_specific_counterexamples(tmp_path, entry, old, new, failed_case):
    product(tmp_path)
    path = tmp_path / entry
    path.write_text(path.read_text().replace(old, new))
    result = verify(tmp_path)
    assert not result['passed']
    assert any(row['entry'] == entry and row['case'] == failed_case and not row['passed'] for row in result['cases'])

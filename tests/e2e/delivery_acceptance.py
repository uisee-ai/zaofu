#!/usr/bin/env python3
"""External black-box acceptance for frozen small-product PRDs (not pytest)."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class Product:
    def __init__(self, project: Path, output: Path):
        self.project, self.output = project, output
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.base = f'http://127.0.0.1:{self.port}'
        self.process = None
        self.requests = []
        self.log = (output / 'server.log').open('a')

    def start(self):
        env = {**os.environ, 'HOST': '0.0.0.0', 'PORT': str(self.port),
               'DATA_DIR': str(self.output / 'data'), 'EVAL_FAULTS': '1'}
        self.process = subprocess.Popen(['node', 'app/dist/server.mjs'], cwd=self.project,
                                        env=env, stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(100):
            if self.process.poll() is not None:
                raise RuntimeError('product server exited before health')
            try:
                code, body = self.request('GET', '/healthz')
                if code == 200 and body.get('ok') is True:
                    return
            except (OSError, ValueError):
                pass
            time.sleep(.1)
        raise RuntimeError('product health timeout')

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def request(self, method, path, body=None, *, key=None, role='writer', headers=None):
        h = {'Content-Type': 'application/json', 'X-Role': role, **(headers or {})}
        if key is not None:
            h['Idempotency-Key'] = key
        data = json.dumps(body).encode() if body is not None else None
        req = Request(self.base + path, method=method, data=data, headers=h)
        try:
            response = urlopen(req, timeout=8)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            value = json.loads(raw)
            status = response.status
        self.requests.append({'method': method, 'path': path, 'body': body,
                              'key': key, 'role': role, 'headers': headers,
                              'status': status, 'response': value})
        if status >= 400:
            assert value.get('error'), f'{path}: error response lacks error'
        return status, value

    def state(self):
        code, value = self.request('GET', '/api/state')
        assert code == 200
        return value


def expect(response, code):
    assert response[0] == code, f'expected {code}, observed {response}'
    return response[1]


def cargo(p):
    path = '/api/assets'
    create = {'id': 'asset-1', 'box': [0, 0, 10, 10]}
    original = expect(p.request('POST', path, create, key='create'), 201)
    assert original['revision'] == original['embedding_revision'] == 1
    assert expect(p.request('POST', path, create, key='create'), 201) == original
    expect(p.request('POST', path, create, key='duplicate'), 409)
    expect(p.request('POST', path, create, key='create', role='reader'), 403)
    assert p.state()['assets'] == [original]
    edit = {'expected_revision': 1, 'box': [1, 1, 20, 20]}
    before = p.state()
    for bad in [{}, {'id': 'bad', 'box': []}, {'id': 'bad', 'box': [0, 0, '10', 10]}]:
        expect(p.request('POST', path, bad, key='invalid-shape'), 400)
    expect(p.request('POST', path + '/missing/edit', edit, key='missing'), 404)
    expect(p.request('POST', path + '/asset-1/edit', edit, key='unknown-role', role='unknown'), 403)
    assert p.state() == before
    expect(p.request('POST', path + '/asset-1/edit', edit, key='edit', headers={'X-Eval-Fault': 'before-commit'}), 503)
    assert p.state() == before
    assert len(expect(p.request('GET', '/api/search'), 200)['assets']) == 1
    changed = expect(p.request('POST', path + '/asset-1/edit', edit, key='edit'), 200)
    assert changed['revision'] == 2 and changed['embedding_revision'] is None
    assert expect(p.request('GET', '/api/search'), 200)['assets'] == []
    assert expect(p.request('POST', path + '/asset-1/edit', edit, key='edit'), 200) == changed
    expect(p.request('POST', path + '/asset-1/edit', edit, key='stale-edit'), 409)
    expect(p.request('POST', path + '/asset-1/edit', {**edit, 'box': [3, 3, 3, 3]}, key='edit'), 409)
    expect(p.request('POST', path + '/asset-1/refresh', {'expected_revision': 1}, key='refresh-old'), 409)
    expect(p.request('POST', path + '/asset-1/refresh', {'expected_revision': 2}, key='refresh'), 200)
    assert expect(p.request('GET', '/api/search'), 200)['assets'][0]['embedding_revision'] == 2
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda i: p.request('POST', path + '/asset-1/edit', {'expected_revision': 2, 'box': [i, i, 4, 4]}, key=f'concurrent-{i}'), [5, 6]))
    assert sorted(x[0] for x in results) == [200, 409]
    assert p.state()['assets'][0]['revision'] == 3
    assert expect(p.request('GET', '/api/search'), 200)['assets'] == []
    before = p.state()
    expect(p.request('POST', path, {'id': 'bad', 'box': [0, 0, -1, 1]}, key='bad'), 400)
    assert p.state() == before
    p.stop(); p.start()
    assert p.state() == before
    assert expect(p.request('POST', path + '/asset-1/edit', edit, key='edit'), 200) == changed
    assert p.state() == before
    return ['C01', 'C02', 'C03', 'C04', 'C05', 'C06', 'C07']


def warehouse(p):
    expect(p.request('POST', '/api/stock', {'warehouse': 'A', 'sku': 'S', 'quantity': 10}, key='seed'), 201)
    transfer = {'from': 'A', 'to': 'B', 'sku': 'S', 'quantity': 6}
    permissions = {'X-Warehouse-Out': 'A', 'X-Warehouse-In': 'B'}
    before = p.state()
    expect(p.request('POST', '/api/stock', {'warehouse': 'A', 'sku': 'S', 'quantity': 10}, key='seed-again'), 409)
    expect(p.request('POST', '/api/stock', {'warehouse': 'C', 'sku': 'S', 'quantity': 0}, key='seed-reader', role='reader'), 403)
    for h in ({}, {'X-Warehouse-Out': 'A'}, {'X-Warehouse-In': 'B'}):
        expect(p.request('POST', '/api/transfers', transfer, key='move', headers=h), 403)
        assert p.state() == before
    expect(p.request('POST', '/api/transfers', transfer, key='move', headers={**permissions, 'X-Eval-Fault': 'before-commit'}), 503)
    assert p.state() == before
    result = expect(p.request('POST', '/api/transfers', transfer, key='move', headers=permissions), 200)
    assert expect(p.request('POST', '/api/transfers', transfer, key='move', headers=permissions), 200) == result
    expect(p.request('POST', '/api/transfers', transfer, key='move', role='reader', headers=permissions), 403)
    expect(p.request('POST', '/api/transfers', {**transfer, 'quantity': 2}, key='move', headers=permissions), 409)
    state = p.state()
    assert {x['warehouse']: x['quantity'] for x in state['stock']} == {'A': 4, 'B': 6}
    assert len(state['transfers']) == 1
    for q in [0, -1, .5]:
        expect(p.request('POST', '/api/transfers', {**transfer, 'quantity': q}, key=f'bad-{q}', headers=permissions), 400)
    expect(p.request('POST', '/api/transfers', {**transfer, 'to': 'A'}, key='same-warehouse', headers={'X-Warehouse-Out': 'A', 'X-Warehouse-In': 'A'}), 400)
    expect(p.request('POST', '/api/transfers', {**transfer, 'quantity': 11}, key='insufficient', headers=permissions), 409)
    assert p.state() == state
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda i: p.request('POST', '/api/transfers', {**transfer, 'quantity': 3}, key=f'concurrent-{i}', headers=permissions), [1, 2]))
    assert sorted(x[0] for x in results) == [200, 409]
    state = p.state()
    assert {x['warehouse']: x['quantity'] for x in state['stock']} == {'A': 1, 'B': 9}
    assert len(state['transfers']) == 2
    p.stop(); p.start()
    assert p.state() == state
    assert expect(p.request('POST', '/api/transfers', transfer, key='move', headers=permissions), 200) == result
    assert p.state() == state
    return ['W01', 'W02', 'W03', 'W04', 'W05', 'W06']


def publishing(p):
    doc = {'id': 'doc-1', 'title': '标题', 'body': '原文'}
    expect(p.request('POST', '/api/documents', doc, key='create'), 201)
    expect(p.request('POST', '/api/documents', doc, key='duplicate'), 409)
    expect(p.request('POST', '/api/documents', {**doc, 'id': 'bad', 'title': ''}, key='invalid'), 400)
    expect(p.request('GET', '/api/public/doc-1'), 404)
    path = '/api/documents/doc-1/'
    expect(p.request('POST', path + 'publish', {'expected_revision': 1}, key='publish'), 409)
    expect(p.request('POST', path + 'approve', {'expected_revision': 1}, key='approve'), 403)
    expect(p.request('POST', path + 'approve', {'expected_revision': 1}, key='approve-reader', role='reader'), 403)
    expect(p.request('POST', path + 'approve', {'expected_revision': 1}, key='approve', role='reviewer'), 200)
    published = expect(p.request('POST', path + 'publish', {'expected_revision': 1}, key='publish'), 200)
    assert published['published_revision'] == 1
    original = expect(p.request('GET', '/api/public/doc-1'), 200)
    assert original == {**doc, 'revision': 1}
    edit = {'expected_revision': 1, 'title': '新标题', 'body': '新文'}
    before = p.state()
    expect(p.request('POST', path + 'edit', edit, key='edit', headers={'X-Eval-Fault': 'before-commit'}), 503)
    assert p.state() == before
    changed = expect(p.request('POST', path + 'edit', edit, key='edit'), 200)
    assert changed['revision'] == 2 and changed['approved_revision'] is None and changed['published_revision'] == 1
    assert expect(p.request('GET', '/api/public/doc-1'), 200) == original
    expect(p.request('POST', path + 'publish', {'expected_revision': 2}, key='publish-v2'), 409)
    expect(p.request('POST', path + 'approve', {'expected_revision': 1}, key='old-approve', role='reviewer'), 409)
    expect(p.request('POST', path + 'edit', edit, key='edit', role='reader'), 403)
    assert expect(p.request('POST', path + 'edit', edit, key='edit'), 200) == changed
    expect(p.request('POST', path + 'edit', {**edit, 'body': 'other'}, key='edit'), 409)
    expect(p.request('POST', path + 'approve', {'expected_revision': 2}, key='approve-v2', role='reviewer'), 200)
    expect(p.request('POST', path + 'publish', {'expected_revision': 2}, key='publish-v2'), 200)
    assert expect(p.request('GET', '/api/public/doc-1'), 200) == {'id': 'doc-1', 'title': '新标题', 'body': '新文', 'revision': 2}
    before = p.state()
    p.stop(); p.start()
    assert p.state() == before
    assert expect(p.request('POST', path + 'edit', edit, key='edit'), 200) == changed
    assert p.state() == before
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda i: p.request('POST', path + 'edit', {'expected_revision': 2, 'title': '并发', 'body': str(i)}, key=f'concurrent-{i}'), [1, 2]))
    assert sorted(x[0] for x in results) == [200, 409]
    assert p.state()['documents'][0]['revision'] == 3
    assert expect(p.request('GET', '/api/public/doc-1'), 200)['revision'] == 2
    return ['P01', 'P02', 'P03', 'P04', 'P05', 'P06']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--case', choices=['cargo-mini', 'warehouse', 'publishing'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--browser-image', required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    product = args.output / 'clean-product'
    report = {'target_commit': args.commit, 'case': args.case, 'checks': [], 'status': 'not_run', 'started_at': time.time()}
    server = None
    try:
        subprocess.run(['git', 'clone', '--no-hardlinks', '-q', str(args.project), str(product)], check=True)
        subprocess.run(['git', 'checkout', '-q', '--detach', args.commit], cwd=product, check=True)
        actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=product, text=True).strip()
        assert actual == args.commit, 'target must be exact full commit'
        for name, cmd in [('install', ['npm', 'ci', '--prefix', 'app']), ('tests', ['npm', '--prefix', 'app', 'test']), ('build', ['npm', '--prefix', 'app', 'run', 'build'])]:
            with (args.output / f'{name}.log').open('w') as log:
                result = subprocess.run(cmd, cwd=product, stdout=log, stderr=subprocess.STDOUT, timeout=120)
            report['checks'].append({'name': name, 'exit_code': result.returncode})
            assert result.returncode == 0, name + ' failed'
        assert (product / 'app/dist/server.mjs').is_file(), 'missing built entrypoint'
        server = Product(product, args.output)
        server.start()
        report['api_criteria'] = {'cargo-mini': cargo, 'warehouse': warehouse, 'publishing': publishing}[args.case](server)
        browser_dir = args.output / 'browser'
        browser_dir.mkdir(mode=0o777)
        browser_dir.chmod(0o777)
        browser_script = Path(__file__).with_name('delivery_browser.cjs')
        cmd = ['docker', 'run', '--rm', '--network', 'host', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
               '-v', f'{browser_script}:/eval/browser.cjs:ro', '-v', f'{browser_dir}:/evidence',
               '--entrypoint', 'node', args.browser_image, '/eval/browser.cjs', server.base, args.case]
        with (args.output / 'browser.log').open('w') as log:
            result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        report['checks'].append({'name': 'docker-browser', 'exit_code': result.returncode})
        assert result.returncode == 0, 'browser failed'
        report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed_or_blocked', error=str(exc), classification='requires_execution_vs_product_review')
    finally:
        if server:
            server.stop()
            server.log.close()
            (args.output / 'http-receipts.json').write_text(json.dumps(server.requests, ensure_ascii=False, indent=2))
        report['finished_at'] = time.time()
        report['oracle_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        (args.output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())

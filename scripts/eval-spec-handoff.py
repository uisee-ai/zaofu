#!/usr/bin/env python3
"""Bounded paired Spec generation and fresh-context handoff, never runtime approval.

Reuses the existing provider runner. Docker mounts exclude the oracle; the
explicit host-read-only fallback requires transcript inspection as well.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/spec_handoff_eval'
METHOD = 'skills/zf-plan-execution-readiness/references/task-spec-slices.md'
AUTHOR = '''你是规格作者。读取 requirement.md 和 method.md。只生成 Plan/Shared Spec/Task Spec，
不执行工作流，不修改文件，不访问外部资料，不进行方法评测或读取评估协议。
只读当前目录指定文件，不查找父目录、其他仓库、历史会话或隐藏测试。
依据冻结要求给出本期范围、任务依赖、逐任务来源/行为/失败不变项/AC/命令/验证责任和决定。
共享正文只在确有共同义务时使用。保持事实、计划、测试执行状态的区别。
最终仅输出 Markdown，使用精确的 ## Shared Spec 和 ## Task Spec: <task_id> 标题。
内容以独立接手可用为准，不复制本提示或整份需求；不要自评得分。
'''
HANDOFF = '''你是未参加作者工作的独立接手者。只读取 requirement.md 与 spec.md，
没有作者聊天上下文。逐条核对来源，复述本片责任、依赖、失败行为及版本/决定影响。
只读当前目录指定文件，不查找父目录、其他仓库、历史会话或隐藏测试。
如规格存在缺口或冲突，给原文位置和最小反例；合理内容不强行挑错。
不要把要求原文有但规格遗漏的事实称为规格已表达。可以明确标注从来源补得的内容。
最终只输出 JSON：{"requirements":[{"source":"R1","spec_location":"...或缺失",
"assessment":"covered|partial|missing|conflict","reason":"..."}],
"handoff":"责任/顺序/关键行为说明","questions":[],"findings":[],"implementation":null}。
若 requirement.md 要求实现接口，implementation 放完整可导入的 ES module 源码字符串，
严格按已确认需求实现该最小接口；不要访问文件/网络、引入依赖或读取隐藏测试。
这是有限交接实验，不代表正式批准或完整产品通过。不修改文件。
'''


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def helper():
    spec = importlib.util.spec_from_file_location('review_eval', ROOT / 'scripts/eval-review-skill.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project(folder: Path, files: dict[str, str]) -> None:
    folder.mkdir(parents=True)
    for name, content in files.items():
        if Path(name).name != name or name.startswith('.'):
            raise ValueError('evaluation files must be direct, non-hidden children')
        (folder / name).write_text(content)
    subprocess.run(['git', 'init', '-q', str(folder)], check=True)


def parse_handoff(answer: str) -> dict:
    value = answer.strip()
    if value.startswith('```json\n') and value.endswith('```'):
        value = value[len('```json\n'):-3]
    result = json.loads(value)
    if not isinstance(result, dict) or not isinstance(result.get('requirements'), list):
        raise ValueError('handoff requires a requirements list')
    code = result.get('implementation')
    if code is not None and not isinstance(code, str):
        raise ValueError('implementation must be source text or null')
    return result


def valid(result: dict) -> bool:
    return (result['returncode'] == 0 and result['turn_completed']
            and not result['timed_out'] and not result['errors']
            and not result['changed_paths'] and bool(result['answers']))


def text_prompt(instruction: str, files: dict[str, str]) -> str:
    """Provide authorized fixture text directly; never relax a failed sandbox."""
    return ('本次为纯文本输入实验。下方附件包含全部冻结文件正文。只读取这些文本，'
            '不要调用任何工具或读取磁盘；文件名仅用于定位引用。\n' + instruction
            + '\n附件正文：\n' + json.dumps(files, ensure_ascii=False))


def execute(case: dict, arm: str, method: str, args) -> dict:
    directory = args.output / f"{case['id']}-{arm}"
    author = directory / 'author'
    project(author / 'project', {'requirement.md': case['requirement'], 'method.md': method})
    author_prompt = text_prompt(AUTHOR, {'requirement.md': case['requirement'], 'method.md': method}) if args.transport == 'host-text' else AUTHOR
    (author / 'prompt.txt').write_text(author_prompt)
    record = {'case': case['id'], 'arm': arm, 'status': 'prepared'}
    if args.prepare_only:
        return record
    run = helper()
    result = run.run_one(author, author_prompt, args)
    record['author'] = {k: result[k] for k in ('returncode', 'seconds', 'usage', 'timed_out', 'trace_sha256')}
    if not valid(result) or args.transport == 'host-text' and result.get('commands'):
        record['status'] = 'author_failed'
        return record
    spec_text = result['answers'][-1]
    handoff = directory / 'handoff'
    project(handoff / 'project', {'requirement.md': case['requirement'], 'spec.md': spec_text})
    handoff_prompt = text_prompt(HANDOFF, {'requirement.md': case['requirement'], 'spec.md': spec_text}) if args.transport == 'host-text' else HANDOFF
    (handoff / 'prompt.txt').write_text(handoff_prompt)
    result = run.run_one(handoff, handoff_prompt, args)
    record['handoff'] = {k: result[k] for k in ('returncode', 'seconds', 'usage', 'timed_out', 'trace_sha256')}
    record['spec_sha256'] = sha(spec_text.encode())
    if not valid(result) or args.transport == 'host-text' and result.get('commands'):
        record['status'] = 'handoff_failed'
        return record
    try:
        parsed = parse_handoff(result['answers'][-1])
    except (ValueError, KeyError) as error:
        record.update(status='handoff_format_failed', error=str(error))
        return record
    save(handoff / 'parsed.json', parsed)
    record['status'] = 'completed_ungraded'
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline', default='fe39e0f8')
    parser.add_argument('--candidate-method', type=Path, default=ROOT / METHOD)
    parser.add_argument('--model', required=True)
    parser.add_argument('--docker-image', default='mcp/playwright:latest')
    parser.add_argument('--transport', choices=['docker', 'host-read-only', 'host-text'], default='docker')
    parser.add_argument('--auth-file', type=Path, default=Path.home() / '.codex/auth.json')
    parser.add_argument('--timeout', type=int, default=240)
    parser.add_argument('--workers', type=int, choices=[1, 2], default=2)
    parser.add_argument('--case', action='append', default=[], help='Explicit subset for a follow-up; retain the full frozen corpus digest')
    parser.add_argument('--arm', action='append', choices=['baseline', 'candidate'], default=[])
    parser.add_argument('--case-addendum', type=Path, help='Explicit frozen clarification, applied identically to both arms')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if args.output.exists() or not 1 <= args.timeout <= 600:
        parser.error('output must be new; timeout must be 1..600 seconds')
    if not args.prepare_only and not args.auth_file.is_file():
        parser.error('local provider auth required')
    baseline = subprocess.check_output(['git', 'rev-parse', '--verify', args.baseline + '^{commit}'], cwd=ROOT, text=True).strip()
    methods = {'baseline': subprocess.check_output(['git', 'show', f'{baseline}:{METHOD}'], cwd=ROOT).decode(),
               'candidate': args.candidate_method.read_text()}
    cases = json.loads((FIXTURES / 'cases.json').read_text())
    if len(cases) != 6 or len({c['id'] for c in cases}) != 6:
        raise ValueError('the frozen pilot must contain six distinct cases')
    if set(args.case) - {c['id'] for c in cases}:
        parser.error('unknown case id')
    addendum = json.loads(args.case_addendum.read_text()) if args.case_addendum else {}
    if (not isinstance(addendum, dict) or set(addendum) - {c['id'] for c in cases}
            or any(not isinstance(v, str) or not v.strip() for v in addendum.values())):
        parser.error('clarifications require known case ids and nonempty text')
    for case in cases:
        if case['id'] in addendum:
            case['requirement'] += '\n\n' + addendum[case['id']]
    selected = [c for c in cases if not args.case or c['id'] in args.case]
    arms = list(dict.fromkeys(args.arm or ['baseline', 'candidate']))
    if args.transport in {'host-read-only', 'host-text'}:
        args.docker_image = None
    elif not args.prepare_only:
        args.docker_image = subprocess.check_output(['docker', 'image', 'inspect', args.docker_image, '--format', '{{.Id}}'], text=True).strip()
    args.output.mkdir(parents=True)
    save(args.output / 'case-addendum.json', addendum)
    for name, method in methods.items():
        (args.output / f'{name}-method.md').write_text(method)
    manifest = {'schema_version': 'spec-handoff-eval.v1', 'baseline': baseline,
                'model': args.model, 'reasoning_effort': 'provider_default_same_for_both_arms',
                'docker_image': args.docker_image, 'max_provider_calls': len(selected) * len(arms) * 2,
                'selected_cases': [c['id'] for c in selected], 'arms': arms,
                'transport': args.transport,
                'timeout_seconds': args.timeout, 'workers': args.workers,
                'codex_version': None if args.prepare_only else subprocess.check_output(['codex', '--version'], text=True).strip(),
                'method_sha256': {k: sha(v.encode()) for k, v in methods.items()},
                'cases_sha256': sha((FIXTURES / 'cases.json').read_bytes()),
                'addendum_sha256': sha((args.output / 'case-addendum.json').read_bytes()),
                'oracle_sha256': sha((FIXTURES / 'oracle.json').read_bytes()),
                'prompts': {'author': sha(AUTHOR.encode()), 'handoff': sha(HANDOFF.encode())},
                'prepared_only': args.prepare_only, 'runs': [],
                'limitations': ['One repetition per case/arm; synthetic pilot, no statistical superiority.',
                               'Same-model fresh sessions, not independent humans or cross-model replication.',
                               'Direct provider method experiment, not v5 workflow/admission qualification.',
                               'Host-read-only is not filesystem read isolation; inspect every tool command.',
                               'Host-text supplies frozen input text directly and rejects tool commands; it does not measure file retrieval.',
                               'Semantic assessment remains explicit review; completion is not a pass.']}
    save(args.output / 'manifest.json', manifest)
    jobs = [(c, arm) for i, c in enumerate(selected)
            for arm in (arms if i % 2 == 0 else list(reversed(arms)))]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(execute, c, arm, methods[arm], args) for c, arm in jobs]
        for future in as_completed(futures):
            row = future.result()
            manifest['runs'].append(row)
            save(args.output / 'manifest.json', manifest)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    return int(any(r['status'].endswith('failed') for r in manifest['runs']))


if __name__ == '__main__':
    raise SystemExit(main())

"""Semantic approval selection is compiled and cannot be lost in a preview."""
import copy

import pytest

from tests.test_workflow_controller_chat_source import rewrite_request
from tests.test_workflow_registered_controller import assembly, _admit, _candidate
from zf.core.config.loader import load_config
from zf.runtime.workflow_synthesis import WorkflowSynthesisError


def approval_assembly(assembly):
    base, state, _, result, writer = assembly
    request = rewrite_request(state, {'kind': 'prd', 'source_ref': 'prd.md', 'target_root': '.',
        'objective': 'Build hello. Workflow and Plan require my approval before implementation.'})
    result = copy.deepcopy(result)
    result.update(requirement_ref=request['requirement_spec_ref'], requirement_digest=request['requirement_spec_digest'])
    result['short_flow_spec']['parameters']['approval_requirements'] = {
        'plan': 'required', 'source_quotes': ['Workflow and Plan require my approval before implementation.']}
    return base, state, request, result, writer


def test_explicit_plan_approval_is_in_first_compiled_yaml(assembly):
    bound = approval_assembly(assembly)
    _, _, candidate = _candidate(bound)
    assert load_config(candidate).workflow.plan_approval_enabled


@pytest.mark.parametrize('fault', ['missing', 'unbound_quote', 'wrong_type'])
def test_registered_synthesis_requires_an_explicit_bound_approval_decision(assembly, fault):
    bound = approval_assembly(assembly)
    params = bound[3]['short_flow_spec']['parameters']
    if fault == 'missing':
        del params['approval_requirements']
    elif fault == 'unbound_quote':
        params['approval_requirements']['source_quotes'] = ['invented owner instruction']
    else:
        params['approval_requirements']['plan'] = True
    with pytest.raises(WorkflowSynthesisError, match='approval'):
        _admit(bound)


def test_required_approval_cannot_be_disabled_in_reviewed_candidate(assembly):
    bound = approval_assembly(assembly)
    _, _, candidate = _candidate(bound)
    from zf.runtime.workflow_approval_requirements import enforce_approval_requirements
    config = load_config(candidate)
    config.workflow.plan_approval_enabled = False
    with pytest.raises(ValueError, match='approval'):
        enforce_approval_requirements(config, bound[3]['short_flow_spec']['parameters'])

"""Action contracts: pure Python/stub workflows, no models or mesh evaluation."""
import asyncio
from dataclasses import replace
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from adsl.agents import actions, assembly_physics, assembly_topology, fixed_assembly
from adsl.agents.service import ObjectWorkflow


ASYNC_ENTRIES = [
    ('review_image', '_review_generation_image'),
    ('review_code', '_review_generation_code'),
    ('edit', '_repair'),
]


def arguments(fn, *, defaults=False):
    return {n: object() if p.default is inspect.Parameter.empty else p.default
            for n, p in inspect.signature(fn).parameters.items()
            if n not in ('workflow', 'self') and
            (defaults or p.default is inspect.Parameter.empty)}


@pytest.mark.parametrize('action,method', ASYNC_ENTRIES)
def test_explicit_signature_matches_original(action, method):
    entry = inspect.signature(getattr(actions, action))
    original = inspect.signature(getattr(ObjectWorkflow, method))
    assert list(entry.parameters)[0] == 'workflow'
    assert list(entry.parameters)[1:] == list(original.parameters)[1:]
    for name, p in list(entry.parameters.items())[1:]:
        assert p.kind == inspect.Parameter.KEYWORD_ONLY
        assert p.default == original.parameters[name].default


@pytest.mark.parametrize('action,method', ASYNC_ENTRIES)
@pytest.mark.parametrize('explicit_defaults', [False, True])
@pytest.mark.parametrize('result', [None, {'status': 'CHANGED'}, {'status': 'NO_CHANGE'}, {'status': 'TOOL_ERROR'}])
def test_async_passthrough_identity(action, method, explicit_defaults, result):
    entry = getattr(actions, action)
    impl = AsyncMock(return_value=result)
    workflow = SimpleNamespace(**{method: impl})
    kwargs = arguments(entry, defaults=explicit_defaults)
    assert asyncio.run(entry(workflow, **kwargs)) is result
    impl.assert_awaited_once()
    expected = inspect.signature(entry).bind(workflow, **kwargs)
    expected.apply_defaults()
    expected.arguments.pop('workflow')
    actual = impl.call_args.kwargs
    assert actual.keys() == expected.arguments.keys()
    assert all(actual[k] is v for k, v in expected.arguments.items())


@pytest.mark.parametrize('action,method', ASYNC_ENTRIES)
def test_async_error_identity_and_unknown_keyword(action, method):
    entry = getattr(actions, action)
    error = RuntimeError('original error')
    impl = AsyncMock(side_effect=error)
    workflow = SimpleNamespace(**{method: impl})
    kwargs = arguments(entry)
    with pytest.raises(TypeError):
        entry(workflow, **kwargs, unknown_parameter=True)
    impl.assert_not_called()
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(entry(workflow, **kwargs))
    assert caught.value is error
    impl.assert_awaited_once()


@pytest.mark.parametrize('domains,expected', [
    (None, ['standing', 'fea', 'topology', 'overhang']),
    (('topology', 'overhang', 'standing'), ['standing', 'topology', 'overhang']),
    (('overhang',), ['overhang']),
    (('fea',), ['fea']),
    (('overhang', 'overhang'), ['overhang']),
    ((), []),
])
def test_physics_selection_and_passthrough(monkeypatch, domains, expected):
    specs = [SimpleNamespace(name='assembly_' + n) for n in ('standing', 'fea', 'topology', 'overhang')]
    result = [object()]
    impl = Mock(return_value=result)
    monkeypatch.setattr(assembly_physics, 'run_assembly_checks', impl)
    kwargs = {n: object() for n in ('execution', 'source', 'root', 'physics', 'initial_topology_run', 'partition_reference')}
    assert actions.review_physics(specs, domains=domains, **kwargs) is result
    impl.assert_called_once()
    selected = impl.call_args.args[0]
    assert [s.name for s in selected] == ['assembly_' + n for n in expected]
    assert all(any(s is original for original in specs) for s in selected)
    if domains is None:
        assert selected is specs
    assert all(impl.call_args.kwargs[k] is v for k, v in kwargs.items())


def test_physics_validation_and_original_error(monkeypatch):
    impl = Mock(return_value=[])
    monkeypatch.setattr(assembly_physics, 'run_assembly_checks', impl)
    kwargs = dict(execution=None, source=None, root=None, physics={})
    sig = inspect.signature(actions.review_physics)
    assert list(sig.parameters) == ['specs', 'execution', 'source', 'root', 'physics', 'domains', 'initial_topology_run', 'partition_reference']
    assert all(p.kind == inspect.Parameter.KEYWORD_ONLY for p in list(sig.parameters.values())[1:])
    assert all(sig.parameters[n].default is None for n in ('domains', 'initial_topology_run', 'partition_reference'))
    with pytest.raises(TypeError):
        actions.review_physics([], **kwargs, unknown=True)
    with pytest.raises(ValueError, match='unknown review domain'):
        actions.review_physics([], **kwargs, domains=('unknown',))
    unknown = [SimpleNamespace(name='legacy_unknown')]
    with pytest.raises(ValueError, match='unknown assembly checker'):
        actions.review_physics(unknown, **kwargs, domains=())
    impl.assert_not_called()
    # None retains the legacy path, even for names the explicit selector rejects.
    actions.review_physics(unknown, **kwargs)
    assert impl.call_args.args[0] is unknown
    actions.review_physics([], **kwargs, domains=('standing',))
    assert impl.call_args.args[0] == []  # Never creates a missing checker.
    error = RuntimeError('runner failure')
    impl.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        actions.review_physics([], **kwargs)
    assert caught.value is error


def test_compatibility_aliases_and_rule_results():
    assert assembly_topology.validate_partition_proposal is actions.regroup_precheck
    assert assembly_topology.InvalidPartitionProposal is actions.InvalidPartitionProposal
    assert fixed_assembly._grouping_realization is actions.regroup_realization
    actions.regroup_precheck(None, purpose='required_repair')
    actions.regroup_precheck(None, purpose='partition_optimization', continuing=True)
    with pytest.raises(actions.InvalidPartitionProposal, match='first partition optimization'):
        actions.regroup_precheck(None, purpose='partition_optimization')
    error = ValueError('same ownership error')
    change = SimpleNamespace(validate_current_assembly=Mock(side_effect=error))
    proposal = SimpleNamespace(action='regroup_print_parts', grouping_change=change)
    with pytest.raises(actions.InvalidPartitionProposal) as caught:
        actions.regroup_precheck(proposal, purpose='partition_optimization', editable=True)
    assert str(caught.value) == str(error) and caught.value.__cause__ is error
    before = 'assembly.add_part("p", body)\n'
    after = before + 'assembly.set_print_orientation("p", rotation_deg=(90,0,0))\n'
    assert actions.reorient_postcheck(before, after) is True
    assert actions.reorient_postcheck(before, after.replace('body', 'other')) is False
    assert actions.regroup_realization(None, None, None, source_sha256='hash') is None


def test_cached_topology_and_single_overhang_refresh(tmp_path, monkeypatch):
    from adsl.agents.checkers import CheckerRun
    from adsl.agents.models import CheckerResult
    from adsl.agents.utils.execution import ExecutionResult
    specs = [assembly_physics.checker_spec('assembly_standing'),
             assembly_topology.checker_spec(), assembly_physics.checker_spec('assembly_overhang')]
    cached = CheckerRun(specs[1], CheckerResult(checker=specs[1].name, status='PASS', summary='cached'), tmp_path, ())
    calls = []
    def checker(spec, **kwargs):
        calls.append(spec.name)
        return CheckerRun(spec, CheckerResult(checker=spec.name, status='PASS', summary='stub'), tmp_path, ())
    monkeypatch.setattr(assembly_topology, 'run_assembly_topology', Mock(side_effect=AssertionError('cache not reused')))
    monkeypatch.setattr(assembly_physics, 'run_checker', checker)
    kwargs = dict(execution=ExecutionResult(tmp_path, tmp_path/'scene.glb', None, (), '', ''),
                  source=tmp_path/'source.py', root=tmp_path, physics={})
    results = actions.review_physics(specs, initial_topology_run=cached, **kwargs)
    assert results[0] is cached
    assert calls == ['assembly_overhang', 'assembly_standing']
    calls.clear()
    refreshed = actions.review_physics([specs[2]], domains=('overhang',), **kwargs)
    assert calls == ['assembly_overhang'] and len(refreshed) == 1


@pytest.mark.parametrize('mode', ['pass', 'repair', 'no_change', 'error'])
@pytest.mark.parametrize('with_physics', [False, True])
def test_fixed_flow_uses_actions_without_extra_calls(tmp_path, monkeypatch, mode, with_physics):
    from test_fixed_assembly import mock_flow, run_flow
    outcomes = [('PASS', True)] if mode == 'pass' else [('FAIL', False), ('PASS', True)]
    patch_status = {'no_change': 'NO_CHANGE', 'error': 'TOOL_ERROR'}.get(mode, 'CHANGED')
    state = mock_flow(tmp_path, monkeypatch, outcomes, patch_status=patch_status)
    specs = (assembly_physics.checker_spec('assembly_standing'),) if with_physics else ()
    state = (state[0], replace(state[1], checker_specs=specs), *state[2:])
    events = []
    from adsl.agents.checkers import CheckerRun
    from adsl.agents.models import CheckerResult
    def checks(selected, *, root, **kwargs):
        return [CheckerRun(s, CheckerResult(checker=s.name, status='PASS', summary='stub'), root, ()) for s in selected]
    monkeypatch.setattr(assembly_physics, 'run_assembly_checks', checks)
    for name in ('review_image', 'review_code', 'edit'):
        original = getattr(actions, name)
        async def spy(*args, _name=name, _original=original, **kwargs):
            events.append(_name)
            return await _original(*args, **kwargs)
        monkeypatch.setattr(fixed_assembly, name, spy)
    physics = actions.review_physics
    def review(*args, **kwargs):
        events.append('review_physics')
        return physics(*args, **kwargs)
    monkeypatch.setattr(fixed_assembly, 'review_physics', review)
    result, book = run_flow(state)
    physical = ['review_physics'] if with_physics else []
    first = ['review_image'] + physical if mode == 'pass' else ['review_image', 'review_code'] + physical + ['edit']
    expected = first + (['review_image'] + physical if mode == 'repair' else [])
    assert events == expected
    assert len(state[-1]) == (0 if mode == 'pass' else 1)
    assert book['retained'] == ('attempt_0001' if mode == 'repair' else 'original')
    assert result.approved == (mode in ('pass', 'repair'))
    if mode in ('no_change', 'error'):
        assert book['stop_reason'] == patch_status


@pytest.mark.parametrize('restricted', [False, True])
def test_reorient_guard_keeps_controller_short_circuit(tmp_path, monkeypatch, restricted):
    from test_fixed_assembly import mock_flow, run_flow
    state = mock_flow(tmp_path, monkeypatch, [('PASS', False), ('PASS', True)])
    state = (state[0], replace(state[1], checker_specs=(), max_rounds=2), *state[2:])
    repair = state[0]._repair
    async def patch(**kwargs):
        # Fixture supplies the existing flag; no new policy is introduced.
        kwargs['payload']['feedback']['orientation_only_edit'] = restricted
        return await repair(**kwargs)
    monkeypatch.setattr(state[0], '_repair', patch)
    predicate = Mock(wraps=actions.reorient_postcheck)
    monkeypatch.setattr(fixed_assembly, 'reorient_postcheck', predicate)
    result, book = run_flow(state)
    assert len(state[-1]) == 1
    if restricted:
        predicate.assert_called_once_with('original', 'candidate1')
        assert book['stop_reason'] == 'overhang_only_shape_edit_rejected'
        assert book['versions']['attempt_0001']['reviews']['reason'] == book['stop_reason']
        assert book['retained'] == 'original' and not result.approved
    else:
        predicate.assert_not_called()
        assert result.approved and book['retained'] == 'attempt_0001'


def test_action_import_does_not_require_native_dependencies():
    import subprocess
    import sys
    script = '''
import builtins
original = builtins.__import__
def checked(name, *args, **kwargs):
    if name.split('.')[0] in {'bpy', 'bmesh', 'manifold3d', 'mujoco'}:
        raise AssertionError('unexpected native import: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = checked
from adsl.agents import actions, assembly_topology, fixed_assembly
assert assembly_topology.InvalidPartitionProposal is actions.InvalidPartitionProposal
assert fixed_assembly._grouping_realization is actions.regroup_realization
'''
    subprocess.run([sys.executable, '-c', script], check=True, capture_output=True, text=True)

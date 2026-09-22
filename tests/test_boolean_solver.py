"""Public CSG solver choice and existing failure cleanup, without model APIs."""
import importlib
from types import SimpleNamespace

import pytest

exporter = importlib.import_module('adsl.core.export.export_glb')


@pytest.mark.parametrize('operation', ['UNION', 'DIFFERENCE', 'INTERSECT'])
@pytest.mark.parametrize('fails', [False, True])
def test_public_boolean_uses_exact_only_and_preserves_errors(monkeypatch, operation, fails):
    created, calls, removed_modifiers, removed_objects = [], [], [], []
    failure = RuntimeError('native Boolean failed')

    def new_modifier(*, name, type):
        assert type == 'BOOLEAN'
        modifier = SimpleNamespace(name=name)
        created.append(modifier)
        return modifier

    base = SimpleNamespace(modifiers=SimpleNamespace(new=new_modifier, remove=removed_modifiers.append))
    other = SimpleNamespace()
    active = SimpleNamespace(active=None)

    def apply(*, modifier):
        current = created[-1]
        assert active.active is base and current.object is other
        assert current.operation == operation and modifier == current.name
        calls.append(current.solver)
        if fails:
            raise failure

    def remove(obj, *, do_unlink):
        assert do_unlink
        removed_objects.append(obj)

    monkeypatch.setattr(exporter, 'bpy', SimpleNamespace(
        context=SimpleNamespace(view_layer=SimpleNamespace(objects=active)),
        ops=SimpleNamespace(object=SimpleNamespace(modifier_apply=apply)),
        data=SimpleNamespace(objects=SimpleNamespace(remove=remove))))
    if fails:
        with pytest.raises(RuntimeError) as caught:
            exporter._apply_boolean(base, other, operation)
        assert caught.value is failure
        assert removed_modifiers == created
    else:
        exporter._apply_boolean(base, other, operation)
        assert removed_modifiers == []
    assert calls == ['EXACT'] and len(created) == 1
    assert removed_objects == [other]

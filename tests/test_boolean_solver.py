"""CSG solver choice and transactional group cleanup, without model APIs."""
import importlib
import os
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
    # A failed low-level operation leaves both inputs for the group-level
    # Manifold retry; successful EXACT evaluation consumes its temporary other.
    assert removed_objects == ([] if fails else [other])


@pytest.mark.skipif(os.environ.get('ADSL_TEST_GEOMETRY_REAL') != '1', reason='native Blender opt-in')
@pytest.mark.parametrize('recovery_fails', [False, True])
def test_real_boolean_group_cleans_temporaries_and_preserves_failed_inputs(monkeypatch, recovery_fails):
    bpy = pytest.importorskip('bpy')
    import numpy as np
    import trimesh
    from adsl.core import Cube

    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    base, = exporter._build_shape(Cube((2, 2, 4), center=(0, 0, 2)))
    other, = exporter._build_shape(Cube((2, 2, 2), center=(1.5, 0, 2)))
    original_objects = {o.as_pointer() for o in bpy.data.objects}
    original_data = base.data
    before = np.array([tuple(v.co) for v in base.data.vertices])
    other_data = other.data

    def fail_exact(*args):
        raise RuntimeError('forced EXACT failure after duplicate creation')

    monkeypatch.setattr(exporter, '_apply_boolean', fail_exact)
    if recovery_fails:
        def fail_recovery(*args):
            raise ValueError('forced recovery failure')
        monkeypatch.setattr(exporter, '_recover_boolean', fail_recovery)
        with pytest.raises(ValueError, match='forced recovery failure'):
            exporter._apply_boolean_group(base, [other], 'UNION')
        assert base.data is original_data
        np.testing.assert_array_equal([tuple(v.co) for v in base.data.vertices], before)
    else:
        exporter._apply_boolean_group(base, [other], 'UNION')
        vertices, faces, _ = exporter._mesh_triangles(base.data, base.matrix_world)
        mesh = trimesh.Trimesh(vertices, faces, process=False)
        assert mesh.is_watertight and mesh.is_winding_consistent
        assert mesh.volume == pytest.approx(22)
        assert 'adsl_boolean_recovery' in base

    assert other.data is other_data
    assert {o.as_pointer() for o in bpy.data.objects} == original_objects

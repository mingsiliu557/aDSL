"""Camera and attachment contracts, including actual executor/queue boundaries."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from adsl.agents.service import ObjectWorkflow
from adsl.agents.utils import execution
from adsl.tools import gpu_render_queue as queue
from adsl.tools import render


def test_review_eight_cameras_face_origin_and_have_explicit_vertical_axes():
    views = render.review_eight_render_views()
    assert len(views) == 8 and [v.index for v in views] == list(range(8))
    for view in views:
        matrix = np.asarray(view.camera_matrix)
        np.testing.assert_allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=1e-6)
        assert np.linalg.det(matrix[:3, :3]) == pytest.approx(1)
        direction = -matrix[:3, 2]
        np.testing.assert_allclose(direction, -matrix[:3, 3] / np.linalg.norm(matrix[:3, 3]), atol=1e-6)
        assert view.label
    np.testing.assert_allclose(np.asarray(views[6].camera_matrix)[:3, 1], [0, 1, 0])
    np.testing.assert_allclose(np.asarray(views[7].camera_matrix)[:3, 1], [0, -1, 0])
    assert len(render.orbit_render_views(num_camera_per_layer=8)) == 8


@pytest.mark.parametrize('layout,count,elevations', [('bad',8,(15,)), ('review_eight',6,(15,)), ('review_eight',8,(15,30))])
def test_invalid_layout_fails_before_render(tmp_path, layout, count, elevations):
    with pytest.raises(ValueError):
        render.render_video(tmp_path, glb_path='unused', view_layout=layout,
                            num_camera_per_layer=count, elevations=elevations)
    with pytest.raises(ValueError):
        glb=tmp_path/'scene.glb';glb.write_bytes(b'glb')
        queue.enqueue_render_job(queue_root=tmp_path/'q', glb_path=glb, output_dir=tmp_path/'out',
            require_worker=False, view_layout=layout, num_camera_per_layer=count, elevations=elevations)


@pytest.mark.parametrize('fixed', [False, True])
def test_execution_passes_layout_to_real_command_boundary(tmp_path, monkeypatch, fixed):
    src=tmp_path/'source.py';src.write_text('scene = None')
    process=Mock(returncode=0 if fixed else 1)
    process.communicate.return_value=('', '')
    commands=[]
    def popen(command, **kwargs):
        commands.append(command)
        if len(commands)==2: return Mock(returncode=1, communicate=Mock(return_value=('', 'mock render failure')))
        return process
    monkeypatch.setattr(execution.subprocess,'Popen',popen)
    with pytest.raises(execution.AssetExecutionError):
        execution.execute_asset_source(src,tmp_path/'asset',render_view_layout='review_eight',
            fixed_assembly={'validation_mode':'visual_only'} if fixed else None)
    command=commands[-1]
    assert command[command.index('--view-layout')+1]=='review_eight'
    assert ('--render-only' in command) is fixed


def test_queue_layout_request_worker_and_legacy_default(tmp_path):
    glb=tmp_path/'scene.glb';glb.write_bytes(b'glb')
    job=queue.enqueue_render_job(queue_root=tmp_path/'q',glb_path=glb,output_dir=tmp_path/'out',
        require_worker=False,view_layout='review_eight')
    request=json.loads((tmp_path/'q/pending'/f'{job}.json').read_text())
    command=queue._render_command(request,python_executable='python')
    assert command[command.index('--view-layout')+1]=='review_eight'
    del request['view_layout']
    command=queue._render_command(request,python_executable='python')
    assert command[command.index('--view-layout')+1]=='orbit'


@pytest.mark.parametrize('queued',[False,True])
def test_asset_executor_local_and_queue_receive_layout(tmp_path,monkeypatch,queued):
    from adsl.agents.utils import asset_executor as child
    calls=[]
    class FakeAsset: pass
    monkeypatch.setattr(child,'Asset',FakeAsset)
    monkeypatch.setattr(child.runpy,'run_path',lambda *a,**k:{'scene':FakeAsset()})
    monkeypatch.setattr(child,'build_source_index',lambda *a:SimpleNamespace(model_dump_json=lambda **k:'{}'))
    monkeypatch.setattr(child,'build_analysis_geometry',lambda *a:{})
    monkeypatch.setattr(child,'export_glb',lambda *a,**k:None)
    monkeypatch.setattr(child,'render_video',lambda **k:calls.append(k))
    def submit(**k):calls.append(k);return {'job_id':'mock'}
    monkeypatch.setattr(child,'submit_render_job',submit)
    monkeypatch.setenv('ADSL_GPU_RENDER_QUEUE','mock' if queued else '')
    child.main(['--source',str(tmp_path/'source.py'),'--output',str(tmp_path/'out'),
                '--render','--view-layout','review_eight'])
    assert len(calls)==1 and calls[0]['view_layout']=='review_eight'


def test_render_only_keeps_two_exploded_views(tmp_path,monkeypatch):
    from adsl.agents.utils import asset_executor as child
    (tmp_path/'assembly').mkdir();(tmp_path/'assembly/exploded.glb').write_bytes(b'glb')
    (tmp_path/'execution.json').write_text(json.dumps({'glb_path':str(tmp_path/'scene.glb')}))
    calls=[];monkeypatch.setattr(child,'render_video',lambda **k:calls.append(k))
    child.main(['--source',str(tmp_path/'source.py'),'--output',str(tmp_path),
                '--render-only','--render','--view-layout','review_eight'])
    assert calls[0]['view_layout']=='review_eight'
    assert calls[1]['num_camera_per_layer']==1 and calls[1]['elevations']==(-30.,30.)
    assert calls[1].get('view_layout','orbit')=='orbit'


def test_view_labels_follow_files_and_deduplicated_attachment_indices(tmp_path):
    paths=[tmp_path/'render_0008.png',tmp_path/'render_0001.png']
    (tmp_path/'meta.json').write_text(json.dumps({'locations':[
        {'file':'render_0001.png','label':'front'}, {'file':'render_0008.png','label':'bottom'}]}))
    rows=ObjectWorkflow._review_view_labels(paths,indices=[3,1])
    assert [(r['image_index'],r['label']) for r in rows]==[(3,'bottom'),(1,'front')]
    (tmp_path/'meta.json').write_text('{broken')
    assert ObjectWorkflow._review_view_labels(paths)==[]
    (tmp_path/'meta.json').unlink()
    assert ObjectWorkflow._review_view_labels(paths)==[]

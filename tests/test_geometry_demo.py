"""Call-boundary smoke; LLM/execution/render mocked, public prompt parsing real."""
import asyncio
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from adsl.agents.models import ObjectPlan
from adsl.agents.prompts import object_prompt
from adsl.agents.tools.context import ToolEvent
from adsl.agents.utils.execution import AssetExecutionError, AssetInfrastructureError

SCRIPT=Path(__file__).resolve().parents[1]/'experiments/geometry_expression/run_demo.py'
spec=importlib.util.spec_from_file_location('geometry_demo',SCRIPT)
demo=importlib.util.module_from_spec(spec);spec.loader.exec_module(demo)


def test_constructive_prompts_and_source_index():
    for role in ('planner','coder'):
        text=object_prompt(role,articulation=False,fixed_assembly=False)
        for name in ('Polygon','linear_extrude','rotate_extrude','hull'):assert name in text
        assert 'scale_top=(1.0, 1.0)' in text and 'angle=360.0, segments=64' in text
    assert '`import math`' in object_prompt('coder',articulation=False)
    from adsl.agents.source_index import _DSL_CALLS
    assert {'Polygon','linear_extrude','rotate_extrude','hull'}<=_DSL_CALLS
    data=json.loads(SCRIPT.with_name('cases.json').read_text())
    assert [c['id'] for c in data['cases']]==['SF06','SF21','T02-bookshelf']
    assert all(not c['reference_images'] for c in data['cases'])


@pytest.mark.parametrize('failure',[None,'source','infra','render'])
def test_demo_stops_at_render_and_limits_source_patch(monkeypatch,tmp_path,failure):
    events=[];attempts=[]
    @dataclass
    class Usage:
        requests:int=2
    class Runtime:
        def __init__(self,**kw):
            self.workspace=kw['workspace']
            self.usage=SimpleNamespace(totals=lambda:Usage())
            self.sessions=SimpleNamespace(database_path=tmp_path/'absent.sqlite')
        def write_runtime_config(self,**kw):events.append(('config',kw))
        def agent(self,**kw):return SimpleNamespace(**kw)
        async def run(self,**kw):
            events.append(('agent',kw['role']))
            if kw['role']=='planner':
                out=ObjectPlan(object_name='sample',components=[],relations=[],critic_checklist=[])
            else:
                ctx=kw['context'];ctx.source_path.write_text('from adsl.core import *\nscene=Cube(1)\n')
                ctx.events.extend([ToolEvent('write_file','source.py')] if kw['role']=='coder_initial' else
                                  [ToolEvent('read_file','source.py'),ToolEvent('apply_patch','source.py')])
                out='done'
            return SimpleNamespace(final_output=out,to_input_list=lambda:[])
    def execute(source,output,**kw):
        attempts.append(kw)
        assert kw['render'] is False and kw['export_urdf'] is False and kw['fixed_assembly'] is None
        if len(attempts)==1:
            if failure=='source':raise AssetExecutionError('bad source')
            if failure=='infra':raise AssetInfrastructureError('unavailable')
        output.mkdir();p=output/'scene.glb';p.touch()
        return SimpleNamespace(glb_path=p,source_index_path=p,analysis_geometry_path=p,stdout='',stderr='')
    def render(glb,output,timeout):
        events.append(('render',timeout))
        if failure=='render':raise RuntimeError('renderer failed')
        return [output/f'{i}.png' for i in range(8)]
    import numpy as np
    import trimesh
    monkeypatch.setattr(demo,'AgentRuntime',Runtime)
    monkeypatch.setattr(demo,'execute_asset_source',execute)
    monkeypatch.setattr(demo,'render_glb',render)
    monkeypatch.setattr(trimesh,'load',lambda *a,**k:SimpleNamespace(geometry={'box':trimesh.creation.box()},bounds=np.zeros((2,3)),extents=np.ones(3)))
    result=asyncio.run(demo.run_demo(dict(id='small',requirement='A box.'),tmp_path/'demo','profile',arm='B'))
    roles=[v for k,v in events if k=='agent']
    assert roles==['planner','coder_initial']+(['coder_execution_patch'] if failure=='source' else [])
    assert result['checkers']==[] and result['critics']==[] and 'approved' not in result
    assert result['status']==('render_failed' if failure=='render' else 'execution_failed' if failure=='infra' else 'rendered')
    assert len(attempts)==(2 if failure=='source' else 1)
    assert result['execution_patches']==int(failure=='source')
    assert (tmp_path/'demo'/'planner'/'system_prompt.md').is_file()
    assert (tmp_path/'demo'/'demo_result.json').is_file()

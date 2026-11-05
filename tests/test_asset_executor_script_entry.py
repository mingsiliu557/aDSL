"""Exercise the production file entrypoint, stopping before export/render."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
import adsl.core
import adsl.tools
from adsl.agents.utils import asset_executor

@pytest.mark.parametrize('mode',['default','profile_static','profile_dynamic'])
def test_executor_file_entry_reaches_source_or_profile_guard(tmp_path,mode):
    overlay=tmp_path/'imports/adsl';overlay.mkdir(parents=True)
    (overlay/'__init__.py').write_text('from .core import *\n')
    for name,target in [('core',Path(adsl.core.__file__).resolve().parent),
                        ('tools',Path(adsl.tools.__file__).resolve().parent),
                        ('agents',Path(asset_executor.__file__).resolve().parents[1])]:
        (overlay/name).symlink_to(target,target_is_directory=True)
    source=tmp_path/'source.py';out=tmp_path/'output'
    bodies={'default':"raise RuntimeError('SCRIPT_ENTRY_REACHED_SOURCE')\n",
            'profile_static':'from adsl.core import loft\n',
            'profile_dynamic':"from adsl import core\nname=''.join(['lo','ft'])\ngetattr(core,name)([])\n"}
    source.write_text(bodies[mode])
    command=[sys.executable,str(Path(asset_executor.__file__).resolve()),'--source',str(source),'--output',str(out)]
    if mode!='default':
        config=tmp_path/'fixed.json';config.write_text(json.dumps({'ablation':'profile_ops'}))
        command+=['--fixed-assembly-config',str(config)]
    env={**os.environ,'PYTHONPATH':str(overlay.parent)}
    result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=60)
    assert result.returncode!=0
    assert 'attempted relative import' not in result.stderr
    diagnostic=json.loads((out/'source_execution_error.json').read_text())
    assert diagnostic['code']=='GENERATED_SOURCE_EXECUTION_FAILED'
    if mode=='default':
        assert diagnostic['exception_type']=='RuntimeError' and diagnostic['message']=='SCRIPT_ENTRY_REACHED_SOURCE'
    else:
        assert diagnostic['reason']=='ABLATION_DISABLED_API' and diagnostic['name']=='loft'
        assert diagnostic['source_locations'][0]['line']==(1 if mode=='profile_static' else 3)

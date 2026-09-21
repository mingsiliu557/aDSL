"""Local SQLite finalization; archival errors cannot erase case results."""
import asyncio
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import sqlite3

import pytest

from adsl.agents.utils.io import read_json,write_json
from experiments.fixed_assembly_prompt import run as prompt
from test_fixed_assembly_prompt import frozen_input


def runtime_with_database(tmp_path):
    database=tmp_path/'local/session.sqlite3';database.parent.mkdir()
    with closing(sqlite3.connect(database)) as db:
        db.execute('CREATE TABLE history (text TEXT)')
        db.execute("INSERT INTO history VALUES ('preserved evidence')");db.commit()
    return SimpleNamespace(sessions=SimpleNamespace(database_path=database))


def test_snapshot_is_closed_before_regular_file_copy(tmp_path,monkeypatch):
    runtime=runtime_with_database(tmp_path);work=tmp_path/'output';work.mkdir()
    copy=prompt.shutil.copyfile
    def checked_copy(source,target):
        with closing(sqlite3.connect(source)) as db:
            db.execute('BEGIN EXCLUSIVE')
            assert db.execute('SELECT text FROM history').fetchone()==('preserved evidence',)
        return copy(source,target)
    monkeypatch.setattr(prompt.shutil,'copyfile',checked_copy)
    result=prompt.snapshot_sessions(runtime,work)
    assert result['status']=='SAVED'
    assert Path(result['local_path']).read_bytes()==(work/'sessions_snapshot.sqlite3').read_bytes()


@pytest.mark.parametrize('stage',['local_sqlite_backup','snapshot_file_copy'])
def test_snapshot_failure_preserves_source_and_reports_stage(tmp_path,monkeypatch,stage):
    runtime=runtime_with_database(tmp_path);work=tmp_path/'output';work.mkdir()
    original=runtime.sessions.database_path.read_bytes()
    def failed(*a,**kw):
        if stage=='local_sqlite_backup':raise sqlite3.OperationalError('disk I/O error')
        raise OSError('disk I/O error')
    if stage=='local_sqlite_backup':monkeypatch.setattr(prompt.sqlite3,'connect',failed)
    else:monkeypatch.setattr(prompt.shutil,'copyfile',failed)
    result=prompt.snapshot_sessions(runtime,work)
    assert result['status']=='ERROR' and result['stage']==stage
    assert runtime.sessions.database_path.read_bytes()==original
    if stage=='snapshot_file_copy':assert result['local_snapshot_available']


def test_backup_warning_does_not_replace_api_failure_or_lose_result(tmp_path,monkeypatch):
    inputs=frozen_input();p=write_json(tmp_path/'SF07/input.json',inputs)
    write_json(tmp_path/'batch.json',{'input_sha256':{'SF07':prompt.file_hash(p)}})
    class Workflow:
        def __init__(self,profile):pass
        async def generate(self,request):
            request.workspace.mkdir(parents=True,exist_ok=True)
            (request.workspace/'source.py').write_text('saved source')
            raise RuntimeError('upstream TLS EOF')
    monkeypatch.setattr(prompt,'PromptWorkflow',Workflow)
    monkeypatch.setattr(prompt,'input_audit',lambda *a:{'input_verified':True,'assembly_api_verified':True})
    def snapshot(runtime,work):
        saved=read_json(tmp_path/'SF07/result.json')
        assert saved['exception']['reason']=='upstream TLS EOF'
        return {'status':'ERROR','stage':'snapshot_file_copy','reason':'disk I/O error'}
    monkeypatch.setattr(prompt,'snapshot_sessions',snapshot)
    result=asyncio.run(prompt.run_case(tmp_path,'SF07'))
    assert result['exception']['reason']=='upstream TLS EOF'
    assert result['session_snapshot']['status']=='ERROR'
    assert not result['approved']
    assert (tmp_path/'SF07/generate/source.py').read_text()=='saved source'
    assert read_json(tmp_path/'SF07/result.json')==result

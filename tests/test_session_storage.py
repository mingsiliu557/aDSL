import asyncio
from pathlib import Path

from adsl.agents.utils.sessions import SessionManager


def test_default_location_unchanged(tmp_path):
    manager=SessionManager(tmp_path/'workspace','task')
    assert manager.database_path==tmp_path/'workspace/sessions.sqlite3'


def test_local_override_separates_workspaces_and_preserves_old_db(tmp_path):
    workspace=tmp_path/'data/workspace';workspace.mkdir(parents=True)
    old=workspace/'sessions.sqlite3';old.write_bytes(b'old evidence')
    one=SessionManager(workspace,'same-task',tmp_path/'local')
    two=SessionManager(tmp_path/'data/other','same-task',tmp_path/'local')
    assert one.database_path!=two.database_path
    assert one.database_path.is_relative_to(tmp_path/'local')
    assert old.read_bytes()==b'old evidence'


def test_sdk_large_session_write_read_and_reopen(tmp_path):
    manager=SessionManager(tmp_path/'data','task',tmp_path/'local')
    items=[{'role':'user','content':'x'*2_000_000}]
    async def run():
        session=manager.for_role('planner')
        await session.add_items(items)
        assert await session.get_items()==items
        session.close()
        reopened=manager.for_role('planner')
        assert await reopened.get_items()==items
        reopened.close()
    asyncio.run(run())
    assert not (manager.workspace/'sessions.sqlite3').exists()

"""Real HTTP trigger -> engine/scheduler -> SQLite history, synthetic task body."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import sessionmaker

import task_engine
from routers import backup
from task_scheduler import TaskResult
from tasks.dbas_restore import DbasRestoreTask


@pytest.fixture
def runtime(test_engine, tmp_path, monkeypatch):
    factory = sessionmaker(bind=test_engine, expire_on_commit=False)
    engine = task_engine.TaskEngine()
    monkeypatch.setattr(engine, '_notify_task_result', AsyncMock())
    task = DbasRestoreTask()
    registry = Mock()
    registry.get_task_instance.return_value = task
    monkeypatch.setattr(task_engine, 'get_registry', lambda: registry)
    monkeypatch.setattr(task_engine, 'get_session', factory)
    monkeypatch.setattr(task_engine, 'get_engine', lambda: engine)
    monkeypatch.setattr(task_engine, 'log_entry', Mock())
    monkeypatch.setattr(backup, '_DBAS_RESTORE_TMP_DIR', tmp_path)
    monkeypatch.setattr(backup, 'BACKUPS_DIR', tmp_path)
    name = 'ecm-backup-2026-01-01_000000.zip'
    (tmp_path / name).write_bytes(b'PK synthetic')
    app = FastAPI()
    app.include_router(backup.router)
    # Existing auth tiers remain exercised by dedicated HTTP authorization suites.
    for dependency in [backup.RequireHumanAdminIfEnabled, backup.RequireAdminIfEnabled,
                       backup.ResolveIsMcpServicePrincipalIfEnabled]:
        app.dependency_overrides[dependency.dependency] = lambda: False
    return SimpleNamespace(engine=engine, task=task, app=app, name=name)


@pytest.mark.parametrize('flow', ['uploaded', 'saved'])
@pytest.mark.parametrize('outcome', ['completed', 'failed', 'cancelled', 'completed_with_warnings'])
async def test_trigger_identity_matches_progress_and_persisted_terminal_history(runtime, monkeypatch, flow, outcome):
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    task, engine = runtime.task, runtime.engine

    async def execute():
        entered.set()
        await release.wait()
        if outcome == 'cancelled':
            task.cancel()
        return TaskResult(success=outcome == 'completed',
                          completed_degraded=outcome == 'completed_with_warnings',
                          error='synthetic failure' if outcome == 'failed' else None,
                          details={'restore_report': {'is_dry_run': True}})
    monkeypatch.setattr(task, '_execute_run', execute)
    original_run = engine.run_task
    results = []
    async def tracked_run(*args, **kwargs):
        try:
            result = await original_run(*args, **kwargs)
            results.append(result)
            return result
        finally:
            if 'dbas_restore' not in engine._active_tasks:
                finished.set()
    monkeypatch.setattr(engine, 'run_task', tracked_run)

    async with AsyncClient(transport=ASGITransport(app=runtime.app), base_url='http://test') as client:
        async def trigger():
            if flow == 'uploaded':
                return await client.post('/api/backup/restore-dbas?run_id=caller-chosen', files={'file': ('backup.zip', b'PK synthetic')})
            return await client.post('/api/backup/restore-dbas-saved', json={'filename': runtime.name, 'run_id': 'caller-chosen'})
        try:
            first = await trigger()
            assert first.status_code == 200
            run_id = first.json()['run_id']
            assert str(UUID(run_id)) == run_id
            await asyncio.wait_for(entered.wait(), 2)
            assert task._progress.to_dict()['run_id'] == run_id
            # A second accepted transport request is rejected by the existing
            # engine lock. Its token must never relabel the running singleton.
            overlap = await trigger()
            assert overlap.json()['run_id'] != run_id
            await asyncio.sleep(0)
            assert task._progress.to_dict()['run_id'] == run_id
            assert any(result.error == 'ALREADY_RUNNING' for result in results)
            release.set()
            await asyncio.wait_for(finished.wait(), 3)
            assert task._progress.to_dict()['status'] == outcome
            history = engine.get_task_history('dbas_restore')
            assert len(history) == 1
            assert history[0]['status'] == outcome
            assert history[0]['details']['run_id'] == run_id
            assert history[0]['details']['restore_report'] == {'is_dry_run': True}
            assert not engine._active_tasks
            # Fixed task-ID rerun gets a fresh transport identity and row.
            finished.clear()
            rerun = await trigger()
            await asyncio.wait_for(finished.wait(), 3)
            assert rerun.json()['run_id'] not in {run_id, overlap.json()['run_id']}
            assert task._progress.to_dict()['run_id'] == rerun.json()['run_id']
            assert engine.get_task_history('dbas_restore')[0]['details']['run_id'] == rerun.json()['run_id']
        finally:
            release.set()
            if engine._active_tasks:
                await asyncio.wait_for(finished.wait(), 3)

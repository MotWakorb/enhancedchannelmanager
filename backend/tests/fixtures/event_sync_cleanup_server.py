"""60r8c isolated browser fixture: real router/SQLite, stateful fake upstream.

No application lifespan, scheduler, provider refresh or live Dispatcharr client.
The fixture run endpoint deliberately invokes the executor synchronously.
"""
import asyncio
import copy
from datetime import datetime
import socket

from tests._config_harness import initialize_test_config, cleanup_test_config


def main():
    config_dir = initialize_test_config()
    try:
        import database
        from fastapi import FastAPI
        import uvicorn
        from channel_pipeline_executor import ActionExecutor, ExecutionContext
        from models import ChannelPipelineRule, ChannelPipelineExecution
        from routers import channel_pipeline
        from services.event_sync_resolver import SecondaryStream
        from tests.services.test_event_sync_cleanup import Upstream, CONFIG, NEW, attach

        database.init_db()
        upstream = Upstream()
        channel_pipeline.get_client = lambda: upstream
        with database.get_session() as session:
            rule = ChannelPipelineRule(id=7, name="Cleanup fixture", conditions='[{"type":"always"}]',
                                       actions='[{"type":"skip"}]', created_at=datetime(2026, 9, 5))
            rule.set_event_sync_config(CONFIG)
            session.add(rule)
            session.commit()
        asyncio.run(attach(upstream))
        upstream.channel["name"] = NEW
        with database.get_session() as session:
            session.get(ChannelPipelineRule, 7).set_event_sync_config({**CONFIG, "detach_stale_streams": False})
            session.commit()
        app = FastAPI()

        @app.post("/fixture/run")
        async def run():
            with database.get_session() as session:
                config = session.get(ChannelPipelineRule, 7).get_event_sync_config()
            executor = ActionExecutor(upstream, existing_channels=[copy.deepcopy(upstream.channel)],
                                      existing_groups=[], execution_id=2)
            context = ExecutionContext(dry_run=False)
            summary = await executor.execute_event_sync_rule(
                7, "Cleanup fixture", config,
                [SecondaryStream(name=NEW, group_id=20, stream_id=3, provider_id=18)], context,
            )
            with database.get_session() as session:
                execution = ChannelPipelineExecution(id=2, rule_id=7, rule_name="Cleanup fixture",
                    mode="execute", status="completed", started_at=datetime.utcnow(), completed_at=datetime.utcnow(),
                    is_event_sync=True)
                execution.set_event_sync_summary([summary])
                execution.set_modified_entities(context.modified_entities)
                session.add(execution)
                session.commit()
            return {"streams": upstream.channel["streams"], "summary": summary}

        @app.get("/fixture/state")
        async def state():
            return {"streams": upstream.channel["streams"], "writes": len(upstream.writes)}

        app.include_router(channel_pipeline.router, prefix="/api/channel-pipeline")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            print(f"CLEANUP_API_PORT={sock.getsockname()[1]}", flush=True)
            uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[sock])
    finally:
        cleanup_test_config(config_dir)


if __name__ == "__main__":
    main()

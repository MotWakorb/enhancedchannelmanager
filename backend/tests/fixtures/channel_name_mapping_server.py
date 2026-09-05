"""Isolated real mapping API for the u0ko6 browser contract test.

Only the normalization router is mounted. No application lifespan, schedulers,
provider refreshes or Dispatcharr writes are started by this fixture.
"""
import socket

from tests._config_harness import initialize_test_config, cleanup_test_config


def main():
    config_dir = initialize_test_config()
    try:
        import database
        from fastapi import FastAPI
        from routers.normalization import router
        import uvicorn

        database.init_db()
        app = FastAPI()
        app.include_router(router)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            print(f"MAPPING_API_PORT={sock.getsockname()[1]}", flush=True)
            uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[sock])
    finally:
        cleanup_test_config(config_dir)


if __name__ == "__main__":
    main()

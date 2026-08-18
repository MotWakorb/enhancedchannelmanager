"""Security regression coverage for bounded password recovery (04c0u.2)."""

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def local_user(test_session):
    from auth.password import hash_password
    from models import User

    user = User(
        username="reset-user",
        email="reset-user@example.com",
        password_hash=hash_password("OldPassword123!"),
        auth_provider="local",
        is_active=True,
    )
    test_session.add(user)
    test_session.commit()
    test_session.refresh(user)
    return user


@pytest.fixture
def enabled_reset_limiter():
    from auth.routes import limiter

    previous = limiter.enabled
    limiter.reset()
    limiter.enabled = True
    try:
        yield limiter
    finally:
        limiter.reset()
        limiter.enabled = previous


@pytest.mark.asyncio
async def test_forgot_password_keeps_one_token_and_throttles_account(
    async_client, test_session, local_user, monkeypatch,
):
    from models import PasswordResetToken

    sent = []
    monkeypatch.setattr(
        "auth.routes.send_password_reset_email",
        lambda email, token, base_url: sent.append(token) or True,
    )

    first = await async_client.post(
        "/api/auth/forgot-password", json={"email": local_user.email}
    )
    second = await async_client.post(
        "/api/auth/forgot-password", json={"email": local_user.email}
    )

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(sent) == 1
    rows = test_session.query(PasswordResetToken).filter_by(user_id=local_user.id).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_forgot_password_removes_expired_and_superseded_rows(
    async_client, test_session, local_user, monkeypatch,
):
    from auth.tokens import hash_token
    from models import PasswordResetToken

    now = datetime.utcnow()
    test_session.add_all(
        [
            PasswordResetToken(
                user_id=local_user.id,
                token_hash=hash_token("expired-token"),
                expires_at=now - timedelta(minutes=1),
            ),
            PasswordResetToken(
                user_id=local_user.id,
                token_hash=hash_token("old-used-token"),
                expires_at=now + timedelta(minutes=10),
                used_at=now - timedelta(minutes=5),
            ),
        ]
    )
    test_session.commit()
    monkeypatch.setattr("auth.routes.send_password_reset_email", lambda *args: True)

    response = await async_client.post(
        "/api/auth/forgot-password", json={"email": local_user.email}
    )

    assert response.status_code == 200
    rows = test_session.query(PasswordResetToken).filter_by(user_id=local_user.id).all()
    assert len(rows) == 1
    assert rows[0].used_at is None
    assert rows[0].expires_at > now


@pytest.mark.asyncio
async def test_forgot_password_replaces_stale_active_token(
    async_client, test_session, local_user, monkeypatch,
):
    from auth.tokens import hash_token
    from models import PasswordResetToken

    stale_hash = hash_token("stale-active-reset-token")
    test_session.add(
        PasswordResetToken(
            user_id=local_user.id,
            token_hash=stale_hash,
            expires_at=datetime.utcnow() + timedelta(minutes=30),
            created_at=datetime.utcnow() - timedelta(minutes=6),
        )
    )
    test_session.commit()
    sent = []
    monkeypatch.setattr(
        "auth.routes.send_password_reset_email",
        lambda _email, token, _base_url: sent.append(token) or True,
    )

    response = await async_client.post(
        "/api/auth/forgot-password", json={"email": local_user.email}
    )

    rows = test_session.query(PasswordResetToken).filter_by(user_id=local_user.id).all()
    assert response.status_code == 200
    assert len(sent) == 1
    assert len(rows) == 1
    assert rows[0].token_hash != stale_hash


@pytest.mark.asyncio
async def test_known_and_unknown_forgot_responses_match(
    async_client, local_user, monkeypatch,
):
    monkeypatch.setattr("auth.routes.send_password_reset_email", lambda *args: True)
    known = await async_client.post(
        "/api/auth/forgot-password", json={"email": local_user.email}
    )
    unknown = await async_client.post(
        "/api/auth/forgot-password", json={"email": "absent@example.com"}
    )
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


@pytest.mark.asyncio
async def test_forgot_password_has_client_rate_limit(
    async_client, local_user, monkeypatch, enabled_reset_limiter,
):
    monkeypatch.setattr("auth.routes.send_password_reset_email", lambda *args: True)
    responses = [
        await async_client.post(
            "/api/auth/forgot-password", json={"email": local_user.email}
        )
        for _ in range(6)
    ]
    assert [response.status_code for response in responses] == [200] * 5 + [429]


@pytest.mark.asyncio
async def test_reset_password_uses_one_indexed_hash_lookup_not_bcrypt_history(
    async_client, test_session, local_user, monkeypatch,
):
    from auth.tokens import hash_token
    from models import PasswordResetToken

    raw_token = "current-reset-credential"
    now = datetime.utcnow()
    test_session.add(
        PasswordResetToken(
            user_id=local_user.id,
            token_hash=hash_token(raw_token),
            expires_at=now + timedelta(hours=1),
        )
    )
    for index in range(500):
        test_session.add(
                PasswordResetToken(
                    user_id=local_user.id,
                    token_hash=hash_token(f"expired-history-{index}"),
                    expires_at=now - timedelta(hours=1),
                    used_at=now - timedelta(hours=2),
                )
        )
    test_session.commit()
    monkeypatch.setattr(
        "auth.routes.verify_password",
        lambda *_args: pytest.fail("reset token validation must not call bcrypt"),
    )

    response = await async_client.post(
        "/api/auth/reset-password",
        json={
            "token": raw_token,
            "new_password": "<Synthetic-04c0u2-New-Password>",
        },
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_successful_reset_consumes_token_and_revokes_sessions(
    async_client, test_session, local_user,
):
    from auth.tokens import hash_token
    from models import PasswordResetToken, UserSession

    raw_token = "single-use-reset-credential"
    token = PasswordResetToken(
        user_id=local_user.id,
        token_hash=hash_token(raw_token),
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    active_session = UserSession(
        user_id=local_user.id,
        refresh_token_hash=hash_token("active-session"),
        expires_at=datetime.utcnow() + timedelta(days=1),
    )
    test_session.add_all([token, active_session])
    test_session.commit()

    response = await async_client.post(
        "/api/auth/reset-password",
        json={
            "token": raw_token,
            "new_password": "<Synthetic-04c0u2-Revocation-Password>",
        },
    )
    test_session.refresh(token)
    test_session.refresh(active_session)

    assert response.status_code == 200
    assert token.used_at is not None
    assert active_session.is_revoked is True


@pytest.mark.asyncio
async def test_reset_token_account_attempt_budget_is_bounded(
    async_client, test_session, local_user,
):
    from auth.tokens import hash_token
    from models import PasswordResetToken

    raw_token = "attempt-limited-reset-credential"
    token = PasswordResetToken(
        user_id=local_user.id,
        token_hash=hash_token(raw_token),
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    test_session.add(token)
    test_session.commit()

    responses = [
        await async_client.post(
            "/api/auth/reset-password",
            json={
                "token": raw_token,
                "new_password": "w" * 4,
            },
        )
        for _ in range(11)
    ]

    assert [response.status_code for response in responses[:10]] == [422] * 10
    assert responses[10].status_code == 429


@pytest.mark.asyncio
async def test_reset_password_has_client_rate_limit(
    async_client, enabled_reset_limiter,
):
    responses = [
        await async_client.post(
            "/api/auth/reset-password",
            json={
                "token": f"invalid-{index}",
                "new_password": "<Synthetic-04c0u2-Limiter-Password>",
            },
        )
        for index in range(11)
    ]
    assert [response.status_code for response in responses[:10]] == [400] * 10
    assert responses[10].status_code == 429


@pytest.mark.asyncio
async def test_invalid_and_expired_reset_tokens_share_public_error(
    async_client, test_session, local_user,
):
    from auth.tokens import hash_token
    from models import PasswordResetToken

    raw_token = "expired-public-reset-credential"
    test_session.add(
        PasswordResetToken(
            user_id=local_user.id,
            token_hash=hash_token(raw_token),
            expires_at=datetime.utcnow() - timedelta(minutes=1),
        )
    )
    test_session.commit()

    expired = await async_client.post(
        "/api/auth/reset-password",
        json={
            "token": raw_token,
            "new_password": "<Synthetic-04c0u2-Expired-Password>",
        },
    )
    invalid = await async_client.post(
        "/api/auth/reset-password",
        json={
            "token": "unknown-reset-token",
            "new_password": "<Synthetic-04c0u2-Unknown-Password>",
        },
    )
    assert expired.status_code == invalid.status_code == 400
    assert expired.json() == invalid.json()


@pytest.mark.asyncio
async def test_concurrent_reset_only_one_request_consumes_token(
    async_client, test_session, local_user,
):
    from auth.tokens import hash_token
    from models import PasswordResetToken

    raw_token = "concurrent-reset-credential"
    test_session.add(
        PasswordResetToken(
            user_id=local_user.id,
            token_hash=hash_token(raw_token),
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
    )
    test_session.commit()

    responses = await asyncio.gather(
        *[
            async_client.post(
                "/api/auth/reset-password",
                json={
                    "token": raw_token,
                    "new_password": "<Synthetic-04c0u2-Race-Password>",
                },
            )
            for _ in range(2)
        ]
    )

    assert sorted(response.status_code for response in responses) == [200, 400]


def test_conditional_consume_rejects_stale_parallel_session(tmp_path):
    """Two workers may load the row, but only one conditional write can win."""
    from auth.routes import _consume_reset_token
    from auth.tokens import hash_token
    from models import Base, PasswordResetToken, User

    engine = create_engine(
        f"sqlite:///{tmp_path / 'consume-race.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    seed = sessions()
    user = User(
        username="race-user",
        email="race-user@example.com",
        auth_provider="local",
        is_active=True,
    )
    seed.add(user)
    seed.flush()
    token = PasswordResetToken(
        user_id=user.id,
        token_hash=hash_token("race-reset-token"),
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    seed.add(token)
    seed.commit()

    worker_one = sessions()
    worker_two = sessions()
    try:
        # Both workers establish the same precondition before either consumes.
        assert worker_one.get(PasswordResetToken, token.id).used_at is None
        assert worker_two.get(PasswordResetToken, token.id).used_at is None
        now = datetime.utcnow()
        assert _consume_reset_token(worker_one, token.id, now) is True
        worker_one.commit()
        assert _consume_reset_token(worker_two, token.id, now) is False
        worker_two.rollback()
    finally:
        worker_one.close()
        worker_two.close()
        seed.close()
        engine.dispose()


def test_migration_deduplicates_unused_tokens_and_adds_attempt_budget(tmp_path):
    from alembic import command
    from alembic.config import Config

    import database

    db_path = tmp_path / "migration.db"
    config = Config(str(database.ALEMBIC_INI_PATH))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(config, "0043")

    engine = create_engine(f"sqlite:///{db_path}")
    now = datetime.utcnow()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, username, auth_provider, is_active, is_admin, created_at, updated_at) "
                "VALUES (1, 'migration-user', 'local', 1, 0, :created, :created)"
            ),
            {"created": now},
        )
        connection.execute(
            text(
                "INSERT INTO password_reset_tokens "
                "(user_id, token_hash, expires_at, used_at, created_at) "
                "VALUES (1, :first_hash, :expires, NULL, :created), "
                "(1, :second_hash, :expires, NULL, :created)"
            ),
            {
                "first_hash": "migration-first",
                "second_hash": "migration-second",
                "expires": now + timedelta(hours=1),
                "created": now,
            },
        )
    engine.dispose()

    command.upgrade(config, "0044")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        columns = {
            column["name"]
            for column in inspect(engine).get_columns("password_reset_tokens")
        }
        indexes = {
            index["name"]: index
            for index in inspect(engine).get_indexes("password_reset_tokens")
        }
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT id, attempt_count FROM password_reset_tokens")
            ).all()
        assert columns >= {"attempt_count"}
        assert rows == [(2, 0)]
        assert indexes["uq_reset_token_unused_user"]["unique"] == 1
    finally:
        engine.dispose()

    command.downgrade(config, "0043")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        columns = {
            column["name"]
            for column in inspect(engine).get_columns("password_reset_tokens")
        }
        indexes = {
            index["name"]
            for index in inspect(engine).get_indexes("password_reset_tokens")
        }
        assert "attempt_count" not in columns
        assert "uq_reset_token_unused_user" not in indexes
    finally:
        engine.dispose()

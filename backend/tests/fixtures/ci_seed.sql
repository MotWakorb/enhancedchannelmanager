-- CI seed data for the ECM backend test suite.
--
-- This file is loaded by the `ci_seed_db` pytest fixture in conftest.py to
-- populate a SQLite database with deterministic reference rows. It is an
-- OPT-IN fixture — tests that use it must request it explicitly. The default
-- in-memory `test_engine` / `test_session` fixtures remain unchanged.
--
-- Keep the inserts minimal and rely on column defaults where practical so
-- schema drift does not silently break the seed. When adding new rows, use
-- primary keys >= 1000 to avoid colliding with rows created inside tests.

BEGIN TRANSACTION;

-- Representative task configuration (disabled so no scheduler will pick it up).
INSERT INTO scheduled_tasks (id, task_id, task_name, description, enabled,
                             schedule_type, send_alerts, alert_on_success,
                             alert_on_warning, alert_on_error, alert_on_info,
                             send_to_email, send_to_discord, send_to_telegram,
                             show_notifications, created_at, updated_at)
VALUES (1001, 'ci_seed_task', 'CI Seed Task', 'Deterministic seed row', 0,
        'manual', 0, 0, 0, 0, 0, 0, 0, 0, 0,
        '2026-01-01 00:00:00', '2026-01-01 00:00:00');

-- Representative journal entry for read-path tests that need one row.
INSERT INTO journal_entries (id, timestamp, category, action_type, entity_id,
                             entity_name, description, user_initiated)
VALUES (1001, '2026-01-01 00:00:00', 'channel', 'create', 1001,
        'CI Seed Channel', 'Seed row for CI tests.', 1);

-- Representative unread notification.
INSERT INTO notifications (id, type, title, message, read, source, created_at)
VALUES (1001, 'info', 'CI Seed Notification',
        'Seed row for CI tests.', 0, 'system',
        '2026-01-01 00:00:00');

COMMIT;

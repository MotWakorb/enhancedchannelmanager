"""bead enhancedchannelmanager-xztoc — the recovery CLI runs on ECM's one password policy.

THE DEFECT
----------

``backend/reset_password.py`` is the documented lockout-recovery tool (README
and ``docs/user_guide/settings/authentication.md`` both tell operators to run
it). It carried its own ``_check_password_strength`` demanding an uppercase
letter, a lowercase letter AND a digit: the composition rules
``backend/auth/password.py`` dropped deliberately under NIST 800-63B, and which
its module docstring calls out as absent. So the one path an operator reaches
for when they are locked out was the one path that refused passwords the web UI
and the API accept.

WHAT IS PINNED HERE
-------------------

The CLI's verdict, driven through the real ``cli_mode`` and ``interactive_mode``
against a real SQLite ``users`` table, not through the validator in isolation:

* a passphrase with no uppercase and no digit is ACCEPTED and the row really is
  rewritten (red before the fix: the old rules rejected it);
* everything ``auth.password.validate_password`` rejects is still rejected;
* ``--force`` still skips validation entirely;
* the CLI holds no second copy of the policy to drift from the first.

Nothing here asserts on the printed error text. The CLI reads only the boolean
half of ``PasswordValidationResult`` on purpose, so that no part of a password
can reach a printed string by data flow; what it prints instead is the constant
``PASSWORD_POLICY_HINT``, and that is asserted as a constant.
"""
import runpy
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

import reset_password
from auth.password import verify_password


CLI_USERNAME = "locked-out-operator"

# All lowercase, no digit, no symbol: valid under ECM's policy (21 characters,
# not on the breached list, does not contain the username) and rejected by
# every one of the three composition rules this bead removed.
NO_COMPOSITION_PHRASE = "correct horse battery"

# A second accepted shape, so the case above cannot pass by accident on length
# alone: still no digit, but mixed case.
MIXED_CASE_PHRASE = "Correct Horse Battery"

# The three things the shared policy really does refuse.
TOO_SHORT_VALUE = "abcdefg"
BREACHED_VALUE = "password"
CONTAINS_USERNAME_VALUE = f"prefix-{CLI_USERNAME}-suffix"

REJECTED_VALUES = [TOO_SHORT_VALUE, BREACHED_VALUE, CONTAINS_USERNAME_VALUE]


@pytest.fixture
def cli_conn():
    """A live connection carrying the one row the CLI updates.

    Raw DDL rather than ``models.Base.metadata`` because that is what the CLI
    actually meets: it opens ``/config/journal.db`` with plain SQLAlchemy Core
    and knows nothing about the ORM.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE users ("
            " id INTEGER PRIMARY KEY,"
            " username TEXT,"
            " email TEXT,"
            " password_hash TEXT,"
            " is_admin INTEGER,"
            " is_active INTEGER,"
            " auth_provider TEXT,"
            " updated_at TEXT)"
        ))
        conn.execute(
            text(
                "INSERT INTO users"
                " (id, username, email, password_hash, is_admin, is_active, auth_provider)"
                " VALUES (1, :username, 'operator@example.com', 'pre-existing-hash',"
                " 1, 1, 'local')"
            ),
            {"username": CLI_USERNAME},
        )
        conn.commit()
        yield conn


def _stored_hash(conn) -> str:
    row = conn.execute(
        text("SELECT password_hash FROM users WHERE username = :username"),
        {"username": CLI_USERNAME},
    ).fetchone()
    return row[0]


class TestCliMode:
    """``reset_password.py -u <user> -p <value>``, the non-interactive path."""

    @pytest.mark.parametrize(
        "value", [NO_COMPOSITION_PHRASE, MIXED_CASE_PHRASE], ids=["no-composition", "mixed-case"]
    )
    def test_accepts_what_the_shared_policy_accepts(self, cli_conn, value):
        """RED WITHOUT THE FIX for ``no-composition``.

        ``correct horse battery`` has no uppercase letter and no digit, so the
        CLI's old local rules exited 1 on it while ``POST
        /api/auth/reset-password`` accepted the same value. The assertion is on
        the stored hash, not on the exit status: the point of the CLI is that
        the account's password really changed.
        """
        before = _stored_hash(cli_conn)

        reset_password.cli_mode(cli_conn, CLI_USERNAME, value)

        after = _stored_hash(cli_conn)
        assert after != before
        assert verify_password(value, after)

    @pytest.mark.parametrize("value", REJECTED_VALUES)
    def test_rejects_what_the_shared_policy_rejects(self, cli_conn, value):
        """Too short, breached, and containing the username: all still refused.

        Without these the fix could have been "accept everything", which would
        pass the case above.
        """
        before = _stored_hash(cli_conn)

        with pytest.raises(SystemExit) as exit_info:
            reset_password.cli_mode(cli_conn, CLI_USERNAME, value)

        assert exit_info.value.code == 1
        assert _stored_hash(cli_conn) == before

    @pytest.mark.parametrize("value", REJECTED_VALUES)
    def test_force_still_skips_validation(self, cli_conn, value):
        """``--force`` is the operator's documented escape hatch and is unchanged."""
        before = _stored_hash(cli_conn)

        reset_password.cli_mode(cli_conn, CLI_USERNAME, value, force=True)

        after = _stored_hash(cli_conn)
        assert after != before
        assert verify_password(value, after)

    def test_an_unknown_username_is_still_refused(self, cli_conn):
        """Positive control on the fixture: the row lookup really runs."""
        with pytest.raises(SystemExit) as exit_info:
            reset_password.cli_mode(cli_conn, "no-such-operator", NO_COMPOSITION_PHRASE)

        assert exit_info.value.code == 1


class TestInteractiveMode:
    """The path the user guide leads with: ``docker exec -it ... reset_password.py``."""

    def _run(self, conn, typed_values):
        """Drive the prompt loop with a scripted sequence of typed values."""
        answers = iter(typed_values)
        with patch("builtins.input", return_value="1"), \
             patch.object(reset_password.getpass, "getpass", side_effect=lambda _prompt: next(answers)):
            reset_password.interactive_mode(conn)

    def test_accepts_a_passphrase_with_no_uppercase_and_no_digit(self, cli_conn):
        """RED WITHOUT THE FIX: the loop re-prompted forever on this value.

        The scripted answer list is exhausted after the confirm, so a CLI that
        rejected the passphrase and looped would raise ``StopIteration`` rather
        than pass.
        """
        before = _stored_hash(cli_conn)

        self._run(cli_conn, [NO_COMPOSITION_PHRASE, NO_COMPOSITION_PHRASE])

        after = _stored_hash(cli_conn)
        assert after != before
        assert verify_password(NO_COMPOSITION_PHRASE, after)

    def test_a_refused_value_reprompts_and_names_the_real_policy(self, cli_conn, capsys):
        """The operator is told what the rules ARE, which the old code never did.

        The hint is a constant, so it can be asserted verbatim without any part
        of the typed value being involved.
        """
        self._run(
            cli_conn,
            [TOO_SHORT_VALUE, NO_COMPOSITION_PHRASE, NO_COMPOSITION_PHRASE],
        )

        printed = capsys.readouterr().out
        assert reset_password.PASSWORD_POLICY_HINT in printed
        assert verify_password(
            NO_COMPOSITION_PHRASE, _stored_hash(cli_conn)
        )


class TestOnePolicyNotTwo:
    """The CLI must hold no second copy of the rules to drift from the first."""

    def test_the_cli_calls_the_shared_validator_itself(self):
        """Identity, not equivalence.

        A private re-implementation that happens to agree today would pass any
        behavioural test in this file and diverge the next time the shared
        policy changes: that divergence IS this bead. Same shape as
        ``test_jy006_auth_disabled_identity_primitives.py::
        test_both_halves_share_one_predicate``.
        """
        import auth.password as shared

        assert reset_password.validate_password is shared.validate_password
        assert reset_password.hash_password is shared.hash_password

    def test_no_composition_check_survives_in_the_cli(self):
        """The named helpers the old rules lived in are gone, not merely unused.

        ``_PASSWORD_ERRORS`` was already dead when this bead was filed (both
        modes computed a fail code and printed a generic line), so leaving it
        behind would have left the rules readable as policy in the file
        operators are pointed at.
        """
        assert not hasattr(reset_password, "_check_password_strength")
        assert not hasattr(reset_password, "_PASSWORD_ERRORS")

    def test_the_printed_hint_states_the_real_policy(self):
        """The hint is operator-facing text and the only place the rules are named."""
        hint = reset_password.PASSWORD_POLICY_HINT

        assert "8 characters" in hint
        assert "common or breached" in hint
        assert "username" in hint
        # It must not re-introduce the dropped rules as advice.
        assert "no uppercase, lowercase or digit requirements" in hint.lower()


def test_the_module_imports_without_the_app_directory():
    """``sys.path.insert(0, "/app")`` is for the container, not a requirement.

    The CLI is executed as ``python /app/reset_password.py``. Re-executing the
    module top to bottom proves the new ``from auth.password import ...`` line
    resolves in this checkout too, so a broken import cannot hide behind the
    module object pytest already had.
    """
    module = runpy.run_path(reset_password.__file__, run_name="not_main")

    assert module["validate_password"].__module__ == "auth.password"

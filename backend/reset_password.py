#!/usr/bin/env python3
"""
ECM Password Reset Utility

Reset a user's password from the command line.

Usage (interactive):
    docker exec -it enhancedchannelmanager python /app/reset_password.py

Usage (non-interactive):
    docker exec enhancedchannelmanager python /app/reset_password.py --username admin --password 'NewPass123'
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

# Ensure /app is in the path so we can import project modules
sys.path.insert(0, "/app")

import bcrypt
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool


# ── Colours ────────────────────────────────────────────────────────────
GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
BOLD = "\033[1m"
NC = "\033[0m"  # No Color


def get_db_path() -> Path:
    """Resolve the journal database path."""
    config_dir = Path(os.environ.get("CONFIG_DIR", "/config"))
    return config_dir / "journal.db"


def hash_password(password: str) -> str:
    """Hash a password with bcrypt (12 rounds)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def validate_password(password: str, username: str) -> str | None:
    """Validate password strength. Returns error message or None if valid."""
    if len(password) < 8:
        return "Password must be at least 8 characters long."
    if not any(c.isupper() for c in password):
        return "Password must contain at least one uppercase letter."
    if not any(c.islower() for c in password):
        return "Password must contain at least one lowercase letter."
    if not any(c.isdigit() for c in password):
        return "Password must contain at least one number."
    if username and username.lower() in password.lower():
        return "Password cannot contain your username."
    return None


def list_users(conn) -> list[dict]:
    """Fetch all users from the database."""
    result = conn.execute(text(
        "SELECT id, username, email, is_admin, is_active, auth_provider FROM users ORDER BY id"
    ))
    users = []
    for row in result.fetchall():
        users.append({
            "id": row[0],
            "username": row[1],
            "email": row[2],
            "is_admin": bool(row[3]),
            "is_active": bool(row[4]),
            "auth_provider": row[5],
        })
    return users


def reset_password(conn, username: str, new_hash: str) -> bool:
    """Update the password hash for a user. Returns True on success."""
    result = conn.execute(
        text("UPDATE users SET password_hash = :hash, updated_at = datetime('now') WHERE username = :username"),
        {"hash": new_hash, "username": username},
    )
    conn.commit()
    return result.rowcount > 0


def interactive_mode(conn, force: bool = False):
    """Run the interactive password reset flow."""
    print()
    print(f"{BLUE}{'═' * 56}{NC}")
    print(f"{BLUE}  ECM Password Reset Utility{NC}")
    print(f"{BLUE}{'═' * 56}{NC}")
    print()

    # List users
    users = list_users(conn)
    if not users:
        print(f"{RED}No users found in the database.{NC}")
        print(f"Create a user through the web UI first.")
        sys.exit(1)

    print(f"{BOLD}Existing users:{NC}")
    print()
    print(f"  {'#':<4} {'Username':<20} {'Email':<30} {'Admin':<7} {'Active':<8} {'Provider'}")
    print(f"  {'─'*4} {'─'*20} {'─'*30} {'─'*7} {'─'*8} {'─'*12}")
    for i, u in enumerate(users, 1):
        admin_str = f"{GREEN}Yes{NC}" if u["is_admin"] else "No"
        active_str = f"{GREEN}Yes{NC}" if u["is_active"] else f"{RED}No{NC}"
        print(f"  {i:<4} {u['username']:<20} {u['email'] or '—':<30} {admin_str:<16} {active_str:<17} {u['auth_provider']}")
    print()

    # Pick user
    while True:
        choice = input(f"Enter username or number (1-{len(users)}): ").strip()
        if not choice:
            continue

        # Try as number
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(users):
                target = users[idx]
                break
        except ValueError:
            pass

        # Try as username
        matches = [u for u in users if u["username"] == choice]
        if matches:
            target = matches[0]
            break

        print(f"{RED}User not found. Try again.{NC}")

    username = target["username"]

    if target["auth_provider"] != "local":
        print(f"\n{YELLOW}Warning:{NC} User '{username}' uses '{target['auth_provider']}' authentication.")
        print(f"Setting a local password will allow login with either method.")
        confirm = input("Continue? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            sys.exit(0)

    # Get new password
    print()
    while True:
        password = getpass.getpass(f"New password for '{username}': ")
        if not password:
            print(f"{RED}Password cannot be empty.{NC}")
            continue

        if not force:
            error = validate_password(password, username)
            if error:
                print(f"{RED}{error}{NC}")
                continue

        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print(f"{RED}Passwords do not match. Try again.{NC}")
            continue

        break

    # Reset
    new_hash = hash_password(password)
    if reset_password(conn, username, new_hash):
        print(f"\n{GREEN}Password for '{username}' has been reset successfully.{NC}")
    else:
        print(f"\n{RED}Failed to update password. User may have been deleted.{NC}")
        sys.exit(1)


def cli_mode(conn, username: str, password: str, force: bool = False):
    """Non-interactive password reset."""
    # Verify user exists
    result = conn.execute(
        text("SELECT id, username FROM users WHERE username = :username"),
        {"username": username},
    )
    user = result.fetchone()
    if not user:
        print(f"{RED}Error: User '{username}' not found.{NC}", file=sys.stderr)
        sys.exit(1)

    # Validate password
    if not force:
        error = validate_password(password, username)
        if error:
            print(f"{RED}Error: {error}{NC}", file=sys.stderr)
            sys.exit(1)

    # Reset
    new_hash = hash_password(password)
    if reset_password(conn, username, new_hash):
        print(f"{GREEN}Password for '{username}' has been reset successfully.{NC}")
    else:
        print(f"{RED}Failed to update password.{NC}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Reset a user's password in ECM.",
        epilog="Run without arguments for interactive mode.",
    )
    parser.add_argument("--username", "-u", help="Username to reset")
    parser.add_argument("--password", "-p", help="New password (omit to be prompted)")
    parser.add_argument("--force", "-f", action="store_true",
                        help="Skip password strength validation")
    args = parser.parse_args()

    # Connect to database
    db_path = get_db_path()
    if not db_path.exists():
        print(f"{RED}Error: Database not found at {db_path}{NC}", file=sys.stderr)
        print(f"Make sure ECM has been started at least once.", file=sys.stderr)
        sys.exit(1)

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.connect() as conn:
        if args.username:
            # Non-interactive (or semi-interactive if no password given)
            password = args.password
            if not password:
                password = getpass.getpass(f"New password for '{args.username}': ")
                confirm = getpass.getpass("Confirm password: ")
                if password != confirm:
                    print(f"{RED}Passwords do not match.{NC}", file=sys.stderr)
                    sys.exit(1)
            cli_mode(conn, args.username, password, force=args.force)
        else:
            interactive_mode(conn, force=args.force)


if __name__ == "__main__":
    main()

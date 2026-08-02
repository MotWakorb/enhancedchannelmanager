# User Management

> **Admin only.** This destination only appears in the Settings navigation for administrators; it does not render for non-admin operators.

User Management, under **Administration** in the Settings navigation, lists
every user account (username, email, sign-in provider, status, and role)
with Edit and Delete actions per row, plus a summary of active vs. inactive
accounts.

## Common tasks

### Create a user account

Account creation on this page requires **local authentication** to be
enabled first. See [Authentication](authentication.md).

1. Go to **Settings → User Management**.
2. Create the new account, setting its username, role, and initial
   password.

**Result:** The new account appears in the list with status Active, and
the active-account count increases by one.

### Change a user's role

1. Go to **Settings → User Management**.
2. Click **Edit** on the user's row.
3. Change their role.
4. Save.

**Result:** The Role column for that user updates immediately, and their
permissions change on their next request.

### Deactivate a user without deleting their account

1. Go to **Settings → User Management**.
2. Click **Edit** on the user's row.
3. Set their status to inactive.
4. Save.

**Result:** The user's Status shows inactive and they can no longer sign
in, but their account (and any history tied to it) is preserved. Use
**Delete** instead only if you actually want the account gone.

## Going deeper

- [Authentication](authentication.md): enabling local authentication, which User Management account creation depends on.
- [Linked Accounts](linked-accounts.md): how an individual operator links an external identity to their own account.

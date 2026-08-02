# Authentication

> **Admin only.** This destination only appears in the Settings navigation for administrators; it does not render for non-admin operators.

Authentication, under **Administration** in the Settings navigation,
controls instance-wide login requirements and which sign-in providers are
available: local username/password, and Dispatcharr SSO.

## Common tasks

### Require login for every operator

1. Go to **Settings → Authentication**.
2. Under **Global Settings**, check **Require Authentication**. When this
   is off, the application runs in open mode, with no login required for
   anyone.
3. Save.

**Result:** Anyone reaching ECM is now prompted to sign in before they can
use it.

### Turn on local username/password sign-in

1. Go to **Settings → Authentication**.
2. Under **Local Authentication**, check **Enable local authentication**.
3. Set **Minimum Password Length** (6–32 characters).
4. Save.

**Result:** Operators can now sign in with a local username and password
meeting the configured minimum length. Create and manage those accounts
under [User Management](user-management.md).

### Let operators sign in with their Dispatcharr credentials

1. Confirm the Dispatcharr URL is set correctly under [General
   Settings](general-settings.md) first. Dispatcharr SSO uses that same
   URL.
2. Go to **Settings → Authentication**.
3. Under **Dispatcharr SSO**, check **Enable Dispatcharr SSO**. This
   allows users to sign in using their Dispatcharr credentials.
4. Save.

**Result:** A Dispatcharr-credential sign-in option becomes available
alongside (or instead of) local authentication, depending on what else you
have enabled.

## Going deeper

- [User Management](user-management.md): creating and managing local user accounts.
- [Linked Accounts](linked-accounts.md): linking an external identity to your own account, as distinct from the instance-wide provider toggles here.
- [`docs/auth_middleware.md`](../../auth_middleware.md): how ECM's auth middleware evaluates these settings on every request.

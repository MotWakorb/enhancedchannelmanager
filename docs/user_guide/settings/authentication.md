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
have enabled. When both are enabled, a returning operator sees a provider
selector on the sign-in screen and picks **Local Account** or
**Dispatcharr** before entering credentials. With only one provider enabled,
that provider's sign-in form is shown directly and there's no selector to
pick from. Sessions renew themselves with an automatic token refresh, so a
signed-in operator isn't dropped back to the sign-in screen mid-session.

### Reset a forgotten local password

This only applies to local accounts. A Dispatcharr SSO account's password is
managed in Dispatcharr, not here.

**Via email**, if SMTP is configured (see [Notifications & Alert
Methods](../notifications/index.md#settings-notification-settings-smtp)):

1. On the local sign-in form, select **Forgot password?**.
2. Enter the account's **Email Address** and select **Send Reset Link**.
3. Open the emailed link, enter **New Password** and **Confirm Password**,
   then select **Reset Password**. The link is valid for one hour.

**Via the command line**, when an operator is locked out or SMTP isn't
configured:

```bash
# Interactive mode: lists users, prompts for everything
docker exec -it ecm-ecm-1 python /app/reset_password.py

# Non-interactive: specify username and password
docker exec ecm-ecm-1 python /app/reset_password.py -u admin -p 'NewPass123'

# Semi-interactive: specify username, prompt for password securely
docker exec -it ecm-ecm-1 python /app/reset_password.py -u admin

# Skip password strength validation
docker exec ecm-ecm-1 python /app/reset_password.py -u admin -p 'simple' --force
```

Interactive mode prints a table of every user with their username, email,
admin status, active status, and auth provider, so you can confirm you're
resetting the right account before you type a new password. Substitute your
own container name if it isn't `ecm-ecm-1`.

## Going deeper

- [User Management](user-management.md): creating and managing local user accounts.
- [Linked Accounts](linked-accounts.md): linking an external identity to your own account, as distinct from the instance-wide provider toggles here.
- [`docs/auth_middleware.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/auth_middleware.md): how ECM's auth middleware evaluates these settings on every request.

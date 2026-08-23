# TLS Certificates

> **Admin only.** This destination only appears in the Settings navigation for administrators; it does not render for non-admin operators.
>
> **No screenshot on this page.** This page can hold real cloud-provider credentials (an AWS access key and secret, or a DNS API token, depending on your DNS provider choice) once configured, and a screenshot of a configured instance risks exposing them. Everything below is described in prose instead.

TLS Certificates, under **Administration** in the Settings navigation, is a
single-section page (no "On this page" rail) that configures HTTPS:
automatic certificates via Let's Encrypt, or a manually uploaded
certificate. The page shows a **Current Status** banner (encrypted or not,
and which port) at the top.

Enabling TLS **restarts ECM**. Because this instance is shared, this
walkthrough was verified by reading the UI's fields and labels, not by
actually enabling TLS. Doing so would have changed the port every other
writer working against this instance was using.

## Common tasks

### Enable HTTPS with an automatic Let's Encrypt certificate (DNS-01)

1. Go to **Settings → TLS Certificates**.
2. Check **Enable TLS/HTTPS** and choose **Certificate Mode: Let's Encrypt
   (Automatic)**.
3. Fill in **Domain Name** (must point at this server), **HTTPS Port**
   (default 6143; HTTP stays on its own configured port, default 6100, as
   a fallback), and **Email Address** (for Let's Encrypt renewal
   notifications).
4. Choose a **DNS Provider** for automatic TXT record management:
   Cloudflare or AWS Route53, or Manual/Other if you'll create the TXT
   record yourself when prompted. Route53 requires an AWS access key, secret,
   and region; Cloudflare requires its own API credential. Click **Test DNS
   Provider** to confirm your credentials work before requesting a
   certificate.
5. Optionally check **Use Staging Environment** first. This issues an
   untrusted test certificate from Let's Encrypt's staging server, useful
   for confirming the DNS-01 flow works before spending against your
   production rate limit.
6. Follow the on-page two-step flow: **1. Save Settings**, then **2.
   Request Certificate**.

**Result:** ECM restarts. The **Current Status** banner shows Encrypted and
the configured HTTPS port. HTTP remains available on its fallback port.

### Upload a manual certificate instead

1. Go to **Settings → TLS Certificates**.
2. Check **Enable TLS/HTTPS** and choose **Certificate Mode: Manual
   Certificate Upload**.
3. Upload your certificate and key. You're responsible for renewal. There's
   no Let's Encrypt auto-renew for a manually uploaded certificate.
4. Save.

**Result:** ECM restarts and serves the uploaded certificate on the
configured HTTPS port.

### Renew or remove a certificate

1. Go to **Settings → TLS Certificates**.
2. Click **Renew Certificate** to request a fresh one ahead of schedule, or
   **Delete Certificate** to remove it.

**Result:** A renewed certificate replaces the current one without
changing your configuration. Deleting a certificate is destructive. You'll
need to request or upload a new one before HTTPS works again.

## Going deeper

- [Backup & Restore](../backup-restore/index.md): no ECM backup format copies your TLS certificate or private key. Copy `/config/tls` yourself, or plan to reissue the certificate, before migrating to a new host.
- [`docs/architecture.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/architecture.md): the dual-port (HTTP fallback + HTTPS) model this page configures.

# Configure Cloud Destinations

> **Status:** Shipped in v0.18.0. **S3 (including S3-compatible), Google Drive, and WebDAV are fully shipped. Dropbox and OneDrive adapters exist in the codebase but are deferred. See the per-provider notes below.**

---

## Why configure a cloud destination

A local backup protects you from accidental ECM misconfiguration or data loss within your host. It does not protect you from host failure, disk failure, or container loss. Configuring a cloud destination sends each backup artifact to durable, off-host storage automatically after every successful backup run.

You can configure multiple cloud destinations. Each destination applies the retention policy independently: a failed upload to one destination does not prune backups at another destination, and a failed upload never prunes the local copy.

---

## All providers: common SSRF security behavior

Every cloud destination URL passes through ECM's SSRF (Server-Side Request Forgery) chokepoint before any connection is made. This is a non-bypassable security control:

- **Always-blocked addresses** (in both LAN-friendly and public-only mode): loopback (`127.x.x.x`), link-local (`169.254.x.x`, which includes the AWS/cloud metadata endpoint), CGNAT (`100.64.x.x/10`), multicast, IPv6 ULA/link-local, and non-http(s) URL schemes.
- **LAN-friendly mode (default):** RFC 1918 private addresses (`192.168.x.x`, `10.x.x.x`, `172.16-31.x.x`) are allowed. Use this when your WebDAV server or S3-compatible endpoint is on a local NAS.
- **Public-only mode:** RFC 1918 addresses are blocked. Switch to this mode if your threat model requires it: Settings → Backup & Restore → "Where backups can be sent".

DNS-rebinding is mitigated by resolving the endpoint hostname before connecting and then connecting by the resolved IP address, not by re-resolving at connection time.

---

## S3 and S3-compatible (MinIO, Backblaze B2)

**Status: Shipped (v0.18.0)**

The S3 adapter supports AWS S3, MinIO, and Backblaze B2. Any service that implements the S3 API is supported via a custom endpoint URL.

### Required credentials

| Field | Description |
|-|-|
| **Bucket name** | The S3 bucket to upload artifacts to. |
| **Access key ID** | AWS/MinIO/B2 access key. |
| **Secret access key** | AWS/MinIO/B2 secret key. |

### Optional credentials

| Field | Description |
|-|-|
| **Endpoint URL** | Custom endpoint for MinIO, B2, or any S3-compatible service. Omit for AWS (the standard AWS regional endpoints are used). |
| **Region** | AWS region (default: `us-east-1`). Ignored for custom endpoint services. |

### Security notes

- A custom `endpoint_url` is pre-resolved and validated through the SSRF denylist before the S3 client is built. An endpoint pointing at `169.254.169.254` (the cloud-metadata service) is refused before any socket opens.
- For AWS, the standard regional endpoints are public AWS infrastructure and are not operator-influenced, so they are not pre-resolved.

### Setup walkthrough

1. Go to **Settings → Backup & Restore → Cloud Destinations**.
2. Click **Add destination**.
3. Select **S3 / S3-compatible**.
4. Fill in the required fields.
5. Click **Test connection** to confirm ECM can reach the bucket.
6. Click **Save**.

On the next scheduled backup, ECM uploads the artifact to this bucket and verifies the upload before applying the retention policy.

---

## WebDAV

**Status: Shipped (v0.18.0)**

The WebDAV adapter works with any RFC 4918 WebDAV server: Nextcloud, ownCloud, Apache `mod_dav`, `rclone serve webdav`, a NAS's built-in WebDAV service, and others.

### Required credentials

| Field | Description |
|-|-|
| **Base URL** | The base URL of the WebDAV endpoint (e.g., `http://192.168.1.50/webdav/backups`). |
| **Username** | Basic auth username (leave blank if the server requires no auth). |
| **Password** | Basic auth password. |

### Optional

| Field | Description |
|-|-|
| **Allow insecure TLS** | Disable TLS certificate verification. Use only on isolated LANs where you control both endpoints. Every upload using this setting is logged. |

### Security notes

- The base URL is validated through the SSRF chokepoint before any connection is made.
- Uploads are streamed from disk. The artifact is never read whole into RAM.
- The `Authorization` header value is masked before logging.

### Setup walkthrough

1. Go to **Settings → Backup & Restore → Cloud Destinations**.
2. Click **Add destination**.
3. Select **WebDAV**.
4. Enter the base URL and optional credentials.
5. Click **Test connection**.
6. Click **Save**.

---

## Google Drive

**Status: Shipped (v0.18.0)**

The Google Drive adapter uses service account (app-only) authentication.

### Required credentials

| Field | Description |
|-|-|
| **Service account key** | The JSON service account key file contents from the Google Cloud Console. |
| **Folder ID** | The Google Drive folder ID where artifacts will be uploaded (from the folder's URL). |

### Setup notes

- Create a service account in the Google Cloud Console, enable the Drive API, and download a JSON key file.
- Share the target folder with the service account's email address (give it Editor access).
- Paste the full contents of the JSON key file into the **Service account key** field.

### Setup walkthrough

1. Go to **Settings → Backup & Restore → Cloud Destinations**.
2. Click **Add destination**.
3. Select **Google Drive**.
4. Paste the service account JSON and fill in the folder ID.
5. Click **Test connection**.
6. Click **Save**.

---

## OneDrive / SharePoint

**Status: Code exists but DEFERRED in v0.18.0. Configuring an OneDrive target will produce a per-target failure on each backup run without uploading anything.**

OneDrive support using the Microsoft Graph API (client credentials OAuth2 flow) is planned but not wired for upload in v0.18.0. The adapter code is present (`backend/cloud_storage/onedrive_adapter.py`) and will be connected in a follow-up release.

Do not configure an OneDrive cloud destination in v0.18.0. If you have one configured, remove it or switch to a different provider until this is resolved.

---

## Dropbox

**Status: Code exists but DEFERRED in v0.18.0. Configuring a Dropbox target will produce a per-target failure on each backup run without uploading anything.**

Dropbox support is planned but not wired for upload in v0.18.0. The adapter code is present (`backend/cloud_storage/dropbox_adapter.py`) and will be connected in a follow-up release.

Do not configure a Dropbox cloud destination in v0.18.0. If you have one configured, remove it or switch to a different provider until this is resolved.

---

## Retention at cloud destinations

After a verified-successful upload to a cloud destination, ECM applies the same retention policy used for local backups:

- Keep the newest N backups (default: 7).
- Additionally prune any backup beyond the newest N that is older than the maximum age (default: 30 days).

Retention is applied independently per cloud destination. A failed upload to one destination does not prune artifacts there (no pruning without a verified-successful new upload).

---

## Testing and troubleshooting

- **Test connection button**: use this before saving a new destination. It confirms ECM can reach the endpoint and authenticate.
- **Task history**: after a backup run, check the DBAS Backup task card's own **History** expander under **Settings → Scheduled Tasks** for per-destination upload results. There is no separate "Task History" destination in Settings; run history lives per-task.
- **Notifications**: a failure notification is emitted when an upload fails. The notification includes the destination name (never the URL or credentials).

See [Troubleshoot a restore](troubleshoot-restore.md) for further diagnostic patterns.

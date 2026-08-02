# Install ECM

ECM ships as a Docker image and is installed with Docker Compose. This article
covers the compose file, the one setting that determines whether your data
survives a container rebuild, and how to confirm the container is up.

## Before you start

- **Docker and Docker Compose** installed on the host that will run ECM.
- **A running Dispatcharr instance** ECM can reach over the network.
  Dispatcharr is a separate application; ECM is a management layer in front
  of it and has nothing to manage until it can connect to one. See
  [Connect ECM to Dispatcharr](connect-dispatcharr.md) for the connection
  step itself, once ECM is up.
- **Network reachability** between the two: whatever host and port ECM runs
  on must be able to reach Dispatcharr's URL (for example
  `http://dispatcharr.local:9191`), whether that's localhost, a Docker
  network, or a LAN address.

## Common tasks

### Install ECM with Docker Compose

1. Create a directory for ECM (for example `~/ecm/`) and, inside it, a
    `docker-compose.yml` file with the following:

    ```yaml
    services:
      ecm:
        image: ghcr.io/motwakorb/enhancedchannelmanager:latest
        ports:
          - "6100:6100"   # HTTP (configurable via ECM_PORT)
          - "6143:6143"   # HTTPS (configurable via ECM_HTTPS_PORT)
        volumes:
          - ./config:/config
        environment:
          - PUID=1000
          - PGID=1000
          - ECM_PORT=6100
          - ECM_HTTPS_PORT=6143
    ```

    Set `PUID`/`PGID` to match the user who should own the files under
    `./config` on the host. Run `id your_user` to find them.

2. From that directory, start the container:

    ```bash
    docker compose up -d
    ```

3. Open `http://<host>:6100` in a browser once the container reports healthy
    (see [Confirm the container is up](#confirm-the-container-is-up) below).

**Result:** ECM's setup wizard loads in the browser. See [First run](first-run.md)
for what happens next.

### Give ECM a place to keep its data

The single most consequential line in the compose file above is
`- ./config:/config`. Everything ECM stores (settings, the channel
database, uploaded logos, TLS certificates, backups) lives under `/config`
inside the container. **If that path isn't backed by a bind mount or a
named volume, all of it lives in the container's writable layer and is
destroyed the next time the container is removed or recreated.** This
includes routine "update to latest" flows in Portainer, Unraid, or
Watchtower, and `docker compose up --force-recreate`.

1. Decide where `/config` should live on the host: a bind mount
   (`./config:/config`, as in the compose file above) or a named volume
   (`ecm-config:/config`, with a matching top-level `volumes:` entry). Either
   is durable; a bind mount is easier to browse and back up directly.
2. Start (or recreate) the container with that mount in place.
3. If you're recreating a container that already holds data you care about,
   take a backup first (**Settings → Backup & Restore**) before recreating
   without confirming the mount. ECM's own startup check (next section)
   only warns after the fact.

**Result:** the container's own preflight output confirms the mount. See
[What the preflight checks tell you](first-run.md#what-the-preflight-checks-tell-you)
for the exact line to look for, and what it means if you see the warning
instead.

### Confirm the container is up

1. Check the container's logs for the preflight banner and the "All
    preflight checks passed!" line:

    ```bash
    docker compose logs -f ecm
    ```

2. Once it's running, hit the health endpoint from the host:

    ```bash
    curl http://localhost:6100/api/health
    ```

**Result:** a healthy container returns something like:

```json
{"status":"healthy","service":"enhanced-channel-manager","version":"0.18.1-0005","release_channel":"dev","git_commit":"33042bfd..."}
```

This exact response (fields and shape) was captured from a running ECM
instance. `/api/health` is intentionally public. It needs no credentials,
which is what makes it usable for external health checks and the Docker
`HEALTHCHECK` the image ships with.

## Going deeper

- [First run](first-run.md): what you'll see the first time you load the UI, and how the preflight checks and config-persistence warning work.
- The project's root `README.md` (in the repository, not part of this published guide): the full compose reference, including the optional MCP server overlay and `PUID`/`PGID`/port environment variables.
- [Troubleshooting](../troubleshooting/index.md): if the container fails to start or exits immediately after `docker compose up`, this is where the dedicated `container-wont-start.md` article will live once written (tracked separately; not yet published).
- [`docs/runbooks/`](../../runbooks/README.md): operational runbooks for post-install incidents (SLO breaches, rollback procedures).

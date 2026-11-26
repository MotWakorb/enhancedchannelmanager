# Runbook: Container Preflight Failure (Python Interpreter, Packages, Launcher)

> The ECM container prints a failed startup check and exits instead of listening. This runbook covers the interpreter failures, the pre-0.16.0 `PATH` inheritance trap, and the supported override path.

- **Severity**: P2 (ECM is down; no data is lost by the failure itself)
- **Owner**: Operator / SRE
- **Last reviewed**: 2026-11-26
- **Related beads**: `enhancedchannelmanager-mevqr`, `enhancedchannelmanager-0oi96` (interpreter pin), `enhancedchannelmanager-wpzo6` (launcher check, override warning)

---

## Alert / Trigger

- **Manual trigger**: the container exits right after start, restarts in a loop, or an update or recreate in Portainer, Unraid or Watchtower left ECM stopped.
- **Log pattern**: the container log ends with `✗ Preflight checks failed! See errors above.` The exit status is 1.
- The container health check never turns healthy, because the app never starts.

---

## Symptoms

- `docker ps -a` shows `ecm-ecm-1` as `Exited (1)` or `Restarting`.
- The web UI and `/api/health` do not answer.
- The log shows a `✗` line from the "Checking Python environment..." or "Checking application modules..." sections, followed by an `Interpreter diagnostics:` block.
- Typical field report: `✓ Python 3.12.x found`, then `✗ Required Python packages missing from ...`, then `ModuleNotFoundError: No module named 'fastapi'`.

Port-in-use lines (`! Port 6100 (HTTP) is already in use`) and the `DATA IS NOT PERSISTENT` warning are advisory. They never stop startup, so they are not the cause of an exit. If the warning is present, read [Before you fix anything](#before-you-fix-anything) first.

---

## Before you fix anything

**Check whether this container has a config mount before you recreate it.** If the log (or Step 1 below) shows `DATA IS NOT PERSISTENT`, everything ECM stores is in this container's writable layer, and removing or recreating the container destroys it. Do not remove it yet. Stopping it does not delete that data. Follow [Recover data from a container with no config mount](../user_guide/backup-restore/recover-an-unmounted-container.md) first. It copies the whole `/config` directory out of the stopped container with `docker cp`, verifies the copy, and mounts that copy at `/config` when you recreate the container. Every recreate in the Resolution section below is that page's Step 7, so apply the fix there, with the mount in place. A crash-looping container needs no running ECM for any of this.

---

## Diagnosis

1. Read the end of the log and the container's mounts and `PATH`:

   ```bash
   docker logs --tail 80 ecm-ecm-1
   docker inspect ecm-ecm-1 --format '{{range .Mounts}}{{.Destination}}{{"\n"}}{{end}}'
   docker inspect ecm-ecm-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(PATH|ECM_PYTHON|ECM_UVICORN|ECM_VERSION)='
   ```

   Expected on a healthy container: `/config` appears in the mounts list, and `PATH` starts with `/opt/venv/bin:`. The last command prints only `PATH`, the two overrides and `ECM_VERSION`. It does not print secrets.

2. Find the first `✗` line and match it:

   | First `✗` line | What it means | Go to |
   |-|-|-|
   | `✗ Python 3 not found (tried: <path>)` | The resolved interpreter did not run. | Step 3 |
   | `✗ Required Python packages missing from <path>` | That interpreter runs but cannot import `fastapi` and `uvicorn`. | Step 3 |
   | `✗ Uvicorn launcher not executable: <path>` | The launcher the container execs is missing or not executable. | Step 3 |
   | `✗ Application module failed to load` | The interpreter is fine but `import main` raised. The traceback is printed right below it. | Step 4 |
   | `✗ Config directory is not writable` | `/config` cannot be written by the PUID/PGID user. Not an interpreter problem. | Step 5 |
   | `✗ MCP_SECRETS_DIR ...` or `✗ MCP credential projection ...` | The MCP secrets directory is wrong. Not an interpreter problem. | Step 5 |

3. Read the `Interpreter diagnostics:` block. The container prints it on every interpreter failure. The paths below are illustrative: your values will differ (on the current Alpine image the system interpreter is `/usr/local/bin/python3`, which is Python 3.12):

   ```text
   → Interpreter diagnostics:
   →   resolved python : /usr/local/bin/python3
   →   resolved uvicorn: /opt/venv/bin/uvicorn (NOT EXECUTABLE — the app launch would fail here too)
   →   sys.prefix      : /usr/local
   →   PATH            : /usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
   →   /opt/venv/bin/python: MISSING — the image virtualenv is not where ECM expects it
   ```

   Read it line by line:

   - **`sys.prefix` is `/opt/venv`**: the right interpreter ran, so the virtualenv itself is damaged or the packages are missing. Pull a fresh image and recreate.
   - **`sys.prefix` is `/usr/local` (or another system path)**: the container fell back to a system interpreter that has none of ECM's packages. Continue below to find out why it fell back.
   - **`/opt/venv/bin/python: MISSING`**: the pinned default is not executable, so the script fell back to a `PATH` lookup. Something is shadowing `/opt/venv`, usually a bind mount over it, or this is a custom image without the virtualenv.
   - **`/opt/venv/bin/python: present` but `resolved python` is something else**: an `ECM_PYTHON` override is in play. Either it points at an executable that lacks ECM's packages (the override was used), or it was not executable and was discarded. A discarded override prints `! ECM_PYTHON=<value> is not executable — falling back to a PATH lookup` earlier in the log.
   - **`resolved uvicorn: ... (NOT EXECUTABLE ...)`**: the launcher is missing. The same applies to an `ECM_UVICORN` override, which also prints a `! ECM_UVICORN=... is not executable` warning when it is discarded.

4. For `✗ Application module failed to load`, the full Python traceback is in the log above the diagnostics block. That is an application or dependency fault, not an environment fault. Capture the log, check whether the failure began at an image update, and pin the previous image tag in your compose file while you report it.

5. For `Config directory is not writable`: set `PUID` and `PGID` to the owner of the host directory behind your `/config` bind mount (`id <your_user>` prints them), then recreate. ECM runs `chown -R` on `/config` at startup, so a read-only mount or a host filesystem that refuses ownership changes (some NAS shares) is the usual cause.

6. Decide whether `PATH` inheritance is the cause. Check the `PATH=` line printed in step 1:

   - **It does not start with `/opt/venv/bin`**, and the container was created before 0.16.0 (an `ECM_VERSION` of `0.8.x` in its environment is a sign): this is the inherited-`PATH` trap below.
   - **It does start with `/opt/venv/bin`**, but `/opt/venv/bin/python` is `MISSING`: look for a mount or custom image that removes the virtualenv.

### Why `PATH` is inherited from before 0.16.0

ECM's Python packages live in the image virtualenv at `/opt/venv`. That virtualenv was introduced in 0.16.0, and the image puts `/opt/venv/bin` at the front of `PATH`. An "update" or "recreate" in Portainer, Unraid or Watchtower builds the new container from the new image, but copies the old container's environment onto it. A container first created before 0.16.0 carries a `PATH` without `/opt/venv/bin`, and that value overrides the image's. On builds before 0.18.1 the entrypoint found `python3` and `uvicorn` through `PATH`, so it ran the system interpreter and failed with `Required Python packages missing`.

From 0.18.1 the entrypoint no longer depends on `PATH` for this. It pins `/opt/venv/bin/python` and `/opt/venv/bin/uvicorn` by absolute path, and only falls back to `PATH` when those are not executable. On 0.18.1 or later, an inherited old `PATH` alone does not cause this failure. If you still see it on 0.18.1 or later, look at the other causes in step 3.

---

## Resolution

**Mitigate first.** Pick the branch that matches step 3.

Branches 1 to 4 each end with a recreate, and removing the container destroys everything ECM stored in it if `/config` has no mount. If Diagnosis step 1 showed no `/config` mount, do not run a recreate command from this section yet. First complete [steps 1 to 6 of Recover data from a container with no config mount](../user_guide/backup-restore/recover-an-unmounted-container.md#step-1-confirm-the-container-has-no-config-mount) (stop the container, copy the whole `/config` directory out, verify the copy, mount it). Then make the change below as part of that page's Step 7. When `/config` is already mounted, run the branch as written.

1. **Old build (before 0.18.1) with an inherited `PATH`.** Update to an image at 0.18.1 or later and recreate the container from your compose file instead of your update tool's in-place update, so it takes the image's own environment:

   ```bash
   docker compose pull ecm
   docker compose up -d --force-recreate ecm
   ```

   Only do this once `/config` is mounted. If it is not, stop and use [Before you fix anything](#before-you-fix-anything), which ends with this recreate as its Step 7. The service is named `ecm` in the repository's `docker-compose.yml`. Use your own service name if it differs.

2. **Something shadows `/opt/venv`.** Remove the bind mount or volume that targets `/opt/venv` from the container definition and recreate. The packages are in the image, so that directory needs no mount.

3. **A wrong `ECM_PYTHON` or `ECM_UVICORN` override.** Remove the variable from the compose `environment:` block (or the container's environment settings) and recreate. If you must keep an override, point it at an executable file that exists inside the container. Check with:

   ```bash
   docker run --rm --entrypoint /bin/sh <your-image> -c 'ls -l /opt/venv/bin/python /opt/venv/bin/uvicorn'
   ```

   Expected: both files are listed and executable.

4. **Custom image with its virtualenv elsewhere.** This is the supported use of the overrides. Set both, to executables inside the image:

   ```yaml
   environment:
     - ECM_PYTHON=/path/to/venv/bin/python
     - ECM_UVICORN=/path/to/venv/bin/uvicorn
   ```

   The interpreter you name must be able to `import fastapi, uvicorn` and `import main` from `/app`. See [README: Startup and interpreter variables](../../README.md#startup-and-interpreter-variables-advanced) for the full contract. Do not use the overrides to work around a broken official image. Report that instead.

5. Verify:

   ```bash
   docker logs --tail 60 ecm-ecm-1
   ```

   Expected: `✓ Python <version> found at /opt/venv/bin/python`, `✓ Uvicorn launcher at /opt/venv/bin/uvicorn`, and `All preflight checks passed!`. Then check readiness:

   ```bash
   docker exec ecm-ecm-1 python3 -c "
   import json, os, urllib.error, urllib.request
   port = os.environ.get('ECM_PORT', '6100')
   try:
       response = urllib.request.urlopen(f'http://localhost:{port}/api/health/ready', timeout=5)
   except urllib.error.HTTPError as error:
       response = error
   print(response.status)
   print(json.dumps(json.load(response), indent=2))
   "
   ```

   Expected: `200` and `"status": "ready"`. The image ships no `curl`, which is why this uses `python3`. If the container is not running, there is nothing to exec into. Read the log instead.

If any step fails, **stop** and escalate. Do not remove the container to "try again" while `/config` has no mount. If you are in that state, the safe exit is the copy-first procedure: [Recover data from a container with no config mount](../user_guide/backup-restore/recover-an-unmounted-container.md). It needs only a stopped container and `docker cp`, so it works however the startup failure presents.

---

## Escalation

If the container still exits after the branch that matched your diagnosis:

- Open an issue (or ask the maintainer) and include the full output of `docker logs ecm-ecm-1`, the image tag or digest, the output of the step 1 commands, and whether the container was created before 0.16.0.
- Do not paste secrets. The step 1 environment command prints only `PATH`, `ECM_PYTHON`, `ECM_UVICORN` and `ECM_VERSION`.

---

## Post-incident

- [ ] Confirm `/config` is on a mounted volume (`✓ Config directory /config is on a mounted volume`) so the next recreate cannot destroy data.
- [ ] Remove any `ECM_PYTHON` or `ECM_UVICORN` override you added while debugging, unless you run a custom image.
- [ ] If your update tool copies the old environment onto each recreate, recreate once from your compose file so the stale `PATH` is gone for good.
- [ ] Update this runbook with anything that was unclear or missing.

---

## References

- `backend/entrypoint.sh`: interpreter resolution, `print_python_diagnostics`, `check_python`, `check_application`.
- [README: Startup and interpreter variables](../../README.md#startup-and-interpreter-variables-advanced) for the variable contract.
- [First run: what the preflight checks tell you](../user_guide/getting-started/first-run.md#what-the-preflight-checks-tell-you)
- [Common issues: the container won't start](../user_guide/troubleshooting/common-issues.md#the-container-wont-start)
- [Recover data from a container with no config mount](../user_guide/backup-restore/recover-an-unmounted-container.md)
- [Disaster Recovery (ECM Configuration Restore)](./disaster-recovery-restore.md)

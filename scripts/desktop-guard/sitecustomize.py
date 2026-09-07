"""Audit-only Python process egress fence, loaded before ECM imports."""
import sys
import os
import json


def guard(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "socket.sendto", "subprocess.Popen", "os.system", "os.posix_spawn"):
        with open(os.environ["AUDIT_GUARD_LOG"], "a") as log:
            log.write(json.dumps({"event": event, "args": str(args)[:500]}) + "\n")
        raise PermissionError("ECM audit denies outbound networking and subprocesses")


sys.addaudithook(guard)
os.environ["AUDIT_GUARD_ACTIVE"] = "1"

"""Bounded literal LF unified-hunk replay, not a shell/patch program runner.

Purpose-bound to reviewed source witnesses: exact input/output positions, every
context byte, and complete before/after file hashes. No search, fuzz, offsets,
line-ending normalization, binary patches, deletion or no-newline extensions.
Callers separately authenticate original archives, policy and entire patch bytes.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

from alpine_source_inputs import InputError, require


def lines(data: bytes) -> list[bytes]:
    parts = data.split(b"\n")
    return [part + b"\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def replay_patch(
    files: dict[str, bytes], patch: bytes, witnesses: list[dict]
) -> dict[str, bytes]:
    require(
        isinstance(patch, bytes)
        and 0 < len(patch) <= 1024**2
        and patch.endswith(b"\n")
        and b"\r" not in patch
        and b"\0" not in patch
        and b"\\ No newline at end of file" not in patch,
        "reviewed bounded literal LF source patch required",
    )
    require(
        isinstance(files, dict)
        and isinstance(witnesses, list)
        and 0 < len(witnesses) <= 100,
        "complete source patch files/witnesses required",
    )
    expected = {}
    for witness in witnesses:
        require(
            isinstance(witness, dict)
            and set(witness) == {"path", "before_sha256", "after_sha256"},
            "complete source patch witness fields required",
        )
        name = witness["path"]
        require(
            isinstance(name, str)
            and name not in {"", ".", ".."}
            and "\\" not in name
            and str(PurePosixPath(name)) == name
            and not PurePosixPath(name).is_absolute()
            and ".." not in PurePosixPath(name).parts
            and not any(character.isspace() for character in name)
            and name not in expected,
            "unique safe source patch witness path required",
        )
        before, after = witness["before_sha256"], witness["after_sha256"]
        require(
            (
                before is None
                or (
                    isinstance(before, str)
                    and re.fullmatch(r"[0-9a-f]{64}", before) is not None
                )
            )
            and isinstance(after, str)
            and re.fullmatch(r"[0-9a-f]{64}", after) is not None
            and before != after,
            "complete distinct source patch witness hashes required",
        )
        expected[name] = witness
    source_lines = lines(patch)
    result = {}
    index = 0
    while index < len(source_lines):
        line = source_lines[index]
        if not line.startswith(b"--- "):
            require(
                not line.startswith((b"+++ ", b"@@ ")),
                "orphan source patch header/hunk",
            )
            index += 1
            continue
        require(
            index + 2 < len(source_lines)
            and source_lines[index + 1].startswith(b"+++ "),
            "complete source patch file header pair required",
        )
        old_tokens = line[4:].split()
        new_tokens = source_lines[index + 1][4:].split()
        require(
            old_tokens and new_tokens, "nonempty source patch header paths required"
        )
        old_path = old_tokens[0]
        new_path = new_tokens[0]
        require(
            new_path.startswith(b"b/"), "explicit source-relative patch output required"
        )
        try:
            name = new_path[2:].decode()
        except UnicodeError as exc:
            raise InputError("invalid source patch path encoding") from exc
        require(
            name in expected and name not in result,
            "source patch touches an unbound or repeated file",
        )
        witness = expected[name]
        if witness["before_sha256"] is None:
            require(
                old_path == b"/dev/null" and name not in files,
                "source patch new-file preimage must be absent",
            )
            original = b""
        else:
            require(
                old_path == b"a/" + name.encode()
                and isinstance(files.get(name), bytes),
                "explicit original source patch preimage required",
            )
            original = files[name]
            require(
                hashlib.sha256(original).hexdigest() == witness["before_sha256"],
                "complete source patch preimage hash differs",
            )
        old_lines = lines(original)
        output = []
        cursor = 0
        index += 2
        require(
            source_lines[index].startswith(b"@@ "),
            "literal source patch hunks required",
        )
        while index < len(source_lines) and source_lines[index].startswith(b"@@ "):
            header = re.fullmatch(
                rb"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[^\n]*\n",
                source_lines[index],
            )
            require(header is not None, "literal source patch hunk header required")
            old_start, old_count, new_start, new_count = (
                int(value) if value is not None else 1 for value in header.groups()
            )
            old_position = old_start if old_count == 0 else old_start - 1
            new_position = new_start if new_count == 0 else new_start - 1
            require(
                old_count + new_count > 0 and cursor <= old_position <= len(old_lines),
                "exact ordered source patch input hunk position required",
            )
            output.extend(old_lines[cursor:old_position])
            require(
                len(output) == new_position,
                "exact source patch output hunk position differs",
            )
            index += 1
            old, new = [], []
            while len(old) < old_count or len(new) < new_count:
                require(index < len(source_lines), "truncated source patch hunk")
                body = source_lines[index]
                require(
                    body[:1] in {b" ", b"+", b"-"},
                    "literal source patch hunk body required",
                )
                if body[:1] != b"+":
                    old.append(body[1:])
                if body[:1] != b"-":
                    new.append(body[1:])
                require(
                    len(old) <= old_count and len(new) <= new_count,
                    "exact source patch hunk line counts required",
                )
                index += 1
            require(
                old_lines[old_position : old_position + old_count] == old,
                "literal source patch context differs; no fuzz/offset permitted",
            )
            output.extend(new)
            cursor = old_position + old_count
        output.extend(old_lines[cursor:])
        data = b"".join(output)
        require(
            hashlib.sha256(data).hexdigest() == witness["after_sha256"],
            "complete replayed source patch postimage hash differs",
        )
        result[name] = data
    require(
        set(result) == set(expected), "complete source patch witness coverage required"
    )
    return result

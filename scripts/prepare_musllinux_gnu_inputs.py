#!/usr/bin/env python3
"""Prepare GNU correspondence inputs from a reviewed, offline hash-named cache.

No package installation, image load, archive path extraction or ELF execution.
This prepares only the bounded comparison gate, not source/advisory clearance.
"""

import argparse
import tarfile
import zipfile
import zlib
from pathlib import Path

from verify_musllinux_gnu import AttributionError, load_manifest, prepare_inputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        prepare_inputs(
            load_manifest(args.manifest),
            args.cache_dir.absolute(),
            args.output_dir.absolute(),
        )
    except (
        AttributionError,
        OSError,
        ValueError,
        KeyError,
        tarfile.TarError,
        zipfile.BadZipFile,
        zlib.error,
    ) as exc:
        print("GNU comparison input preparation failed: " + str(exc), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

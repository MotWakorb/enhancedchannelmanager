"""Security primitives shared across ECM subsystems.

This package holds standalone, dependency-light security building blocks that
must be importable from anywhere WITHOUT forming an import cycle. Notably the
SSRF chokepoint (:mod:`security.ssrf`) is imported by the cloud-storage adapters
and (eventually) the settings router, so it deliberately lives here rather than
inside the settings router.
"""

"""Shared types used across ffmpeg_builder modules."""

from dataclasses import dataclass, field
from typing import List


@dataclass
class ValidationResult:
    """Result of validating an ffmpeg configuration element."""

    valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.valid = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def merge(self, other: "ValidationResult") -> None:
        """Merge another ValidationResult into this one."""
        if not other.valid:
            self.valid = False
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)

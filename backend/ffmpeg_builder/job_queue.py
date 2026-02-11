"""
FFMPEG job queue module.

Provides an in-memory job queue with FIFO/priority ordering, concurrency limits,
retry logic, state transitions, and cleanup.
"""
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Enums & Exceptions
# ---------------------------------------------------------------------------

class JobStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvalidTransitionError(Exception):
    """Raised when a job state transition is not allowed."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class JobQueueConfig:
    max_concurrent: int = 1
    max_retries: int = 3
    retry_delay: int = 30  # seconds
    priority_mode: str = "fifo"  # "fifo" or "priority"


@dataclass
class Job:
    id: str
    name: str
    command: List[str]
    status: JobStatus = JobStatus.QUEUED
    priority: int = 0
    retry_count: int = 0
    retry_after: Optional[datetime] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# JobQueue
# ---------------------------------------------------------------------------

class JobQueue:
    """In-memory job queue with concurrency limits and retry logic."""

    def __init__(
        self,
        max_concurrent: int = 1,
        max_retries: int = 3,
        retry_delay: int = 30,
    ) -> None:
        self.config = JobQueueConfig(
            max_concurrent=max_concurrent,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        self._jobs: Dict[str, Job] = {}

    def enqueue(
        self,
        name: str,
        command: List[str],
        priority: int = 0,
    ) -> Job:
        """Add a job to the queue.

        Args:
            name: Human-readable job name.
            command: ffmpeg command as list of strings.
            priority: Job priority (higher = more urgent).

        Returns:
            The newly created Job.
        """
        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id,
            name=name,
            command=command,
            priority=priority,
        )
        self._jobs[job_id] = job
        return job

    def dequeue(self) -> Optional[Job]:
        """Dequeue the next eligible job, respecting concurrency limits.

        Returns:
            The next Job to run, or None if no jobs are eligible.
        """
        running_count = sum(
            1 for j in self._jobs.values() if j.status == JobStatus.RUNNING
        )
        if running_count >= self.config.max_concurrent:
            return None

        # Get eligible queued jobs (retry_after is advisory metadata only)
        eligible = [
            j for j in self._jobs.values()
            if j.status == JobStatus.QUEUED
        ]

        if not eligible:
            return None

        # Always sort by priority (descending) then creation time (ascending)
        # FIFO is the natural fallback when all priorities are equal (0)
        eligible.sort(key=lambda j: (-j.priority, j.created_at))

        job = eligible[0]
        job.status = JobStatus.RUNNING
        job.started_at = datetime.utcnow()
        return job

    def get_job(self, job_id: str) -> Job:
        """Get a job by ID.

        Raises:
            KeyError: If job not found.
        """
        return self._jobs[job_id]

    def complete(self, job_id: str) -> None:
        """Mark a running job as completed."""
        job = self._jobs[job_id]
        if job.status != JobStatus.RUNNING:
            raise InvalidTransitionError(
                f"Cannot complete job in {job.status.value} state"
            )
        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.utcnow()

    def fail(self, job_id: str, error: str = "") -> None:
        """Mark a running job as failed.

        Args:
            job_id: Job identifier.
            error: Error message describing the failure.
        """
        job = self._jobs[job_id]
        if job.status != JobStatus.RUNNING:
            raise InvalidTransitionError(
                f"Cannot fail job in {job.status.value} state"
            )
        job.status = JobStatus.FAILED
        job.error = error
        job.completed_at = datetime.utcnow()

    def cancel(self, job_id: str) -> None:
        """Cancel a queued or running job.

        Raises:
            InvalidTransitionError: If the job is in a terminal state.
        """
        job = self._jobs[job_id]
        if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
            raise InvalidTransitionError(
                f"Cannot cancel job in {job.status.value} state"
            )
        job.status = JobStatus.CANCELLED
        job.completed_at = datetime.utcnow()

    def retry(self, job_id: str) -> None:
        """Retry a failed job.

        Re-queues the job with an incremented retry count and a delay.

        Raises:
            InvalidTransitionError: If the job cannot be retried (wrong state
                or max retries exceeded).
        """
        job = self._jobs[job_id]
        if job.status not in (JobStatus.FAILED,):
            raise InvalidTransitionError(
                f"Cannot retry job in {job.status.value} state"
            )
        if job.retry_count >= self.config.max_retries:
            raise InvalidTransitionError(
                f"Job has exceeded max retries ({self.config.max_retries})"
            )
        job.retry_count += 1
        job.status = JobStatus.QUEUED
        job.error = None
        job.completed_at = None
        job.retry_after = datetime.utcnow() + timedelta(
            seconds=self.config.retry_delay
        )

    def status_counts(self) -> Dict[JobStatus, int]:
        """Return counts of jobs grouped by status."""
        counts: Dict[JobStatus, int] = defaultdict(int)
        for job in self._jobs.values():
            counts[job.status] += 1
        return dict(counts)

    def cleanup(self, retention_days: int = 7) -> int:
        """Remove old completed/failed jobs.

        Args:
            retention_days: Keep jobs newer than this many days.

        Returns:
            Number of jobs removed.
        """
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        to_remove = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status in (JobStatus.COMPLETED, JobStatus.FAILED)
            and job.completed_at is not None
            and job.completed_at < cutoff
        ]
        for job_id in to_remove:
            del self._jobs[job_id]
        return len(to_remove)

    def update_config(self, **kwargs) -> None:
        """Update queue configuration at runtime.

        Accepts keyword arguments matching JobQueueConfig fields:
        max_concurrent, max_retries, retry_delay, priority_mode.
        """
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)

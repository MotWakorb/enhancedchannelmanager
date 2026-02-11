"""
Unit tests for the FFMPEG Builder job queue module.

Tests job queuing, lifecycle, retry logic, configuration, and cleanup (Spec 1.15).
These are TDD tests -- they will FAIL until the backend modules are implemented.
"""
from datetime import datetime, timedelta

import pytest

from ffmpeg_builder.job_queue import (
    JobQueue,
    Job,
    JobStatus,
    JobQueueConfig,
    InvalidTransitionError,
)

from tests.fixtures.ffmpeg_factories import (
    create_ffmpeg_job,
    create_builder_state,
)


class TestJobQueue:
    """Tests for basic job queue operations."""

    @pytest.fixture
    def queue(self):
        """Create a fresh job queue for testing."""
        return JobQueue(max_concurrent=2)

    def test_enqueues_job(self, queue):
        """A job can be added to the queue."""
        job = queue.enqueue(
            name="Test Encode",
            command=["ffmpeg", "-i", "input.mp4", "-c:v", "libx264", "output.mp4"],
        )

        assert job is not None
        assert job.id is not None
        assert job.status == JobStatus.QUEUED

    def test_dequeues_next_job(self, queue):
        """The next queued job can be dequeued for processing."""
        queue.enqueue(name="Job 1", command=["ffmpeg", "-i", "a.mp4", "b.mp4"])
        queue.enqueue(name="Job 2", command=["ffmpeg", "-i", "c.mp4", "d.mp4"])

        job = queue.dequeue()

        assert job is not None
        assert job.name == "Job 1"

    def test_respects_max_concurrent(self, queue):
        """Queue does not dequeue more jobs than max_concurrent allows."""
        queue.enqueue(name="Job 1", command=["ffmpeg", "-i", "a.mp4", "b.mp4"])
        queue.enqueue(name="Job 2", command=["ffmpeg", "-i", "c.mp4", "d.mp4"])
        queue.enqueue(name="Job 3", command=["ffmpeg", "-i", "e.mp4", "f.mp4"])

        job1 = queue.dequeue()
        job2 = queue.dequeue()
        job3 = queue.dequeue()

        # Max concurrent is 2, so third dequeue should return None
        assert job1 is not None
        assert job2 is not None
        assert job3 is None

    def test_queue_fifo_order(self, queue):
        """Jobs are dequeued in FIFO order by default."""
        queue.enqueue(name="First", command=["ffmpeg", "-i", "a.mp4", "b.mp4"])
        queue.enqueue(name="Second", command=["ffmpeg", "-i", "c.mp4", "d.mp4"])
        queue.enqueue(name="Third", command=["ffmpeg", "-i", "e.mp4", "f.mp4"])

        first = queue.dequeue()
        second = queue.dequeue()

        assert first.name == "First"
        assert second.name == "Second"

    def test_priority_ordering(self, queue):
        """Higher-priority jobs are dequeued before lower-priority ones."""
        queue.enqueue(name="Low", command=["ffmpeg", "-i", "a.mp4", "b.mp4"], priority=1)
        queue.enqueue(name="High", command=["ffmpeg", "-i", "c.mp4", "d.mp4"], priority=10)
        queue.enqueue(name="Medium", command=["ffmpeg", "-i", "e.mp4", "f.mp4"], priority=5)

        first = queue.dequeue()

        assert first.name == "High"

    def test_queue_status_counts(self, queue):
        """Queue provides counts of jobs by status."""
        queue.enqueue(name="Job 1", command=["ffmpeg", "-i", "a.mp4", "b.mp4"])
        queue.enqueue(name="Job 2", command=["ffmpeg", "-i", "c.mp4", "d.mp4"])
        queue.dequeue()  # Moves Job 1 to running

        counts = queue.status_counts()

        assert counts[JobStatus.QUEUED] == 1
        assert counts[JobStatus.RUNNING] == 1


class TestJobLifecycle:
    """Tests for job state transitions."""

    @pytest.fixture
    def queue(self):
        return JobQueue(max_concurrent=2)

    def test_job_starts_as_queued(self, queue):
        """New jobs start with QUEUED status."""
        job = queue.enqueue(name="New Job", command=["ffmpeg", "-i", "a.mp4", "b.mp4"])

        assert job.status == JobStatus.QUEUED

    def test_job_transitions_to_running(self, queue):
        """Dequeued job transitions to RUNNING status."""
        queue.enqueue(name="Run Me", command=["ffmpeg", "-i", "a.mp4", "b.mp4"])
        job = queue.dequeue()

        assert job.status == JobStatus.RUNNING

    def test_job_transitions_to_completed(self, queue):
        """Running job can be marked as completed."""
        queue.enqueue(name="Complete Me", command=["ffmpeg", "-i", "a.mp4", "b.mp4"])
        job = queue.dequeue()

        queue.complete(job.id)
        updated = queue.get_job(job.id)

        assert updated.status == JobStatus.COMPLETED

    def test_job_transitions_to_failed(self, queue):
        """Running job can be marked as failed with an error message."""
        queue.enqueue(name="Fail Me", command=["ffmpeg", "-i", "a.mp4", "b.mp4"])
        job = queue.dequeue()

        queue.fail(job.id, error="Encoding error: invalid codec")
        updated = queue.get_job(job.id)

        assert updated.status == JobStatus.FAILED
        assert "invalid codec" in updated.error

    def test_job_can_be_cancelled_when_queued(self, queue):
        """A queued job can be cancelled."""
        job = queue.enqueue(name="Cancel Me", command=["ffmpeg", "-i", "a.mp4", "b.mp4"])

        queue.cancel(job.id)
        updated = queue.get_job(job.id)

        assert updated.status == JobStatus.CANCELLED

    def test_job_can_be_cancelled_when_running(self, queue):
        """A running job can be cancelled."""
        queue.enqueue(name="Cancel Running", command=["ffmpeg", "-i", "a.mp4", "b.mp4"])
        job = queue.dequeue()

        queue.cancel(job.id)
        updated = queue.get_job(job.id)

        assert updated.status == JobStatus.CANCELLED

    def test_cannot_cancel_completed_job(self, queue):
        """A completed job cannot be cancelled."""
        queue.enqueue(name="Already Done", command=["ffmpeg", "-i", "a.mp4", "b.mp4"])
        job = queue.dequeue()
        queue.complete(job.id)

        with pytest.raises(InvalidTransitionError):
            queue.cancel(job.id)


class TestJobRetry:
    """Tests for job retry logic."""

    @pytest.fixture
    def queue(self):
        return JobQueue(max_concurrent=2, max_retries=3, retry_delay=5)

    def test_retries_failed_job(self, queue):
        """A failed job can be retried and goes back to QUEUED."""
        queue.enqueue(name="Retry Me", command=["ffmpeg", "-i", "a.mp4", "b.mp4"])
        job = queue.dequeue()
        queue.fail(job.id, error="Temporary error")

        queue.retry(job.id)
        updated = queue.get_job(job.id)

        assert updated.status == JobStatus.QUEUED
        assert updated.retry_count == 1

    def test_respects_max_retries(self, queue):
        """Job cannot be retried beyond the max retry count."""
        queue.enqueue(name="Max Retry", command=["ffmpeg", "-i", "a.mp4", "b.mp4"])
        job = queue.dequeue()

        # Exhaust all retries
        for i in range(3):
            queue.fail(job.id, error=f"Attempt {i + 1} failed")
            queue.retry(job.id)
            job = queue.dequeue()

        # Fail one more time
        queue.fail(job.id, error="Final failure")

        # Should not allow another retry
        with pytest.raises((InvalidTransitionError, ValueError)):
            queue.retry(job.id)

    def test_retry_delay_between_attempts(self, queue):
        """Retried jobs have a delay before becoming eligible for dequeue."""
        queue.enqueue(name="Delayed Retry", command=["ffmpeg", "-i", "a.mp4", "b.mp4"])
        job = queue.dequeue()
        queue.fail(job.id, error="Temporary error")
        queue.retry(job.id)

        updated = queue.get_job(job.id)

        # The job should have a scheduled retry time in the future
        assert updated.retry_after is not None
        assert updated.retry_after > datetime.utcnow()

    def test_no_retry_on_cancel(self, queue):
        """Cancelled jobs cannot be retried."""
        job = queue.enqueue(name="Cancelled", command=["ffmpeg", "-i", "a.mp4", "b.mp4"])
        queue.cancel(job.id)

        with pytest.raises((InvalidTransitionError, ValueError)):
            queue.retry(job.id)


class TestQueueConfig:
    """Tests for job queue configuration."""

    def test_default_max_concurrent(self):
        """Default max_concurrent is a sensible value (e.g. 1)."""
        queue = JobQueue()

        assert queue.config.max_concurrent >= 1

    def test_updates_max_concurrent(self):
        """Max concurrent can be updated at runtime."""
        queue = JobQueue(max_concurrent=2)

        queue.update_config(max_concurrent=4)

        assert queue.config.max_concurrent == 4

    def test_updates_max_retries(self):
        """Max retries can be updated at runtime."""
        queue = JobQueue(max_retries=3)

        queue.update_config(max_retries=5)

        assert queue.config.max_retries == 5

    def test_updates_retry_delay(self):
        """Retry delay (in seconds) can be updated at runtime."""
        queue = JobQueue(retry_delay=10)

        queue.update_config(retry_delay=30)

        assert queue.config.retry_delay == 30

    def test_updates_priority_mode(self):
        """Priority mode (fifo or priority) can be toggled."""
        queue = JobQueue()

        queue.update_config(priority_mode="priority")

        assert queue.config.priority_mode == "priority"


class TestJobCleanup:
    """Tests for cleaning up old completed/failed jobs."""

    @pytest.fixture
    def queue(self):
        return JobQueue(max_concurrent=2)

    def test_removes_old_completed_jobs(self, queue):
        """Completed jobs older than the retention period are removed."""
        # Enqueue and complete several jobs
        for i in range(5):
            queue.enqueue(name=f"Old Job {i}", command=["ffmpeg", "-i", "a.mp4", "b.mp4"])
            job = queue.dequeue()
            queue.complete(job.id)
            # Manually backdate the completed_at timestamp
            j = queue.get_job(job.id)
            j.completed_at = datetime.utcnow() - timedelta(days=30)

        # Run cleanup with 7-day retention
        removed = queue.cleanup(retention_days=7)

        assert removed >= 5

    def test_keeps_recent_jobs(self, queue):
        """Recently completed jobs are not removed by cleanup."""
        queue.enqueue(name="Recent Job", command=["ffmpeg", "-i", "a.mp4", "b.mp4"])
        job = queue.dequeue()
        queue.complete(job.id)

        # Run cleanup with 7-day retention
        removed = queue.cleanup(retention_days=7)

        # Recent job should still exist
        assert removed == 0
        recent = queue.get_job(job.id)
        assert recent is not None
        assert recent.status == JobStatus.COMPLETED

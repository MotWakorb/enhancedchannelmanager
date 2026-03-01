"""
Scheduled Tasks Package.

This package contains all task implementations that can be scheduled
via the task engine.
"""

from tasks.epg_refresh import EPGRefreshTask
from tasks.m3u_refresh import M3URefreshTask
from tasks.m3u_change_monitor import M3UChangeMonitorTask
from tasks.cleanup import CleanupTask
from tasks.stream_probe import StreamProbeTask
from tasks.failed_stream_reprobe import FailedStreamReprobeTask
from tasks.struck_stream_cleanup import StruckStreamCleanupTask
from tasks.popularity_calculation import PopularityCalculationTask
from tasks.auto_creation import AutoCreationTask
from tasks.dummy_epg_refresh import DummyEPGRefreshTask

__all__ = [
    "EPGRefreshTask",
    "M3URefreshTask",
    "M3UChangeMonitorTask",
    "CleanupTask",
    "StreamProbeTask",
    "FailedStreamReprobeTask",
    "StruckStreamCleanupTask",
    "PopularityCalculationTask",
    "AutoCreationTask",
    "DummyEPGRefreshTask",
]

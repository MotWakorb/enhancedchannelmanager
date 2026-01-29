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

__all__ = [
    "EPGRefreshTask",
    "M3URefreshTask",
    "M3UChangeMonitorTask",
    "CleanupTask",
    "StreamProbeTask",
]

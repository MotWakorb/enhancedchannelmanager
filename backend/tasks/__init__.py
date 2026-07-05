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
from tasks.channel_pipeline import ChannelPipelineTask
from tasks.dummy_epg_refresh import DummyEPGRefreshTask
from tasks.black_screen_scan import BlackScreenScanTask
from tasks.yaml_backup import YamlBackupTask
from tasks.dbas_backup import DbasBackupTask
from tasks.dbas_restore import DbasRestoreTask
from tasks.dbas_sync import DbasSyncTask
from tasks.stats_v2_rollup import StatsV2RollupTask

__all__ = [
    "EPGRefreshTask",
    "M3URefreshTask",
    "M3UChangeMonitorTask",
    "CleanupTask",
    "StreamProbeTask",
    "FailedStreamReprobeTask",
    "StruckStreamCleanupTask",
    "PopularityCalculationTask",
    "ChannelPipelineTask",
    "DummyEPGRefreshTask",
    "BlackScreenScanTask",
    "YamlBackupTask",
    "DbasBackupTask",
    "DbasRestoreTask",
    "DbasSyncTask",
    "StatsV2RollupTask",
]

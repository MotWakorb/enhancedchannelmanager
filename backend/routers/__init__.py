"""
Router registry — imports and exports all API routers.

Updated in each phase as new routers are extracted from main.py.
"""
from routers.journal import router as journal_router
from routers.tags import router as tags_router
from routers.profiles import router as profiles_router
from routers.normalization import router as normalization_router
from routers.streams import router as streams_router
from routers.alert_methods import router as alert_methods_router
from routers.health import router as health_router
from routers.notifications import router as notifications_router
from routers.stats import router as stats_router
from routers.stream_stats import router as stream_stats_router
from routers.stream_preview import router as stream_preview_router
from routers.auto_creation import router as auto_creation_router
from routers.ffmpeg import router as ffmpeg_router
from routers.tasks import router as tasks_router
from routers.settings import router as settings_router
from routers.epg import router as epg_router
from routers.m3u import router as m3u_router
from routers.m3u_digest import router as m3u_digest_router
from routers.channels import router as channels_router
from routers.channel_groups import router as channel_groups_router

all_routers = [
    tasks_router,
    journal_router,
    tags_router,
    profiles_router,
    normalization_router,
    streams_router,
    alert_methods_router,
    health_router,
    notifications_router,
    stats_router,
    stream_stats_router,
    stream_preview_router,
    auto_creation_router,
    ffmpeg_router,
    settings_router,
    epg_router,
    m3u_router,
    m3u_digest_router,
    channels_router,
    channel_groups_router,
]

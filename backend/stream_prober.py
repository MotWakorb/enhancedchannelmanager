"""
Stream Prober service.
Uses ffprobe to extract stream metadata and stores results in SQLite.
Supports both scheduled and on-demand probing.
"""
import asyncio
import json
import logging
import shutil
from datetime import datetime, timedelta
from typing import Optional

from database import get_session
from models import StreamStats

logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_PROBE_TIMEOUT = 30  # seconds
DEFAULT_PROBE_BATCH_SIZE = 10  # streams per cycle
DEFAULT_PROBE_INTERVAL_HOURS = 24  # daily


def check_ffprobe_available() -> bool:
    """Check if ffprobe is available on the system."""
    return shutil.which("ffprobe") is not None


class StreamProber:
    """
    Background service that probes streams using ffprobe.
    Supports scheduled probing and on-demand single/batch probes.
    """

    def __init__(
        self,
        client,
        probe_timeout: int = DEFAULT_PROBE_TIMEOUT,
        probe_batch_size: int = DEFAULT_PROBE_BATCH_SIZE,
        probe_interval_hours: int = DEFAULT_PROBE_INTERVAL_HOURS,
        probe_enabled: bool = True,
        schedule_time: str = "03:00",  # HH:MM format, 24h
        user_timezone: str = "",  # IANA timezone name
    ):
        self.client = client
        self.probe_timeout = probe_timeout
        self.probe_batch_size = probe_batch_size
        self.probe_interval_hours = probe_interval_hours
        self.probe_enabled = probe_enabled
        self.schedule_time = schedule_time
        self.user_timezone = user_timezone
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._probing_in_progress = False

    async def start(self):
        """Start the background scheduled probing task."""
        logger.info("StreamProber.start() called")

        if self._running:
            logger.warning("StreamProber already running")
            return

        # Check ffprobe availability
        ffprobe_available = check_ffprobe_available()
        logger.info(f"ffprobe availability check: {ffprobe_available}")

        if not ffprobe_available:
            logger.error("ffprobe not found - stream probing disabled (scheduled probing will not start)")
            logger.warning("On-demand probing will fail without ffprobe")
            return

        self._running = True
        self._task = asyncio.create_task(self._scheduled_probe_loop())
        logger.info(
            f"StreamProber started successfully (schedule: {self.schedule_time}, interval: {self.probe_interval_hours}h, "
            f"batch: {self.probe_batch_size}, timeout: {self.probe_timeout}s)"
        )

    async def stop(self):
        """Stop the background probing task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("StreamProber stopped")

    def _get_seconds_until_next_schedule(self) -> int:
        """Calculate seconds until the next scheduled probe time."""
        try:
            # Parse schedule time
            hour, minute = map(int, self.schedule_time.split(":"))
        except (ValueError, AttributeError):
            hour, minute = 3, 0  # Default to 3:00 AM

        now = datetime.utcnow()

        # If user has a timezone set, calculate in their local time
        if self.user_timezone:
            try:
                import zoneinfo
                tz = zoneinfo.ZoneInfo(self.user_timezone)
                now_local = datetime.now(tz)
                # Create next scheduled time in user's timezone
                next_run_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if next_run_local <= now_local:
                    # Already passed today, schedule for tomorrow
                    next_run_local += timedelta(days=1)
                # Convert to UTC for sleep calculation
                next_run_utc = next_run_local.astimezone(zoneinfo.ZoneInfo("UTC")).replace(tzinfo=None)
                seconds_until = (next_run_utc - now).total_seconds()
            except Exception as e:
                logger.warning(f"Failed to use timezone {self.user_timezone}, using UTC: {e}")
                # Fall back to UTC
                next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if next_run <= now:
                    next_run += timedelta(days=1)
                seconds_until = (next_run - now).total_seconds()
        else:
            # No timezone set, use UTC
            next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            seconds_until = (next_run - now).total_seconds()

        return max(60, int(seconds_until))  # At least 60 seconds

    async def _scheduled_probe_loop(self):
        """Main loop for scheduled probing."""
        # Wait a bit before first probe to let system stabilize
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return

        while self._running and self.probe_enabled:
            # Calculate time until next scheduled probe
            seconds_until = self._get_seconds_until_next_schedule()
            hours_until = seconds_until / 3600
            logger.info(f"StreamProber: Next scheduled probe in {hours_until:.1f} hours (at {self.schedule_time})")

            # Sleep until scheduled time
            try:
                await asyncio.sleep(seconds_until)
            except asyncio.CancelledError:
                break

            # Run the probe
            try:
                await self._probe_stale_streams()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"StreamProber scheduled probe error: {e}")

            # Small delay before calculating next schedule (to avoid immediate re-triggering)
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break

    async def _probe_stale_streams(self):
        """Find and probe streams that haven't been probed recently."""
        if self._probing_in_progress:
            logger.debug("Probe already in progress, skipping")
            return

        self._probing_in_progress = True
        try:
            cutoff = datetime.utcnow() - timedelta(hours=self.probe_interval_hours)

            # Get all streams from Dispatcharr
            streams = await self._fetch_all_streams()
            if not streams:
                logger.debug("No streams found to probe")
                return

            # Find streams needing probe (never probed or stale)
            session = get_session()
            try:
                probed_recently = {
                    stat.stream_id
                    for stat in session.query(StreamStats).filter(
                        StreamStats.last_probed > cutoff
                    ).all()
                }

                to_probe = [s for s in streams if s["id"] not in probed_recently]
                to_probe = to_probe[: self.probe_batch_size]  # Limit batch size

                if not to_probe:
                    logger.debug("No streams need probing")
                    return

                logger.info(f"Scheduled probe: {len(to_probe)} streams to probe")

                for stream in to_probe:
                    if not self._running:
                        break
                    await self.probe_stream(
                        stream["id"], stream.get("url"), stream.get("name")
                    )
                    await asyncio.sleep(1)  # Rate limiting

            finally:
                session.close()
        finally:
            self._probing_in_progress = False

    async def probe_stream(
        self, stream_id: int, url: Optional[str], name: Optional[str] = None
    ) -> dict:
        """
        Probe a single stream using ffprobe.
        Returns the probe result dict.
        """
        logger.debug(f"probe_stream() called for stream_id={stream_id}, name={name}, url={'present' if url else 'missing'}")

        if not url:
            logger.warning(f"Stream {stream_id} has no URL, marking as failed")
            return self._save_probe_result(
                stream_id, name, None, "failed", "No URL available"
            )

        try:
            logger.debug(f"Running ffprobe for stream {stream_id}")
            result = await self._run_ffprobe(url)
            logger.info(f"Stream {stream_id} probe succeeded")
            return self._save_probe_result(stream_id, name, result, "success", None)
        except asyncio.TimeoutError:
            logger.warning(f"Stream {stream_id} probe timed out after {self.probe_timeout}s")
            return self._save_probe_result(
                stream_id,
                name,
                None,
                "timeout",
                f"Probe timed out after {self.probe_timeout}s",
            )
        except Exception as e:
            error_msg = str(e)
            # Truncate very long error messages
            if len(error_msg) > 500:
                error_msg = error_msg[:500] + "..."
            logger.error(f"Stream {stream_id} probe failed: {error_msg}")
            return self._save_probe_result(stream_id, name, None, "failed", error_msg)

    async def _run_ffprobe(self, url: str) -> dict:
        """Run ffprobe and parse JSON output."""
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-timeout",
            str(self.probe_timeout * 1000000),  # microseconds
            url,
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.probe_timeout + 5
            )
        except asyncio.TimeoutError:
            process.kill()
            raise

        if process.returncode != 0:
            error_text = stderr.decode()[:500] if stderr else "Unknown error"
            raise RuntimeError(f"ffprobe failed: {error_text}")

        output = stdout.decode()
        if not output.strip():
            raise RuntimeError("ffprobe returned empty output")

        return json.loads(output)

    def _save_probe_result(
        self,
        stream_id: int,
        stream_name: Optional[str],
        ffprobe_data: Optional[dict],
        status: str,
        error_message: Optional[str],
    ) -> dict:
        """Parse ffprobe output and save to database."""
        session = get_session()
        try:
            # Get or create stats record
            stats = (
                session.query(StreamStats).filter_by(stream_id=stream_id).first()
            )
            if not stats:
                stats = StreamStats(stream_id=stream_id)
                session.add(stats)

            stats.stream_name = stream_name
            stats.probe_status = status
            stats.error_message = error_message
            stats.last_probed = datetime.utcnow()

            if ffprobe_data and status == "success":
                self._parse_ffprobe_data(stats, ffprobe_data)

            session.commit()
            result = stats.to_dict()
            logger.debug(f"Saved probe result for stream {stream_id}: {status}")
            return result
        except Exception as e:
            logger.error(f"Failed to save probe result: {e}")
            session.rollback()
            raise
        finally:
            session.close()

    def _parse_ffprobe_data(self, stats: StreamStats, data: dict):
        """Extract relevant fields from ffprobe JSON output."""
        streams = data.get("streams", [])
        format_info = data.get("format", {})

        # Find video stream
        video_stream = next(
            (s for s in streams if s.get("codec_type") == "video"), None
        )
        if video_stream:
            width = video_stream.get("width")
            height = video_stream.get("height")
            if width and height:
                stats.resolution = f"{width}x{height}"

            stats.video_codec = video_stream.get("codec_name")

            # Parse FPS from various fields
            fps = self._parse_fps(video_stream)
            if fps:
                stats.fps = str(fps)

        # Find audio stream
        audio_stream = next(
            (s for s in streams if s.get("codec_type") == "audio"), None
        )
        if audio_stream:
            stats.audio_codec = audio_stream.get("codec_name")
            stats.audio_channels = audio_stream.get("channels")

        # Format info
        format_name = format_info.get("format_name", "")
        stats.stream_type = self._parse_stream_type(format_name)

        # Bitrate
        bit_rate = format_info.get("bit_rate")
        if bit_rate:
            try:
                stats.bitrate = int(bit_rate)
            except (ValueError, TypeError):
                pass

    def _parse_fps(self, video_stream: dict) -> Optional[float]:
        """Parse FPS from various ffprobe fields."""
        # Try r_frame_rate first (most reliable)
        r_frame_rate = video_stream.get("r_frame_rate")
        if r_frame_rate and "/" in r_frame_rate:
            try:
                num, den = r_frame_rate.split("/")
                if float(den) > 0:
                    return round(float(num) / float(den), 2)
            except (ValueError, ZeroDivisionError):
                pass

        # Try avg_frame_rate
        avg_frame_rate = video_stream.get("avg_frame_rate")
        if avg_frame_rate and "/" in avg_frame_rate:
            try:
                num, den = avg_frame_rate.split("/")
                if float(den) > 0:
                    return round(float(num) / float(den), 2)
            except (ValueError, ZeroDivisionError):
                pass

        return None

    def _parse_stream_type(self, format_name: str) -> Optional[str]:
        """Parse stream type from ffprobe format name."""
        format_lower = format_name.lower()
        if "hls" in format_lower or "m3u8" in format_lower or "applehttp" in format_lower:
            return "HLS"
        elif "mpegts" in format_lower:
            return "MPEG-TS"
        elif "mp4" in format_lower or "mov" in format_lower:
            return "MP4"
        elif "flv" in format_lower:
            return "FLV"
        elif "rtmp" in format_lower:
            return "RTMP"
        elif "dash" in format_lower:
            return "DASH"
        elif format_name:
            # Return first part if multiple formats listed (e.g., "hls,applehttp")
            return format_name.split(",")[0].upper()[:10]
        return None

    async def _fetch_all_streams(self) -> list:
        """Fetch all streams from Dispatcharr (paginated)."""
        all_streams = []
        page = 1
        while True:
            try:
                result = await self.client.get_streams(page=page, page_size=500)
                streams = result.get("results", [])
                all_streams.extend(streams)
                if not result.get("next"):
                    break
                page += 1
                if page > 50:  # Safety limit
                    break
            except Exception as e:
                logger.error(f"Failed to fetch streams page {page}: {e}")
                break
        return all_streams

    async def probe_all_streams(self):
        """Probe all streams (runs in background)."""
        if self._probing_in_progress:
            logger.warning("Probe already in progress")
            return {"status": "already_running"}

        self._probing_in_progress = True
        probed_count = 0
        try:
            streams = await self._fetch_all_streams()
            logger.info(f"Starting probe of {len(streams)} streams")

            for stream in streams:
                if not self._running:
                    break
                await self.probe_stream(
                    stream["id"], stream.get("url"), stream.get("name")
                )
                probed_count += 1
                await asyncio.sleep(0.5)  # Rate limiting

            logger.info(f"Completed probing {probed_count} streams")
            return {"status": "completed", "probed": probed_count}
        except Exception as e:
            logger.error(f"Probe all streams failed: {e}")
            return {"status": "failed", "error": str(e), "probed": probed_count}
        finally:
            self._probing_in_progress = False

    @staticmethod
    def get_all_stats() -> list:
        """Get all stream stats from database."""
        session = get_session()
        try:
            stats = session.query(StreamStats).all()
            return [s.to_dict() for s in stats]
        finally:
            session.close()

    @staticmethod
    def get_stats_by_stream_ids(stream_ids: list[int]) -> dict[int, dict]:
        """Get stats for multiple streams by their IDs."""
        if not stream_ids:
            return {}
        session = get_session()
        try:
            stats = session.query(StreamStats).filter(
                StreamStats.stream_id.in_(stream_ids)
            ).all()
            return {s.stream_id: s.to_dict() for s in stats}
        finally:
            session.close()

    @staticmethod
    def get_stats_by_stream_id(stream_id: int) -> Optional[dict]:
        """Get stats for a specific stream."""
        session = get_session()
        try:
            stats = (
                session.query(StreamStats).filter_by(stream_id=stream_id).first()
            )
            return stats.to_dict() if stats else None
        finally:
            session.close()

    @staticmethod
    def get_stats_summary() -> dict:
        """Get summary of probe statistics."""
        from sqlalchemy import func

        session = get_session()
        try:
            total = session.query(func.count(StreamStats.id)).scalar() or 0
            success = (
                session.query(func.count(StreamStats.id))
                .filter(StreamStats.probe_status == "success")
                .scalar()
                or 0
            )
            failed = (
                session.query(func.count(StreamStats.id))
                .filter(StreamStats.probe_status == "failed")
                .scalar()
                or 0
            )
            timeout = (
                session.query(func.count(StreamStats.id))
                .filter(StreamStats.probe_status == "timeout")
                .scalar()
                or 0
            )
            pending = (
                session.query(func.count(StreamStats.id))
                .filter(StreamStats.probe_status == "pending")
                .scalar()
                or 0
            )

            return {
                "total": total,
                "success": success,
                "failed": failed,
                "timeout": timeout,
                "pending": pending,
            }
        finally:
            session.close()

    @staticmethod
    def delete_stats(stream_id: int) -> bool:
        """Delete stats for a specific stream."""
        session = get_session()
        try:
            deleted = (
                session.query(StreamStats)
                .filter_by(stream_id=stream_id)
                .delete()
            )
            session.commit()
            return deleted > 0
        except Exception as e:
            logger.error(f"Failed to delete stats for stream {stream_id}: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    @staticmethod
    def purge_old_stats(days: int = 30):
        """Remove stats for streams not probed in specified days."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        session = get_session()
        try:
            deleted = (
                session.query(StreamStats)
                .filter(StreamStats.last_probed < cutoff)
                .delete()
            )
            session.commit()
            if deleted > 0:
                logger.info(f"Purged {deleted} old stream stats")
        except Exception as e:
            logger.error(f"Failed to purge old stats: {e}")
            session.rollback()
        finally:
            session.close()


# Global prober instance
_prober: Optional[StreamProber] = None


def get_prober() -> Optional[StreamProber]:
    """Get the global prober instance."""
    logger.debug(f"get_prober() called, returning: {_prober is not None} (instance exists: {_prober is not None})")
    return _prober


def set_prober(prober: StreamProber):
    """Set the global prober instance."""
    global _prober
    _prober = prober
    logger.info(f"Stream prober instance set: {prober is not None}")

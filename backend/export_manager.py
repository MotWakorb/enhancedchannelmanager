"""
Export manager — orchestrates M3U/XMLTV generation for a playlist profile.
Fetches channel/stream/EPG data from Dispatcharr, generates files,
and writes them to /config/exports/<profile_id>/.
"""
import logging
import os
import tempfile
from pathlib import Path

from config import CONFIG_DIR
from dispatcharr_client import get_client
from m3u_generator import generate_m3u
from xmltv_generator import generate_xmltv

logger = logging.getLogger(__name__)

EXPORTS_DIR = CONFIG_DIR / "exports"


class ExportManager:
    """Orchestrates export generation for playlist profiles."""

    def get_export_path(self, profile_id: int) -> Path:
        """Get the export directory for a profile."""
        return EXPORTS_DIR / str(profile_id)

    async def generate(self, profile: dict) -> dict:
        """Generate M3U and XMLTV files for a profile.

        Args:
            profile: Profile dict from PlaylistProfile.to_dict().

        Returns:
            Dict with generation results: channels_count, m3u_path, xmltv_path, m3u_size, xmltv_size.
        """
        profile_id = profile["id"]
        logger.info("[EXPORT] Starting generation for profile %s (%s)", profile_id, profile.get("name"))

        client = get_client()

        # 1. Fetch channels based on selection mode
        channels = await self._fetch_channels(client, profile)
        if not channels:
            logger.warning("[EXPORT] No channels found for profile %s", profile_id)
            return {"channels_count": 0, "m3u_path": None, "xmltv_path": None}

        # 2. Sort channels
        channels = self._sort_channels(channels, profile.get("sort_order", "number"))

        # 3. Fetch stream URLs for first stream of each channel
        stream_lookup = await self._fetch_stream_urls(client, channels)

        # 4. Fetch EPG data for channels with tvg_ids
        epg_data = await self._fetch_epg_data(client, channels)

        # 5. Generate M3U
        m3u_content = generate_m3u(channels, profile, stream_lookup)

        # 6. Generate XMLTV
        xmltv_content = generate_xmltv(channels, epg_data, profile)

        # 7. Write files atomically
        export_dir = self.get_export_path(profile_id)
        prefix = profile.get("filename_prefix", "playlist")

        m3u_path = export_dir / f"{prefix}.m3u"
        xmltv_path = export_dir / f"{prefix}.xml"

        self._atomic_write(m3u_path, m3u_content)
        self._atomic_write(xmltv_path, xmltv_content)

        result = {
            "channels_count": len(channels),
            "m3u_path": str(m3u_path),
            "xmltv_path": str(xmltv_path),
            "m3u_size": len(m3u_content.encode("utf-8")),
            "xmltv_size": len(xmltv_content.encode("utf-8")),
        }
        logger.info("[EXPORT] Generation complete for profile %s: %s channels", profile_id, len(channels))
        return result

    def cleanup(self, profile_id: int) -> None:
        """Remove export files for a profile."""
        export_dir = self.get_export_path(profile_id)
        if export_dir.exists():
            import shutil
            shutil.rmtree(export_dir)
            logger.info("[EXPORT] Cleaned up exports for profile %s", profile_id)

    async def _fetch_channels(self, client, profile: dict) -> list[dict]:
        """Fetch channels from Dispatcharr based on profile selection."""
        mode = profile.get("selection_mode", "all")
        all_channels = await self._get_all_channels(client)

        if mode == "all":
            return all_channels

        if mode == "groups":
            selected = set(profile.get("selected_groups", []))
            return [
                ch for ch in all_channels
                if ch.get("channel_group") in selected
            ]

        if mode == "channels":
            selected = set(profile.get("selected_channels", []))
            return [ch for ch in all_channels if ch.get("id") in selected]

        return all_channels

    async def _get_all_channels(self, client) -> list[dict]:
        """Fetch all channels from Dispatcharr (handles pagination)."""
        all_channels = []
        page = 1
        while True:
            result = await client.get_channels(page=page, page_size=500)
            channels = result.get("results", [])
            all_channels.extend(channels)
            if not result.get("next"):
                break
            page += 1
        # Enrich with group names
        groups = await client.get_channel_groups()
        group_lookup = {g["id"]: g.get("name", "") for g in groups}
        for ch in all_channels:
            ch["channel_group_name"] = group_lookup.get(ch.get("channel_group"), "")
            # Resolve logo URL
            logo = ch.get("logo")
            if isinstance(logo, dict):
                ch["logo_url"] = logo.get("url", "")
            elif isinstance(logo, str):
                ch["logo_url"] = logo
            else:
                ch["logo_url"] = ""
        return all_channels

    def _sort_channels(self, channels: list[dict], sort_order: str) -> list[dict]:
        """Sort channels by the specified order."""
        if sort_order == "name":
            return sorted(channels, key=lambda c: (c.get("name") or "").lower())
        elif sort_order == "group":
            return sorted(channels, key=lambda c: (
                (c.get("channel_group_name") or "").lower(),
                c.get("channel_number") or 0,
            ))
        else:  # "number" (default)
            return sorted(channels, key=lambda c: c.get("channel_number") or 0)

    async def _fetch_stream_urls(self, client, channels: list[dict]) -> dict:
        """Fetch stream details for the first stream of each channel."""
        all_stream_ids = set()
        for ch in channels:
            stream_ids = ch.get("streams", [])
            if stream_ids:
                all_stream_ids.add(stream_ids[0])

        if not all_stream_ids:
            return {}

        stream_lookup = {}
        ids_list = list(all_stream_ids)
        for i in range(0, len(ids_list), 100):
            batch = ids_list[i:i + 100]
            try:
                streams = await client.get_streams_by_ids(batch)
                for s in streams:
                    stream_lookup[s.get("id")] = s
            except Exception as e:
                logger.warning("[EXPORT] Failed to fetch stream batch: %s", e)

        return stream_lookup

    async def _fetch_epg_data(self, client, channels: list[dict]) -> list[dict]:
        """Fetch EPG programme data for channels."""
        try:
            return await client.get_epg_grid()
        except Exception as e:
            logger.warning("[EXPORT] Failed to fetch EPG data: %s", e)
            return []

    def _atomic_write(self, path: Path, content: str) -> None:
        """Write content to a file atomically (write to temp, then rename)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, path)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                # Temp file may already be gone (race with rename, or never
                # created); the original write failure below is the real error
                # we want to propagate, so suppress this cleanup error.
                pass
            raise

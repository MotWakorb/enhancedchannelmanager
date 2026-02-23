"""
Account Stream Limits Module

Ported from StreamFlow-mod to ECM.
Limits the number of streams per M3U account that can be assigned to each channel.

IMPORTANT: Limits are applied PER CHANNEL, not globally!
Example: Global limit 2 → Each channel can have max 2 streams from each account.
         With 10 channels, an account can provide max 20 streams total (2×10).
"""
import logging
from typing import Dict, List, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


class AccountStreamLimiter:
    """
    Manages per-account stream limits for channel assignments.
    
    Limits are applied PER CHANNEL:
    - Global limit: Default limit for all accounts per channel
    - Per-account limits: Override global limit for specific accounts per channel
    """
    
    def __init__(
        self,
        enabled: bool = False,
        global_limit: int = 0,
        account_limits: Optional[Dict[str, int]] = None
    ):
        """
        Initialize account stream limiter.
        
        Args:
            enabled: Whether limits are enabled
            global_limit: Default limit per account per channel (0 = unlimited)
            account_limits: Per-account limits {account_id: limit} per channel
        """
        self.enabled = enabled
        self.global_limit = global_limit
        self.account_limits = account_limits or {}
        
    def apply_limits(
        self,
        stream_ids: List[int],
        stream_m3u_map: Dict[int, int],
        channel_name: str = "unknown"
    ) -> List[int]:
        """
        Apply account stream limits to a single channel's stream list.
        
        Args:
            stream_ids: List of stream IDs for this channel
            stream_m3u_map: Map of stream_id -> m3u_account_id
            channel_name: Channel name for logging
            
        Returns:
            Filtered list of stream IDs with limits applied
        """
        if not self.enabled:
            logger.debug("[ACCOUNT-LIMITS] Disabled, returning all streams")
            return stream_ids
            
        if self.global_limit == 0 and not self.account_limits:
            logger.debug("[ACCOUNT-LIMITS] No limits configured, returning all streams")
            return stream_ids
            
        # Track streams per account FOR THIS CHANNEL ONLY
        account_counts = defaultdict(int)
        limited_streams = []
        excluded_count = 0
        
        logger.info(
            "[ACCOUNT-LIMITS] Channel '%s': Applying limits to %s streams",
            channel_name, len(stream_ids)
        )
        
        for stream_id in stream_ids:
            m3u_account_id = stream_m3u_map.get(stream_id)
            
            # Skip custom streams (no M3U account)
            if m3u_account_id is None:
                limited_streams.append(stream_id)
                continue
                
            # Determine limit for this account
            account_limit = self.account_limits.get(str(m3u_account_id), self.global_limit)
            
            # If limit is 0, no limit applies
            if account_limit == 0:
                limited_streams.append(stream_id)
                continue
                
            # Check if we're within the limit FOR THIS CHANNEL
            if account_counts[m3u_account_id] < account_limit:
                limited_streams.append(stream_id)
                account_counts[m3u_account_id] += 1
            else:
                # Stream exceeds limit for this channel, skip it
                excluded_count += 1
                logger.debug(
                    "[ACCOUNT-LIMITS] Stream %s from account %s exceeds per-channel limit (%s)",
                    stream_id, m3u_account_id, account_limit
                )
                
        if excluded_count > 0:
            logger.info(
                "[ACCOUNT-LIMITS] Channel '%s': Excluded %s streams due to account limits",
                channel_name, excluded_count
            )
            
        # Log per-account statistics for this channel
        if account_counts:
            logger.debug("[ACCOUNT-LIMITS] Channel '%s' account limits applied:", channel_name)
            for account_id, count in account_counts.items():
                account_limit = self.account_limits.get(str(account_id), self.global_limit)
                if account_limit > 0:
                    logger.debug(
                        "[ACCOUNT-LIMITS]   Account %s: %s/%s streams assigned",
                        account_id, count, account_limit
                    )
                    
        return limited_streams
        
    def apply_limits_bulk(
        self,
        channel_streams: Dict[int, List[int]],
        stream_m3u_map: Dict[int, int],
        channel_names: Optional[Dict[int, str]] = None
    ) -> Dict[int, List[int]]:
        """
        Apply account stream limits to multiple channels at once.
        
        Args:
            channel_streams: Map of channel_id -> list of stream_ids
            stream_m3u_map: Map of stream_id -> m3u_account_id
            channel_names: Optional map of channel_id -> channel_name for logging
            
        Returns:
            Modified channel_streams with limits applied per channel
        """
        if not self.enabled:
            return channel_streams
            
        channel_names = channel_names or {}
        limited_channels = {}
        total_excluded = 0
        
        logger.info(
            "[ACCOUNT-LIMITS] Applying per-channel limits to %s channels",
            len(channel_streams)
        )
        
        for channel_id, stream_ids in channel_streams.items():
            channel_name = channel_names.get(channel_id, f"Channel {channel_id}")
            limited_streams = self.apply_limits(stream_ids, stream_m3u_map, channel_name)
            limited_channels[channel_id] = limited_streams
            total_excluded += len(stream_ids) - len(limited_streams)
            
        if total_excluded > 0:
            logger.info(
                "[ACCOUNT-LIMITS] Applied per-channel account stream limits: "
                "%s streams were excluded from assignment across %s channels",
                total_excluded, len(channel_streams)
            )
            
        return limited_channels


def apply_account_stream_limits(
    stream_ids: List[int],
    stream_m3u_map: Dict[int, int],
    enabled: bool = False,
    global_limit: int = 0,
    account_limits: Optional[Dict[str, int]] = None,
    channel_name: str = "unknown"
) -> List[int]:
    """
    Convenience function to apply account stream limits to a single channel.
    
    Args:
        stream_ids: List of stream IDs for this channel
        stream_m3u_map: Map of stream_id -> m3u_account_id
        enabled: Whether limits are enabled
        global_limit: Default limit per account per channel (0 = unlimited)
        account_limits: Per-account limits {account_id: limit} per channel
        channel_name: Channel name for logging
        
    Returns:
        Filtered list of stream IDs with limits applied
    """
    limiter = AccountStreamLimiter(
        enabled=enabled,
        global_limit=global_limit,
        account_limits=account_limits
    )
    return limiter.apply_limits(stream_ids, stream_m3u_map, channel_name)

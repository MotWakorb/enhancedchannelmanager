"""
Stream Provider Diversification Module

Ported from StreamFlow-mod to ECM.
Provides Round Robin and Priority Weighted diversification modes
to improve stream redundancy and failover.
"""
import logging
from typing import List, Dict, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


class StreamDiversification:
    """
    Handles provider diversification for stream ordering.
    
    Two modes available:
    - Round Robin: Alphabetical provider rotation (A → B → C → A → B → C...)
    - Priority Weighted: M3U priority-based rotation (Premium → Basic → Premium → Basic...)
    """
    
    MODE_ROUND_ROBIN = "round_robin"
    MODE_PRIORITY_WEIGHTED = "priority_weighted"
    
    def __init__(
        self,
        enabled: bool = False,
        mode: str = MODE_ROUND_ROBIN,
        m3u_account_priorities: Optional[Dict[str, int]] = None
    ):
        """
        Initialize diversification handler.
        
        Args:
            enabled: Whether diversification is enabled
            mode: Diversification mode (round_robin or priority_weighted)
            m3u_account_priorities: M3U account priorities {account_id: priority}
        """
        self.enabled = enabled
        self.mode = mode
        self.m3u_account_priorities = m3u_account_priorities or {}
        
    def apply_diversification(
        self,
        stream_ids: List[int],
        stream_m3u_map: Dict[int, int],
        channel_name: str = "unknown"
    ) -> List[int]:
        """
        Apply provider diversification to stream list.
        
        Args:
            stream_ids: List of stream IDs (already sorted by quality)
            stream_m3u_map: Map of stream_id -> m3u_account_id
            channel_name: Channel name for logging
            
        Returns:
            Reordered list of stream IDs with diversification applied
        """
        if not self.enabled:
            logger.debug("[DIVERSIFICATION] Disabled, returning original order")
            return stream_ids
            
        if len(stream_ids) <= 1:
            logger.debug("[DIVERSIFICATION] Only %s stream(s), skipping", len(stream_ids))
            return stream_ids
            
        # Group streams by provider
        provider_groups = defaultdict(list)
        streams_without_provider = []
        
        for stream_id in stream_ids:
            m3u_account_id = stream_m3u_map.get(stream_id)
            if m3u_account_id is not None:
                provider_groups[m3u_account_id].append(stream_id)
            else:
                # Custom streams without M3U account
                streams_without_provider.append(stream_id)
                
        if len(provider_groups) <= 1:
            logger.debug(
                "[DIVERSIFICATION] Channel '%s': Only %s provider(s), skipping diversification",
                channel_name, len(provider_groups)
            )
            return stream_ids
            
        logger.info(
            "[DIVERSIFICATION] Channel '%s': Applying %s diversification to %s streams from %s providers",
            channel_name, self.mode, len(stream_ids), len(provider_groups)
        )
        
        # Sort providers based on mode
        if self.mode == self.MODE_PRIORITY_WEIGHTED:
            # Sort by M3U priority (highest first)
            sorted_providers = sorted(
                provider_groups.keys(),
                key=lambda p: self.m3u_account_priorities.get(str(p), 0),
                reverse=True
            )
            logger.debug(
                "[DIVERSIFICATION] Priority Weighted mode: Provider order by priority: %s",
                [(p, self.m3u_account_priorities.get(str(p), 0)) for p in sorted_providers]
            )
        else:
            # Round Robin: Sort alphabetically by provider ID
            sorted_providers = sorted(provider_groups.keys())
            logger.debug(
                "[DIVERSIFICATION] Round Robin mode: Provider order (alphabetical): %s",
                sorted_providers
            )
            
        # Interleave streams from providers (Round Robin)
        result = []
        max_streams = max(len(streams) for streams in provider_groups.values())
        
        for round_idx in range(max_streams):
            for provider_id in sorted_providers:
                streams = provider_groups[provider_id]
                if round_idx < len(streams):
                    result.append(streams[round_idx])
                    
        # Add custom streams (without provider) at the end
        result.extend(streams_without_provider)
        
        logger.info(
            "[DIVERSIFICATION] Channel '%s': Diversification complete - %s streams reordered",
            channel_name, len(result)
        )
        
        return result


def apply_provider_diversification(
    stream_ids: List[int],
    stream_m3u_map: Dict[int, int],
    enabled: bool = False,
    mode: str = "round_robin",
    m3u_account_priorities: Optional[Dict[str, int]] = None,
    channel_name: str = "unknown"
) -> List[int]:
    """
    Convenience function to apply provider diversification.
    
    Args:
        stream_ids: List of stream IDs (already sorted by quality)
        stream_m3u_map: Map of stream_id -> m3u_account_id
        enabled: Whether diversification is enabled
        mode: Diversification mode (round_robin or priority_weighted)
        m3u_account_priorities: M3U account priorities {account_id: priority}
        channel_name: Channel name for logging
        
    Returns:
        Reordered list of stream IDs with diversification applied
    """
    diversifier = StreamDiversification(
        enabled=enabled,
        mode=mode,
        m3u_account_priorities=m3u_account_priorities
    )
    return diversifier.apply_diversification(stream_ids, stream_m3u_map, channel_name)

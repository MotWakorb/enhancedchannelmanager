"""
Enhanced Stream Features Configuration

Configuration management for enhanced stream features:
- Provider Diversification
- Account Stream Limits
- Extended M3U Priority Modes
"""
import json
import logging
from pathlib import Path
from typing import Dict, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Configuration file path
CONFIG_DIR = Path("/config")
ENHANCED_CONFIG_FILE = CONFIG_DIR / "enhanced_features.json"


class ProviderDiversificationConfig(BaseModel):
    """Provider diversification configuration."""
    enabled: bool = Field(default=False, description="Enable provider diversification")
    mode: str = Field(
        default="round_robin",
        description="Diversification mode: round_robin or priority_weighted"
    )


class AccountStreamLimitsConfig(BaseModel):
    """Account stream limits configuration."""
    enabled: bool = Field(default=False, description="Enable account stream limits")
    global_limit: int = Field(
        default=0,
        ge=0,
        description="Global limit per account per channel (0 = unlimited)"
    )
    account_limits: Dict[str, int] = Field(
        default_factory=dict,
        description="Per-account limits {account_id: limit} per channel"
    )


class M3UPriorityConfig(BaseModel):
    """M3U priority configuration."""
    mode: str = Field(
        default="disabled",
        description="Priority mode: disabled, same_resolution, all_streams"
    )
    account_priorities: Dict[str, int] = Field(
        default_factory=dict,
        description="M3U account priorities {account_id: priority}"
    )


class EnhancedFeaturesConfig(BaseModel):
    """Complete enhanced stream features configuration."""
    provider_diversification: ProviderDiversificationConfig = Field(
        default_factory=ProviderDiversificationConfig
    )
    account_stream_limits: AccountStreamLimitsConfig = Field(
        default_factory=AccountStreamLimitsConfig
    )
    m3u_priority: M3UPriorityConfig = Field(
        default_factory=M3UPriorityConfig
    )


class EnhancedFeaturesManager:
    """Manages enhanced stream features configuration."""
    
    def __init__(self):
        self.config_file = ENHANCED_CONFIG_FILE
        self._config: Optional[EnhancedFeaturesConfig] = None
        
    def load_config(self) -> EnhancedFeaturesConfig:
        """Load configuration from file."""
        if self._config is not None:
            return self._config
            
        if not self.config_file.exists():
            logger.info("[ENHANCED-CONFIG] Config file not found, using defaults")
            self._config = EnhancedFeaturesConfig()
            self.save_config()
            return self._config
            
        try:
            with open(self.config_file, 'r') as f:
                data = json.load(f)
            self._config = EnhancedFeaturesConfig(**data)
            logger.info("[ENHANCED-CONFIG] Configuration loaded successfully")
            return self._config
        except Exception as e:
            logger.error("[ENHANCED-CONFIG] Error loading config: %s", e)
            self._config = EnhancedFeaturesConfig()
            return self._config
            
    def save_config(self) -> bool:
        """Save configuration to file."""
        if self._config is None:
            return False
            
        try:
            # Ensure config directory exists
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.config_file, 'w') as f:
                json.dump(self._config.dict(), f, indent=2)
            logger.info("[ENHANCED-CONFIG] Configuration saved successfully")
            return True
        except Exception as e:
            logger.error("[ENHANCED-CONFIG] Error saving config: %s", e)
            return False
            
    def update_config(self, updates: Dict) -> bool:
        """Update configuration with partial updates."""
        try:
            current = self.load_config()
            
            # Update provider diversification
            if "provider_diversification" in updates:
                pd_updates = updates["provider_diversification"]
                if "enabled" in pd_updates:
                    current.provider_diversification.enabled = pd_updates["enabled"]
                if "mode" in pd_updates:
                    current.provider_diversification.mode = pd_updates["mode"]
                    
            # Update account stream limits
            if "account_stream_limits" in updates:
                asl_updates = updates["account_stream_limits"]
                if "enabled" in asl_updates:
                    current.account_stream_limits.enabled = asl_updates["enabled"]
                if "global_limit" in asl_updates:
                    current.account_stream_limits.global_limit = asl_updates["global_limit"]
                if "account_limits" in asl_updates:
                    current.account_stream_limits.account_limits = asl_updates["account_limits"]
                    
            # Update M3U priority
            if "m3u_priority" in updates:
                mp_updates = updates["m3u_priority"]
                if "mode" in mp_updates:
                    current.m3u_priority.mode = mp_updates["mode"]
                if "account_priorities" in mp_updates:
                    current.m3u_priority.account_priorities = mp_updates["account_priorities"]
                    
            self._config = current
            return self.save_config()
        except Exception as e:
            logger.error("[ENHANCED-CONFIG] Error updating config: %s", e)
            return False
            
    def get_config(self) -> EnhancedFeaturesConfig:
        """Get current configuration."""
        return self.load_config()
        
    def reset_config(self) -> bool:
        """Reset configuration to defaults."""
        self._config = EnhancedFeaturesConfig()
        return self.save_config()


# Global instance for easy access
_manager_instance: Optional[EnhancedFeaturesManager] = None


def get_enhanced_features_config() -> EnhancedFeaturesConfig:
    """Get the global enhanced stream features configuration."""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = EnhancedFeaturesManager()
    return _manager_instance.load_config()


def save_enhanced_features_config(config: EnhancedFeaturesConfig) -> bool:
    """Save the global enhanced stream features configuration."""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = EnhancedFeaturesManager()
    _manager_instance._config = config
    return _manager_instance.save_config()

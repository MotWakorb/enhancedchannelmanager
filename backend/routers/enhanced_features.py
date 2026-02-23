"""
Enhanced Stream Features API Router

API endpoints for enhanced stream features in ECM:
- Provider Diversification
- Account Stream Limits
- M3U Priority
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional

from enhanced_features_config import (
    EnhancedFeaturesManager,
    get_enhanced_features_config,
    EnhancedFeaturesConfig,
    ProviderDiversificationConfig,
    AccountStreamLimitsConfig,
    M3UPriorityConfig,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/enhanced-features", tags=["Enhanced Stream Features"])


# Request models for partial updates
class ProviderDiversificationUpdate(BaseModel):
    """Provider diversification update request."""
    enabled: Optional[bool] = None
    mode: Optional[str] = None


class AccountStreamLimitsUpdate(BaseModel):
    """Account stream limits update request."""
    enabled: Optional[bool] = None
    global_limit: Optional[int] = None
    account_limits: Optional[Dict[str, int]] = None


class M3UPriorityUpdate(BaseModel):
    """M3U priority update request."""
    mode: Optional[str] = None
    account_priorities: Optional[Dict[str, int]] = None


class EnhancedFeaturesUpdate(BaseModel):
    """Complete enhanced stream features update request."""
    provider_diversification: Optional[ProviderDiversificationUpdate] = None
    account_stream_limits: Optional[AccountStreamLimitsUpdate] = None
    m3u_priority: Optional[M3UPriorityUpdate] = None


# Endpoints
@router.get("/config", response_model=EnhancedFeaturesConfig)
async def get_config():
    """
    Get current enhanced stream features configuration.
    
    Returns configuration for:
    - Provider Diversification
    - Account Stream Limits
    - M3U Priority
    """
    try:
        config = get_enhanced_features_config()
        return config
    except Exception as e:
        logger.error("[ENHANCED-API] Error getting config: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config")
async def update_config(updates: EnhancedFeaturesUpdate):
    """
    Update enhanced stream features configuration.
    
    Supports partial updates - only provided fields will be updated.
    """
    try:
        manager = EnhancedFeaturesManager()
        
        # Convert Pydantic models to dict, excluding None values
        update_dict = {}
        if updates.provider_diversification:
            update_dict["provider_diversification"] = updates.provider_diversification.dict(exclude_none=True)
        if updates.account_stream_limits:
            update_dict["account_stream_limits"] = updates.account_stream_limits.dict(exclude_none=True)
        if updates.m3u_priority:
            update_dict["m3u_priority"] = updates.m3u_priority.dict(exclude_none=True)
        
        success = manager.update_config(update_dict)
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update configuration")
        
        return {"success": True, "message": "Configuration updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[ENHANCED-API] Error updating config: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset")
async def reset_config():
    """
    Reset enhanced stream features configuration to defaults.
    
    This will:
    - Disable all features
    - Clear all custom settings
    - Reset to default values
    """
    try:
        manager = EnhancedFeaturesManager()
        success = manager.reset_config()
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to reset configuration")
        
        return {"success": True, "message": "Configuration reset to defaults"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[ENHANCED-API] Error resetting config: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/provider-diversification", response_model=ProviderDiversificationConfig)
async def get_provider_diversification():
    """Get provider diversification configuration."""
    try:
        config = get_enhanced_features_config()
        return config.provider_diversification
    except Exception as e:
        logger.error("[ENHANCED-API] Error getting provider diversification: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/provider-diversification")
async def update_provider_diversification(updates: ProviderDiversificationUpdate):
    """Update provider diversification configuration."""
    try:
        manager = EnhancedFeaturesManager()
        success = manager.update_config({
            "provider_diversification": updates.dict(exclude_none=True)
        })
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update provider diversification")
        
        return {"success": True, "message": "Provider diversification updated"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[ENHANCED-API] Error updating provider diversification: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/account-stream-limits", response_model=AccountStreamLimitsConfig)
async def get_account_stream_limits():
    """Get account stream limits configuration."""
    try:
        config = get_enhanced_features_config()
        return config.account_stream_limits
    except Exception as e:
        logger.error("[ENHANCED-API] Error getting account stream limits: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/account-stream-limits")
async def update_account_stream_limits(updates: AccountStreamLimitsUpdate):
    """Update account stream limits configuration."""
    try:
        manager = EnhancedFeaturesManager()
        success = manager.update_config({
            "account_stream_limits": updates.dict(exclude_none=True)
        })
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update account stream limits")
        
        return {"success": True, "message": "Account stream limits updated"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[ENHANCED-API] Error updating account stream limits: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/m3u-priority", response_model=M3UPriorityConfig)
async def get_m3u_priority():
    """Get M3U priority configuration."""
    try:
        config = get_enhanced_features_config()
        return config.m3u_priority
    except Exception as e:
        logger.error("[ENHANCED-API] Error getting M3U priority: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/m3u-priority")
async def update_m3u_priority(updates: M3UPriorityUpdate):
    """Update M3U priority configuration."""
    try:
        manager = EnhancedFeaturesManager()
        success = manager.update_config({
            "m3u_priority": updates.dict(exclude_none=True)
        })
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update M3U priority")
        
        return {"success": True, "message": "M3U priority updated"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[ENHANCED-API] Error updating M3U priority: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

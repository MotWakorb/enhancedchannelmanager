/**
 * VLC Media Player Types
 */

export type VLCOpenBehavior = 'protocol_only' | 'm3u_fallback' | 'm3u_only';

// Extend Window interface for VLC settings
export interface VLCWindow extends Window {
    __vlcSettings?: {
        behavior: VLCOpenBehavior;
    };
}
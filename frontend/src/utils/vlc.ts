/**
 * Utility for opening streams in VLC media player.
 * Behavior depends on user settings: protocol_only, m3u_fallback, or m3u_only.
 */

export type VLCOpenBehavior = 'protocol_only' | 'm3u_fallback' | 'm3u_only';

// Store modal trigger callbacks
type ModalCallback = (url: string, name?: string) => void;
let modalCallbacks: ModalCallback[] = [];

/**
 * Register a callback to show the VLC protocol helper modal.
 * Components should register their modal trigger when mounted.
 */
export function registerVLCModalCallback(callback: ModalCallback): () => void {
  modalCallbacks.push(callback);
  // Return unregister function
  return () => {
    modalCallbacks = modalCallbacks.filter(cb => cb !== callback);
  };
}

/**
 * Gets VLC open behavior from settings (stored in global state or defaults to m3u_fallback).
 */
function getVLCBehavior(): VLCOpenBehavior {
  // Get from window global if App.tsx has set it
  const globalSettings = (window as any).__vlcSettings;
  return globalSettings?.behavior || 'm3u_fallback';
}

/**
 * Attempts to open a stream URL in VLC.
 * Behavior depends on user settings loaded from global state.
 *
 * @param url - The stream URL to open
 * @param name - Optional name for the stream (used in m3u fallback)
 */
export function openInVLC(url: string, name?: string): void {
  const behavior = getVLCBehavior();

  // If m3u_only, skip protocol and download M3U directly
  if (behavior === 'm3u_only') {
    downloadM3U(url, name);
    return;
  }
  // Try vlc:// protocol first
  const vlcUrl = `vlc://${url}`;

  // Create a hidden iframe to attempt the protocol
  const iframe = document.createElement('iframe');
  iframe.style.display = 'none';
  document.body.appendChild(iframe);

  // Track if we successfully opened
  let protocolWorked = false;

  // Listen for blur event (indicates app switch, protocol likely worked)
  const handleBlur = () => {
    protocolWorked = true;
  };
  window.addEventListener('blur', handleBlur);

  // Try to open via iframe
  try {
    iframe.contentWindow?.location.replace(vlcUrl);
  } catch {
    // Protocol not supported, will fall back
  }

  // After a short delay, check if we need to fall back
  setTimeout(() => {
    window.removeEventListener('blur', handleBlur);
    document.body.removeChild(iframe);

    if (!protocolWorked) {
      // Protocol failed - handle based on behavior mode
      if (behavior === 'protocol_only') {
        // Show helper modal via registered callbacks
        if (modalCallbacks.length > 0) {
          modalCallbacks[0](url, name);
        }
      } else {
        // m3u_fallback: download M3U file
        downloadM3U(url, name);
      }
    }
  }, 500);
}

/**
 * Downloads an .m3u playlist file containing the stream URL.
 * VLC is typically associated with .m3u files and will open them.
 *
 * @param url - The stream URL
 * @param name - Optional name for the stream
 */
export function downloadM3U(url: string, name?: string): void {
  // Create M3U content
  const streamName = name || 'Stream';
  const m3uContent = `#EXTM3U
#EXTINF:-1,${streamName}
${url}
`;

  // Create blob and download
  const blob = new Blob([m3uContent], { type: 'audio/x-mpegurl' });
  const blobUrl = URL.createObjectURL(blob);

  // Create download link
  const link = document.createElement('a');
  link.href = blobUrl;
  link.download = `${streamName.replace(/[^a-zA-Z0-9]/g, '_')}.m3u`;
  link.style.display = 'none';
  document.body.appendChild(link);

  // Trigger download
  link.click();

  // Cleanup
  setTimeout(() => {
    document.body.removeChild(link);
    URL.revokeObjectURL(blobUrl);
  }, 100);
}

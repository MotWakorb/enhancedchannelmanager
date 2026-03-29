/// <reference types="vite/client" />

interface Window {
  __vlcSettings?: { behavior: 'protocol_only' | 'm3u_fallback' | 'm3u_only' };
}

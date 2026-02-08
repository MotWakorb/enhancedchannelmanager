/**
 * Shared formatting utilities.
 *
 * Consolidated from duplicate implementations across components.
 */

/** Format bytes to human-readable string (e.g. "1.5 GB"). */
export function formatBytes(bytes: number | undefined): string {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex++;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

/** Format bits per second to human-readable bitrate (e.g. "12.5 Mbps"). */
export function formatBitrate(bps: number): string {
  if (!bps) return '0 bps';
  const units = ['bps', 'Kbps', 'Mbps', 'Gbps'];
  let value = bps;
  let unitIndex = 0;
  while (value >= 1000 && unitIndex < units.length - 1) {
    value /= 1000;
    unitIndex++;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

/** Format date string to weekday abbreviation (e.g. "Mon"). */
export function formatDateLabel(dateStr: string): string {
  const date = new Date(dateStr);
  return date.toLocaleDateString('en-US', { weekday: 'short' });
}

/**
 * Format ISO timestamp to locale string with smart year display.
 * Example: "Jan 8 2:35 PM" (same year) or "Jan 8, 2026 2:35 PM" (different year).
 */
export function formatTimestamp(isoString: string): string {
  const date = new Date(isoString);
  const now = new Date();

  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined,
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  });
}

/**
 * Format ISO timestamp to relative time string (e.g. "5m ago", "3h ago").
 * Falls back to formatted date for times older than 7 days.
 * Capitalize controls the first letter: "Just now" vs "just now".
 */
export function formatRelativeTime(isoString: string, capitalize = false): string {
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return capitalize ? 'Just now' : 'just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;

  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined,
    hour: 'numeric',
    minute: '2-digit',
  });
}

/** Format ISO timestamp to HH:MM:SS time string. */
export function formatEventTime(isoString: string): string {
  const date = new Date(isoString);
  return date.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

/**
 * Format ISO timestamp to locale string, or "Never" if null.
 */
export function formatDateTime(isoString: string | null): string {
  if (!isoString) return 'Never';
  const date = new Date(isoString);
  return date.toLocaleString();
}

/**
 * Format seconds to compact duration string (e.g. "2h 15m", "45m 30s", "12s").
 * For simple numeric seconds only.
 */
export function formatDuration(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return '-';
  if (!seconds) return '0s';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.round(seconds % 60);
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  if (minutes > 0) {
    return `${minutes}m ${secs}s`;
  }
  return `${secs}s`;
}

/**
 * Format duration from seconds or string (e.g. "1h 23m", "HH:MM:SS") to HH:MM:SS.
 * Handles string durations like "1h 23m 45s", already-formatted "HH:MM:SS",
 * and plain number-as-string or actual numbers.
 */
export function formatDurationHMS(duration: string | number | undefined): string {
  if (!duration) return '-';

  let totalSeconds: number;

  if (typeof duration === 'string') {
    if (duration.includes(':')) {
      return duration;
    }
    const hourMatch = duration.match(/(\d+)h/);
    const minMatch = duration.match(/(\d+)m/);
    const secMatch = duration.match(/(\d+)s/);

    const hours = hourMatch ? parseInt(hourMatch[1], 10) : 0;
    const mins = minMatch ? parseInt(minMatch[1], 10) : 0;
    const secs = secMatch ? parseInt(secMatch[1], 10) : 0;

    if (!hourMatch && !minMatch && !secMatch) {
      totalSeconds = parseFloat(duration);
      if (isNaN(totalSeconds)) return duration;
    } else {
      totalSeconds = hours * 3600 + mins * 60 + secs;
    }
  } else {
    totalSeconds = Math.floor(duration);
  }

  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const secs = Math.floor(totalSeconds % 60);

  const hh = hours.toString().padStart(2, '0');
  const mm = minutes.toString().padStart(2, '0');
  const ss = secs.toString().padStart(2, '0');

  return `${hh}:${mm}:${ss}`;
}

/** Format seconds to compact watch time (e.g. "2h 15m" or "45m"). */
export function formatWatchTime(seconds: number | undefined): string {
  if (!seconds) return '0m';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  return `${minutes}m`;
}

/** Format speed for display (e.g. "1.00x"). */
export function formatSpeed(speed: number | string | undefined): string {
  if (speed === undefined || speed === null) return '-';
  const numSpeed = typeof speed === 'number' ? speed : parseFloat(speed);
  if (isNaN(numSpeed)) return String(speed);
  return `${numSpeed.toFixed(2)}x`;
}

/** Get CSS class based on speed value. */
export function getSpeedClass(speed: number | string | undefined): string {
  if (speed === undefined || speed === null) return '';
  const numSpeed = typeof speed === 'number' ? speed : parseFloat(speed);
  if (isNaN(numSpeed)) return '';
  if (numSpeed >= 0.98) return 'speed-good';
  if (numSpeed >= 0.90) return 'speed-warning';
  return 'speed-bad';
}

/** Format FPS for display (e.g. "29.97"). */
export function formatFps(fps: number | undefined): string {
  if (fps === undefined || fps === null) return '-';
  return fps.toFixed(2);
}

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import * as api from '../services/api';
import type { Notification } from '../services/api';
import { useNotifications } from '../contexts/NotificationContext';
import './NotificationCenter.css';

interface NotificationCenterProps {
  onNotificationClick?: (notification: Notification) => void;
}

// Progress metadata structure for task notifications
interface ProbeProgress {
  current: number;
  total: number;
  success: number;
  failed: number;
  skipped: number;
  status: 'idle' | 'starting' | 'fetching' | 'refreshing' | 'probing' | 'paused' | 'cancelled' | 'completed' | 'reordering' | 'failed' | 'fetching_sources' | 'fetching_accounts' | 'building_digest' | 'sending_email' | 'sending_discord';
  current_stream: string;
}

interface ProgressMetadata {
  progress?: ProbeProgress;
}

// Helper to check if notification has progress (from any task)
const isProgressNotification = (n: Notification): boolean => {
  // Match stream_probe source OR any task_* source with progress metadata
  const hasProgressSource = n.source === 'stream_probe' || n.source?.startsWith('task_');
  return hasProgressSource && n.metadata?.progress !== undefined;
};

// Alias for backward compatibility
const isProbeNotification = isProgressNotification;

// Helper to get progress from notification
const getProbeProgress = (n: Notification): ProbeProgress | null => {
  if (!isProgressNotification(n)) return null;
  return (n.metadata as ProgressMetadata)?.progress || null;
};

// Helper to check if task is actively running (not completed, failed, or idle)
const isProbeActive = (status: ProbeProgress['status']): boolean => {
  return ['probing', 'fetching', 'refreshing', 'reordering', 'starting', 'fetching_sources', 'fetching_accounts', 'building_digest', 'sending_email', 'sending_discord'].includes(status);
};

export function NotificationCenter({ onNotificationClick }: NotificationCenterProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [restartingFromNotification, setRestartingFromNotification] = useState<number | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const toasts = useNotifications();

  // Load notifications
  const loadNotifications = useCallback(async () => {
    try {
      setLoading(true);
      const response = await api.getNotifications({ page_size: 20 });
      setNotifications(response.notifications);
      setUnreadCount(response.unread_count);
    } catch (err) {
      console.error('Failed to load notifications:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  // Check if any notification has an active probe running (including paused)
  const hasActiveProbe = useMemo(() => {
    return notifications.some(n => {
      const progress = getProbeProgress(n);
      return progress && (isProbeActive(progress.status) || progress.status === 'paused');
    });
  }, [notifications]);

  // Load on mount and periodically - faster when probe is running
  useEffect(() => {
    loadNotifications();
    // Poll every 2 seconds when probe is running, otherwise every 30 seconds
    const pollInterval = hasActiveProbe ? 2000 : 30000;
    const interval = setInterval(loadNotifications, pollInterval);
    return () => clearInterval(interval);
  }, [loadNotifications, hasActiveProbe]);

  // Close panel when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        panelRef.current &&
        buttonRef.current &&
        !panelRef.current.contains(event.target as Node) &&
        !buttonRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [isOpen]);

  const handleToggle = () => {
    setIsOpen(!isOpen);
    if (!isOpen) {
      loadNotifications();
    }
  };

  const handleMarkRead = async (notification: Notification) => {
    try {
      await api.markNotificationRead(notification.id, !notification.read);
      loadNotifications();
    } catch (err) {
      console.error('Failed to mark notification:', err);
    }
  };

  const handleMarkAllRead = async () => {
    try {
      await api.markAllNotificationsRead();
      loadNotifications();
    } catch (err) {
      console.error('Failed to mark all read:', err);
    }
  };

  const handleDelete = async (notification: Notification) => {
    try {
      await api.deleteNotification(notification.id);
      loadNotifications();
    } catch (err) {
      console.error('Failed to delete notification:', err);
    }
  };

  const handleClearAll = async () => {
    try {
      await api.clearNotifications(true); // Only clear read
      loadNotifications();
    } catch (err) {
      console.error('Failed to clear notifications:', err);
    }
  };

  const handleDeleteAll = async () => {
    try {
      await api.clearNotifications(false); // Delete ALL notifications
      loadNotifications();
    } catch (err) {
      console.error('Failed to delete all notifications:', err);
    }
  };

  const handleCancelProbe = async (e: React.MouseEvent) => {
    e.stopPropagation(); // Prevent notification click
    try {
      await api.cancelProbe();
      loadNotifications();
    } catch (err) {
      console.error('Failed to cancel probe:', err);
    }
  };

  const handlePauseProbe = async (e: React.MouseEvent) => {
    e.stopPropagation(); // Prevent notification click
    try {
      await api.pauseProbe();
      loadNotifications();
    } catch (err) {
      console.error('Failed to pause probe:', err);
    }
  };

  const handleResumeProbe = async (e: React.MouseEvent) => {
    e.stopPropagation(); // Prevent notification click
    try {
      await api.resumeProbe();
      loadNotifications();
    } catch (err) {
      console.error('Failed to resume probe:', err);
    }
  };

  const handleRestartServices = async (notification: Notification) => {
    setRestartingFromNotification(notification.id);
    try {
      const result = await api.restartServices();
      if (result.success) {
        // Dispatch event to dismiss any restart toasts from SettingsTab
        window.dispatchEvent(new CustomEvent('services-restarted'));
        toasts.success('Services restarted successfully with new settings.', 'Restart Complete');
        // Delete this notification since the action is complete
        await api.deleteNotification(notification.id);
        loadNotifications();
      } else {
        toasts.error(result.message || 'Failed to restart services', 'Restart Failed');
      }
    } catch (err) {
      console.error('Failed to restart services:', err);
      toasts.error('Failed to restart services', 'Restart Failed');
    } finally {
      setRestartingFromNotification(null);
    }
  };

  const handleNotificationClick = (notification: Notification) => {
    if (onNotificationClick) {
      onNotificationClick(notification);
    }
    if (notification.action_url) {
      // Handle navigation if needed
      window.location.href = notification.action_url;
    }
  };

  const formatTime = (dateStr: string) => {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString();
  };

  const getIcon = (type: string) => {
    switch (type) {
      case 'success': return 'check_circle';
      case 'warning': return 'warning';
      case 'error': return 'error';
      default: return 'info';
    }
  };

  const hasRestartAction = (notification: Notification): boolean => {
    return notification.metadata?.action_type === 'restart_services';
  };

  // Render progress bar for probe notifications
  const renderProbeProgress = (notification: Notification) => {
    const progress = getProbeProgress(notification);
    if (!progress) return null;

    const percentage = progress.total > 0
      ? Math.round((progress.current / progress.total) * 100)
      : 0;

    return (
      <div className="notification-probe-progress">
        {/* Progress bar */}
        <div className="notification-progress-bar">
          <div
            className="notification-progress-fill"
            style={{ width: `${percentage}%` }}
          />
        </div>

        {/* Stats and controls row - stats left, buttons right */}
        <div className="notification-probe-controls-row">
          <div className="notification-probe-stats">
            {progress.success > 0 && (
              <span className="probe-stat probe-stat-success">
                <span className="material-icons">check</span>
                {progress.success}
              </span>
            )}
            {progress.failed > 0 && (
              <span className="probe-stat probe-stat-failed">
                <span className="material-icons">close</span>
                {progress.failed}
              </span>
            )}
            {progress.skipped > 0 && (
              <span className="probe-stat probe-stat-skipped">
                <span className="material-icons">remove</span>
                {progress.skipped}
              </span>
            )}
          </div>

          {(isProbeActive(progress.status) || progress.status === 'paused') && (
            <div className="probe-control-buttons">
              {isProbeActive(progress.status) && (
                <button
                  className="probe-control-btn probe-pause-btn"
                  onClick={handlePauseProbe}
                  title="Pause probe"
                >
                  <span className="material-icons">pause</span>
                </button>
              )}
              {progress.status === 'paused' && (
                <button
                  className="probe-control-btn probe-resume-btn"
                  onClick={handleResumeProbe}
                  title="Resume probe"
                >
                  <span className="material-icons">play_arrow</span>
                </button>
              )}
              <button
                className="probe-control-btn probe-cancel-btn"
                onClick={handleCancelProbe}
                title="Cancel probe"
              >
                <span className="material-icons">close</span>
              </button>
            </div>
          )}
        </div>

        {/* Current stream name if still running or paused */}
        {(isProbeActive(progress.status) || progress.status === 'paused') && progress.current_stream && (
          <div className="notification-probe-current">
            {progress.current_stream}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="notification-center">
      <button
        ref={buttonRef}
        className={`notification-bell ${unreadCount > 0 ? 'has-unread' : ''}`}
        onClick={handleToggle}
        title={`Notifications${unreadCount > 0 ? ` (${unreadCount} unread)` : ''}`}
      >
        <span className="material-icons">notifications</span>
        {unreadCount > 0 && (
          <span className="notification-badge">
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </button>

      {isOpen && (
        <div ref={panelRef} className="notification-panel">
          <div className="notification-panel-header">
            <h3>Notifications</h3>
            <div className="notification-panel-actions">
              {unreadCount > 0 && (
                <button
                  className="notification-action-btn"
                  onClick={handleMarkAllRead}
                  title="Mark all as read"
                >
                  <span className="material-icons">done_all</span>
                </button>
              )}
              {notifications.some(n => n.read) && (
                <button
                  className="notification-action-btn"
                  onClick={handleClearAll}
                  title="Clear read notifications"
                >
                  <span className="material-icons">delete_sweep</span>
                </button>
              )}
              {notifications.length > 0 && (
                <button
                  className="notification-action-btn delete-all"
                  onClick={handleDeleteAll}
                  title="Delete all notifications"
                >
                  <span className="material-icons">delete_forever</span>
                </button>
              )}
            </div>
          </div>

          <div className="notification-list">
            {loading && notifications.length === 0 ? (
              <div className="notification-empty">
                <span className="material-icons spinning">sync</span>
                <span>Loading...</span>
              </div>
            ) : notifications.length === 0 ? (
              <div className="notification-empty">
                <span className="material-icons">notifications_none</span>
                <span>No notifications</span>
              </div>
            ) : (
              notifications.map((notification) => (
                <div
                  key={notification.id}
                  className={`notification-item notification-${notification.type} ${notification.read ? 'read' : 'unread'} ${isProbeNotification(notification) ? 'notification-probe' : ''}`}
                  onClick={() => handleNotificationClick(notification)}
                >
                  <div className="notification-icon">
                    <span className="material-icons">{getIcon(notification.type)}</span>
                  </div>
                  <div className="notification-content">
                    {notification.title && (
                      <div className="notification-title">{notification.title}</div>
                    )}
                    <div className="notification-message">{notification.message}</div>
                    {hasRestartAction(notification) && (
                      <button
                        className="notification-action-btn-inline"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleRestartServices(notification);
                        }}
                        disabled={restartingFromNotification === notification.id}
                      >
                        <span className={`material-icons ${restartingFromNotification === notification.id ? 'spinning' : ''}`}>
                          {restartingFromNotification === notification.id ? 'sync' : 'restart_alt'}
                        </span>
                        {restartingFromNotification === notification.id ? 'Restarting...' : 'Restart Services'}
                      </button>
                    )}
                    {renderProbeProgress(notification)}
                    <div className="notification-time">{formatTime(notification.created_at)}</div>
                  </div>
                  {/* Hide actions for active probe notifications */}
                  {!(isProbeNotification(notification) &&
                     getProbeProgress(notification) &&
                     (isProbeActive(getProbeProgress(notification)!.status) ||
                      getProbeProgress(notification)!.status === 'paused')) && (
                    <div className="notification-actions">
                      <button
                        className="notification-item-action"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleMarkRead(notification);
                        }}
                        title={notification.read ? 'Mark as unread' : 'Mark as read'}
                      >
                        <span className="material-icons">
                          {notification.read ? 'mark_email_unread' : 'mark_email_read'}
                        </span>
                      </button>
                      <button
                        className="notification-item-action delete"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDelete(notification);
                        }}
                        title="Delete"
                      >
                        <span className="material-icons">close</span>
                      </button>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default NotificationCenter;

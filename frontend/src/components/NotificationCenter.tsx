import { useState, useEffect, useRef, useCallback } from 'react';
import * as api from '../services/api';
import type { Notification } from '../services/api';
import './NotificationCenter.css';

interface NotificationCenterProps {
  onNotificationClick?: (notification: Notification) => void;
}

export function NotificationCenter({ onNotificationClick }: NotificationCenterProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

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

  // Load on mount and periodically
  useEffect(() => {
    loadNotifications();
    const interval = setInterval(loadNotifications, 30000); // Poll every 30 seconds
    return () => clearInterval(interval);
  }, [loadNotifications]);

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
                  className={`notification-item notification-${notification.type} ${notification.read ? 'read' : 'unread'}`}
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
                    <div className="notification-time">{formatTime(notification.created_at)}</div>
                  </div>
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

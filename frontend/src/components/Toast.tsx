import { useState, useEffect, useCallback } from 'react';
import './Toast.css';

export type ToastType = 'info' | 'success' | 'warning' | 'error';

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface ToastProps {
  id: string;
  type: ToastType;
  title?: string;
  message: string;
  duration?: number; // ms, 0 = no auto-dismiss
  action?: ToastAction;
  onDismiss: (id: string) => void;
}

const ICONS: Record<ToastType, string> = {
  info: 'info',
  success: 'check_circle',
  warning: 'warning',
  error: 'error',
};

export function Toast({
  id,
  type,
  title,
  message,
  duration = 5000,
  action,
  onDismiss,
}: ToastProps) {
  const [isExiting, setIsExiting] = useState(false);
  const [progress, setProgress] = useState(100);

  const handleDismiss = useCallback(() => {
    setIsExiting(true);
    // Wait for exit animation to complete
    setTimeout(() => {
      onDismiss(id);
    }, 300);
  }, [id, onDismiss]);

  // Auto-dismiss timer with progress bar
  useEffect(() => {
    if (duration <= 0) return;

    const startTime = Date.now();
    const endTime = startTime + duration;

    const updateProgress = () => {
      const now = Date.now();
      const remaining = Math.max(0, endTime - now);
      const newProgress = (remaining / duration) * 100;
      setProgress(newProgress);

      if (remaining <= 0) {
        handleDismiss();
      }
    };

    // Update progress every 50ms for smooth animation
    const interval = setInterval(updateProgress, 50);

    return () => clearInterval(interval);
  }, [duration, handleDismiss]);

  return (
    <div
      className={`toast toast-${type} ${isExiting ? 'toast-exiting' : ''}`}
      role="alert"
      aria-live={type === 'error' ? 'assertive' : 'polite'}
    >
      <div className="toast-icon">
        <span className="material-icons">{ICONS[type]}</span>
      </div>

      <div className="toast-content">
        {title && <div className="toast-title">{title}</div>}
        <div className="toast-message">{message}</div>
        {action && (
          <div className="toast-action">
            <button
              className="toast-action-button"
              onClick={() => {
                action.onClick();
                handleDismiss();
              }}
            >
              {action.label}
            </button>
          </div>
        )}
      </div>

      <button
        className="toast-dismiss"
        onClick={handleDismiss}
        aria-label="Dismiss notification"
      >
        <span className="material-icons">close</span>
      </button>

      {duration > 0 && (
        <div
          className="toast-progress"
          style={{ width: `${progress}%` }}
        />
      )}
    </div>
  );
}

export default Toast;

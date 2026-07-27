import { createContext, useContext, useState, useCallback, useMemo, useRef, ReactNode } from 'react';
import { ToastContainer, ToastData } from '../components/ToastContainer';
import { ToastType, ToastAction } from '../components/Toast';

// Notification options when adding a new notification
export interface NotificationOptions {
  type?: ToastType;
  title?: string;
  message: string;
  duration?: number;
  action?: ToastAction;
}

// Context value interface
interface NotificationContextValue {
  // Add a notification and return its ID
  notify: (options: NotificationOptions) => string;
  // Convenience methods
  info: (message: string, title?: string) => string;
  success: (message: string, title?: string) => string;
  warning: (message: string, title?: string) => string;
  error: (message: string, title?: string) => string;
  // Dismiss a notification by ID
  dismiss: (id: string) => void;
  // Dismiss all notifications
  dismissAll: () => void;
}

const NotificationContext = createContext<NotificationContextValue | null>(null);

// Generate unique IDs for notifications
let notificationIdCounter = 0;
function generateId(): string {
  notificationIdCounter += 1;
  return `notification-${notificationIdCounter}-${Date.now()}`;
}

interface NotificationProviderProps {
  children: ReactNode;
  position?: 'top-right' | 'top-left' | 'bottom-right' | 'bottom-left' | 'top-center' | 'bottom-center';
  maxVisible?: number;
}

export function NotificationProvider({
  children,
  position = 'top-right',
  maxVisible = 5,
}: NotificationProviderProps) {
  const [toasts, setToasts] = useState<ToastData[]>([]);
  // Synchronous mirror of `toasts` so notify() can deduplicate against
  // notifications added earlier in the same tick (bead fi3dq) -- state
  // reads inside rapid successive calls would be stale.
  const toastsRef = useRef<ToastData[]>([]);

  const dismiss = useCallback((id: string) => {
    toastsRef.current = toastsRef.current.filter((toast) => toast.id !== id);
    setToasts(toastsRef.current);
  }, []);

  const dismissAll = useCallback(() => {
    toastsRef.current = [];
    setToasts([]);
  }, []);

  const notify = useCallback((options: NotificationOptions): string => {
    const type = options.type || 'info';
    // Deduplicate equivalent notifications (same type + title + message):
    // repeat callers get the existing toast's id back instead of stacking
    // a duplicate. Guards against error storms flooding the viewport with
    // identical toasts (bead enhancedchannelmanager-fi3dq).
    const existing = toastsRef.current.find(
      (toast) =>
        toast.type === type &&
        toast.title === options.title &&
        toast.message === options.message
    );
    if (existing) {
      return existing.id;
    }

    const id = generateId();
    const toast: ToastData = {
      id,
      type,
      title: options.title,
      message: options.message,
      duration: options.duration ?? 5000,
      action: options.action,
    };

    toastsRef.current = [toast, ...toastsRef.current];
    setToasts(toastsRef.current);
    return id;
  }, []);

  const info = useCallback((message: string, title?: string): string => {
    return notify({ type: 'info', message, title });
  }, [notify]);

  const success = useCallback((message: string, title?: string): string => {
    return notify({ type: 'success', message, title });
  }, [notify]);

  const warning = useCallback((message: string, title?: string): string => {
    return notify({ type: 'warning', message, title });
  }, [notify]);

  const error = useCallback((message: string, title?: string): string => {
    return notify({ type: 'error', message, title, duration: 8000 }); // Errors stay longer
  }, [notify]);

  // Memoize the context value so consumers using it as a dep in
  // useCallback/useEffect don't get a fresh reference on every provider
  // render. All functions below are already stable via useCallback.
  const value = useMemo<NotificationContextValue>(() => ({
    notify,
    info,
    success,
    warning,
    error,
    dismiss,
    dismissAll,
  }), [notify, info, success, warning, error, dismiss, dismissAll]);

  return (
    <NotificationContext.Provider value={value}>
      {children}
      <ToastContainer
        toasts={toasts}
        onDismiss={dismiss}
        position={position}
        maxVisible={maxVisible}
      />
    </NotificationContext.Provider>
  );
}

// Hook to use notifications
// eslint-disable-next-line react-refresh/only-export-components -- hook + provider co-located by convention; moving would require import updates across many consumers
export function useNotifications(): NotificationContextValue {
  const context = useContext(NotificationContext);
  if (!context) {
    throw new Error('useNotifications must be used within a NotificationProvider');
  }
  return context;
}

export default NotificationContext;

import { createContext, useContext, useState, useCallback, ReactNode } from 'react';
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

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id));
  }, []);

  const dismissAll = useCallback(() => {
    setToasts([]);
  }, []);

  const notify = useCallback((options: NotificationOptions): string => {
    const id = generateId();
    const toast: ToastData = {
      id,
      type: options.type || 'info',
      title: options.title,
      message: options.message,
      duration: options.duration ?? 5000,
      action: options.action,
    };

    setToasts((prev) => [toast, ...prev]);
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

  const value: NotificationContextValue = {
    notify,
    info,
    success,
    warning,
    error,
    dismiss,
    dismissAll,
  };

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
export function useNotifications(): NotificationContextValue {
  const context = useContext(NotificationContext);
  if (!context) {
    throw new Error('useNotifications must be used within a NotificationProvider');
  }
  return context;
}

export default NotificationContext;

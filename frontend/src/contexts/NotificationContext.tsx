import { useState, useCallback, ReactNode } from 'react';
import { ToastContainer, ToastData } from '../components/ToastContainer';
import type { NotificationOptions, NotificationContextValue } from './useNotifications';
import { NotificationContext } from './useNotifications';

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
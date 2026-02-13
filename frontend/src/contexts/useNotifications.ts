import { createContext, useContext } from 'react';
import type { ToastType, ToastAction } from '../components/Toast';

// Notification options when adding a new notification
export interface NotificationOptions {
    type?: ToastType;
    title?: string;
    message: string;
    duration?: number;
    action?: ToastAction;
}

// Context value interface
export interface NotificationContextValue {
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

// Context object for the notification system
export const NotificationContext = createContext<NotificationContextValue | null>(null);

// Hook to use notifications
export function useNotifications(): NotificationContextValue {
    const context = useContext(NotificationContext);
    if (!context) {
        throw new Error('useNotifications must be used within a NotificationProvider');
    }
    return context;
}
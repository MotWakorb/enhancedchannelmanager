import { Toast, ToastProps } from './Toast';
import './ToastContainer.css';

export type ToastData = Omit<ToastProps, 'onDismiss'>;

interface ToastContainerProps {
  toasts: ToastData[];
  onDismiss: (id: string) => void;
  position?: 'top-right' | 'top-left' | 'bottom-right' | 'bottom-left' | 'top-center' | 'bottom-center';
  maxVisible?: number;
}

export function ToastContainer({
  toasts,
  onDismiss,
  position = 'top-right',
  maxVisible = 5,
}: ToastContainerProps) {
  // Only show the most recent toasts up to maxVisible
  const visibleToasts = toasts.slice(0, maxVisible);

  if (visibleToasts.length === 0) {
    return null;
  }

  return (
    <div className={`toast-container toast-container-${position}`}>
      {visibleToasts.map((toast) => (
        <Toast
          key={toast.id}
          {...toast}
          onDismiss={onDismiss}
        />
      ))}
      {toasts.length > maxVisible && (
        <div className="toast-overflow-indicator">
          +{toasts.length - maxVisible} more notifications
        </div>
      )}
    </div>
  );
}

export default ToastContainer;

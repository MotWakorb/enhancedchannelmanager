import { useState, useEffect, useCallback, useRef } from 'react';
import type {
  ServiceStatus,
  HealthCheckResult,
  Incident,
  IncidentUpdate,
  MaintenanceWindow,
  ServiceAlertHistory,
  AnyStatusWebSocketMessage,
  StatusUpdateMessage,
  HealthCheckMessage,
  IncidentCreatedMessage,
  IncidentUpdatedMessage,
  IncidentResolvedMessage,
  MaintenanceStartedMessage,
  MaintenanceEndedMessage,
  AlertTriggeredMessage,
  InitialStatusMessage,
} from '../types';

/**
 * Connection state for the WebSocket.
 */
export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting';

/**
 * Event handlers for status updates.
 */
export interface StatusEventHandlers {
  /** Called when a service status changes */
  onStatusUpdate?: (serviceId: string, status: ServiceStatus, details?: Record<string, unknown>) => void;
  /** Called when a health check result is received */
  onHealthCheck?: (serviceId: string, status: ServiceStatus, responseTimeMs: number | null) => void;
  /** Called when an incident is created */
  onIncidentCreated?: (incident: Incident) => void;
  /** Called when an incident is updated */
  onIncidentUpdated?: (incidentId: number, update: IncidentUpdate) => void;
  /** Called when an incident is resolved */
  onIncidentResolved?: (incidentId: number, serviceId: string) => void;
  /** Called when a maintenance window starts */
  onMaintenanceStarted?: (window: MaintenanceWindow) => void;
  /** Called when a maintenance window ends */
  onMaintenanceEnded?: (windowId: number) => void;
  /** Called when an alert is triggered */
  onAlertTriggered?: (alert: ServiceAlertHistory) => void;
  /** Called when initial status is received on connect */
  onInitialStatus?: (services: Record<string, HealthCheckResult>) => void;
  /** Called on any message (raw) */
  onMessage?: (message: AnyStatusWebSocketMessage) => void;
  /** Called on connection state change */
  onConnectionChange?: (state: ConnectionState) => void;
  /** Called on error */
  onError?: (error: string) => void;
}

/**
 * Options for the status WebSocket hook.
 */
export interface UseStatusWebSocketOptions {
  /** Whether to enable auto-reconnection (default: true) */
  autoReconnect?: boolean;
  /** Maximum reconnection attempts (default: 5) */
  maxReconnectAttempts?: number;
  /** Base delay between reconnection attempts in ms (default: 1000) */
  reconnectDelay?: number;
  /** Maximum delay between reconnection attempts in ms (default: 30000) */
  maxReconnectDelay?: number;
  /** Service IDs to subscribe to (empty = subscribe to all) */
  subscribeServices?: string[];
  /** Event handlers */
  handlers?: StatusEventHandlers;
}

/**
 * Return value from the status WebSocket hook.
 */
export interface UseStatusWebSocketReturn {
  /** Current connection state */
  connectionState: ConnectionState;
  /** Whether currently connected */
  isConnected: boolean;
  /** Current service statuses (from initial + updates) */
  serviceStatuses: Record<string, HealthCheckResult>;
  /** Last error message */
  lastError: string | null;
  /** Subscribe to specific services */
  subscribe: (serviceIds: string[]) => void;
  /** Subscribe to all services */
  subscribeAll: () => void;
  /** Unsubscribe from specific services */
  unsubscribe: (serviceIds: string[]) => void;
  /** Manually connect */
  connect: () => void;
  /** Manually disconnect */
  disconnect: () => void;
  /** Send heartbeat */
  sendHeartbeat: () => void;
}

/**
 * Hook for managing a WebSocket connection to the status updates endpoint.
 * Provides real-time status updates for services, incidents, and alerts.
 *
 * @param options - Configuration options
 * @returns WebSocket state and control functions
 *
 * @example
 * const { isConnected, serviceStatuses, connectionState } = useStatusWebSocket({
 *   handlers: {
 *     onStatusUpdate: (serviceId, status) => {
 *       console.log(`Service ${serviceId} is now ${status}`);
 *     },
 *     onIncidentCreated: (incident) => {
 *       toast.warning(`New incident: ${incident.title}`);
 *     },
 *   },
 * });
 */
export function useStatusWebSocket(
  options: UseStatusWebSocketOptions = {}
): UseStatusWebSocketReturn {
  const {
    autoReconnect = true,
    maxReconnectAttempts = 5,
    reconnectDelay = 1000,
    maxReconnectDelay = 30000,
    subscribeServices = [],
    handlers = {},
  } = options;

  // State
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected');
  const [serviceStatuses, setServiceStatuses] = useState<Record<string, HealthCheckResult>>({});
  const [lastError, setLastError] = useState<string | null>(null);

  // Refs
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handlersRef = useRef(handlers);
  const subscribeServicesRef = useRef(subscribeServices);

  // Keep handlers ref up to date
  useEffect(() => {
    handlersRef.current = handlers;
  }, [handlers]);

  // Keep subscribeServices ref up to date
  useEffect(() => {
    subscribeServicesRef.current = subscribeServices;
  }, [subscribeServices]);

  // Build WebSocket URL
  const getWebSocketUrl = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/ws/status`;
  }, []);

  // Update connection state and notify handler
  const updateConnectionState = useCallback((state: ConnectionState) => {
    setConnectionState(state);
    handlersRef.current.onConnectionChange?.(state);
  }, []);

  // Handle incoming message
  const handleMessage = useCallback((event: MessageEvent) => {
    try {
      const message = JSON.parse(event.data) as AnyStatusWebSocketMessage;

      // Call raw message handler
      handlersRef.current.onMessage?.(message);

      // Route message to specific handler based on type
      switch (message.type) {
        case 'status_update': {
          const msg = message as StatusUpdateMessage;
          handlersRef.current.onStatusUpdate?.(msg.service_id, msg.status, msg.details);
          // Update local state
          setServiceStatuses(prev => ({
            ...prev,
            [msg.service_id]: {
              ...prev[msg.service_id],
              service_id: msg.service_id,
              status: msg.status,
              checked_at: msg.timestamp || new Date().toISOString(),
            } as HealthCheckResult,
          }));
          break;
        }

        case 'health_check': {
          const msg = message as HealthCheckMessage;
          handlersRef.current.onHealthCheck?.(msg.service_id, msg.status, msg.response_time_ms);
          // Update local state
          setServiceStatuses(prev => ({
            ...prev,
            [msg.service_id]: {
              ...prev[msg.service_id],
              service_id: msg.service_id,
              status: msg.status,
              response_time_ms: msg.response_time_ms,
              checked_at: msg.timestamp || new Date().toISOString(),
            } as HealthCheckResult,
          }));
          break;
        }

        case 'incident_created': {
          const msg = message as IncidentCreatedMessage;
          handlersRef.current.onIncidentCreated?.(msg.incident);
          break;
        }

        case 'incident_updated': {
          const msg = message as IncidentUpdatedMessage;
          handlersRef.current.onIncidentUpdated?.(msg.incident_id, msg.update);
          break;
        }

        case 'incident_resolved': {
          const msg = message as IncidentResolvedMessage;
          handlersRef.current.onIncidentResolved?.(msg.incident_id, msg.service_id);
          break;
        }

        case 'maintenance_started': {
          const msg = message as MaintenanceStartedMessage;
          handlersRef.current.onMaintenanceStarted?.(msg.window);
          break;
        }

        case 'maintenance_ended': {
          const msg = message as MaintenanceEndedMessage;
          handlersRef.current.onMaintenanceEnded?.(msg.window_id);
          break;
        }

        case 'alert_triggered': {
          const msg = message as AlertTriggeredMessage;
          handlersRef.current.onAlertTriggered?.(msg.alert);
          break;
        }

        case 'initial_status': {
          const msg = message as InitialStatusMessage;
          setServiceStatuses(msg.services);
          handlersRef.current.onInitialStatus?.(msg.services);
          break;
        }

        case 'heartbeat':
          // Heartbeat received - connection is alive
          break;

        case 'error': {
          const errMsg = (message as { message: string }).message;
          setLastError(errMsg);
          handlersRef.current.onError?.(errMsg);
          break;
        }

        case 'subscribed':
        case 'unsubscribed':
          // Subscription confirmation - no action needed
          break;

        default:
          // Handle unknown message types for forward compatibility
          console.warn('Unknown WebSocket message type:', (message as { type: string }).type);
      }
    } catch (e) {
      console.error('Failed to parse WebSocket message:', e);
    }
  }, []);

  // Calculate reconnect delay with exponential backoff
  const getReconnectDelay = useCallback(() => {
    const attempt = reconnectAttemptsRef.current;
    const delay = Math.min(
      reconnectDelay * Math.pow(2, attempt),
      maxReconnectDelay
    );
    // Add some jitter to prevent thundering herd
    return delay + Math.random() * 1000;
  }, [reconnectDelay, maxReconnectDelay]);

  // Send subscription after connection
  const sendSubscription = useCallback(() => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return;

    const serviceIds = subscribeServicesRef.current;
    const message = {
      type: 'subscribe',
      service_ids: serviceIds.length > 0 ? serviceIds : undefined,
    };
    wsRef.current.send(JSON.stringify(message));
  }, []);

  // Connect to WebSocket
  const connect = useCallback(() => {
    // Don't connect if already connected or connecting
    if (wsRef.current?.readyState === WebSocket.OPEN ||
        wsRef.current?.readyState === WebSocket.CONNECTING) {
      return;
    }

    // Clear any pending reconnect timeout
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    updateConnectionState('connecting');
    setLastError(null);

    try {
      const ws = new WebSocket(getWebSocketUrl());
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectAttemptsRef.current = 0;
        updateConnectionState('connected');
        // Send subscription preference
        sendSubscription();
      };

      ws.onmessage = handleMessage;

      ws.onerror = () => {
        setLastError('WebSocket connection error');
        handlersRef.current.onError?.('WebSocket connection error');
      };

      ws.onclose = () => {
        wsRef.current = null;

        // Attempt reconnection if enabled
        if (autoReconnect && reconnectAttemptsRef.current < maxReconnectAttempts) {
          updateConnectionState('reconnecting');
          reconnectAttemptsRef.current += 1;

          const delay = getReconnectDelay();
          reconnectTimeoutRef.current = setTimeout(connect, delay);
        } else {
          updateConnectionState('disconnected');
          if (reconnectAttemptsRef.current >= maxReconnectAttempts) {
            setLastError('Max reconnection attempts reached');
            handlersRef.current.onError?.('Max reconnection attempts reached');
          }
        }
      };
    } catch (e) {
      const error = e instanceof Error ? e.message : 'Failed to create WebSocket';
      setLastError(error);
      handlersRef.current.onError?.(error);
      updateConnectionState('disconnected');
    }
  }, [
    autoReconnect,
    maxReconnectAttempts,
    getWebSocketUrl,
    getReconnectDelay,
    handleMessage,
    sendSubscription,
    updateConnectionState,
  ]);

  // Disconnect from WebSocket
  const disconnect = useCallback(() => {
    // Clear any pending reconnect timeout
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    // Reset reconnect attempts so manual reconnect works
    reconnectAttemptsRef.current = maxReconnectAttempts;

    // Close WebSocket
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    updateConnectionState('disconnected');
  }, [maxReconnectAttempts, updateConnectionState]);

  // Subscribe to specific services
  const subscribe = useCallback((serviceIds: string[]) => {
    subscribeServicesRef.current = serviceIds;
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'subscribe',
        service_ids: serviceIds,
      }));
    }
  }, []);

  // Subscribe to all services
  const subscribeAll = useCallback(() => {
    subscribeServicesRef.current = [];
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'subscribe',
      }));
    }
  }, []);

  // Unsubscribe from specific services
  const unsubscribe = useCallback((serviceIds: string[]) => {
    subscribeServicesRef.current = subscribeServicesRef.current.filter(
      id => !serviceIds.includes(id)
    );
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'unsubscribe',
        service_ids: serviceIds,
      }));
    }
  }, []);

  // Send heartbeat
  const sendHeartbeat = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'heartbeat',
      }));
    }
  }, []);

  // Auto-connect on mount
  useEffect(() => {
    connect();

    return () => {
      // Cleanup on unmount
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Update subscription when subscribeServices prop changes
  useEffect(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      sendSubscription();
    }
  }, [subscribeServices, sendSubscription]);

  return {
    connectionState,
    isConnected: connectionState === 'connected',
    serviceStatuses,
    lastError,
    subscribe,
    subscribeAll,
    unsubscribe,
    connect,
    disconnect,
    sendHeartbeat,
  };
}

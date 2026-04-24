import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
import { AuthProvider } from './hooks/useAuth'
import { ProtectedRoute } from './components/ProtectedRoute'
import { ErrorBoundary } from './components/ErrorBoundary'
import {
  installGlobalErrorHandlers,
  reportClientError,
} from './services/clientErrorReporter'
import './index.css'
import './shared/common.css'

// ADR-006 (bd-i6a1m): wire the frontend error reporter before the React
// tree mounts so a crash during initial render still produces a
// telemetry event. installGlobalErrorHandlers() is idempotent.
installGlobalErrorHandlers()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary
      reloadMode="page"
      onError={(error) => {
        void reportClientError({
          kind: 'boundary',
          message: error.message,
          stack: error.stack ?? '',
        })
      }}
    >
      <AuthProvider>
        <ProtectedRoute>
          <App />
        </ProtectedRoute>
      </AuthProvider>
    </ErrorBoundary>
  </React.StrictMode>,
)

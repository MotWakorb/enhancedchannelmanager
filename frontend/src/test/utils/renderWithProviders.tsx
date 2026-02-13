/**
 * Custom render utilities for testing components with required providers.
 */
import { ReactElement, ReactNode } from 'react'
import { render, RenderOptions, RenderResult } from '@testing-library/react'
import { NotificationProvider } from '../../contexts/NotificationContext'

/**
 * Configuration options for renderWithProviders
 */
interface ProviderOptions {
  /** Whether to wrap with NotificationProvider (default: true) */
  withNotifications?: boolean
}

/**
 * Combined options for render
 */
interface CustomRenderOptions extends Omit<RenderOptions, 'wrapper'> {
  providerOptions?: ProviderOptions
}

/**
 * Creates a wrapper component with all specified providers
 */
function createWrapper(options: ProviderOptions = {}): React.FC<{ children: ReactNode }> {
  const { withNotifications = true } = options

  return function Wrapper({ children }: { children: ReactNode }) {
    let wrapped = children

    // Wrap with NotificationProvider if enabled
    if (withNotifications) {
      wrapped = (
        <NotificationProvider position="top-right">
          {wrapped}
        </NotificationProvider>
      )
    }

    return <>{wrapped}</>
  }
}

/**
 * Custom render function that wraps components with required providers.
 *
 * Usage:
 * ```tsx
 * import { renderWithProviders } from '../test/utils/renderWithProviders'
 *
 * test('renders component', () => {
 *   const { getByText } = renderWithProviders(<MyComponent />)
 *   expect(getByText('Hello')).toBeInTheDocument()
 * })
 * ```
 *
 * @param ui - The React element to render
 * @param options - Render options including provider configuration
 * @returns RenderResult from @testing-library/react
 */
export function renderWithProviders(
  ui: ReactElement,
  options: CustomRenderOptions = {}
): RenderResult {
  const { providerOptions, ...renderOptions } = options
  const Wrapper = createWrapper(providerOptions)

  return render(ui, { wrapper: Wrapper, ...renderOptions })
}

/**
 * Re-export everything from @testing-library/react
 * This allows test files to import everything from this module
 */
// eslint-disable-next-line react-refresh/only-export-components
export * from '@testing-library/react'

/**
 * Export userEvent for convenient imports
 */
export { default as userEvent } from '@testing-library/user-event'

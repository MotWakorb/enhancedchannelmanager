import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RouteHeaderSlot, RouteHeaderTargetProvider } from './RouteHeaderSlots';

describe('RouteHeaderSlot', () => {
  it('composes route-owned content into distinct shared targets', () => {
    const primary = document.createElement('div');
    const status = document.createElement('div');
    const controls = document.createElement('div');
    document.body.append(primary, status, controls);

    render(
      <RouteHeaderTargetProvider targets={{ 'primary-action': primary, status, controls }}>
        <RouteHeaderSlot name="primary-action"><button>Refresh</button></RouteHeaderSlot>
        <RouteHeaderSlot name="status"><span>Auto-refresh: 30s</span></RouteHeaderSlot>
        <RouteHeaderSlot name="controls"><button>Interval</button></RouteHeaderSlot>
      </RouteHeaderTargetProvider>,
    );

    expect(screen.getByRole('button', { name: 'Refresh' }).closest('[data-route-header-slot]'))
      .toHaveAttribute('data-route-header-slot', 'primary-action');
    expect(screen.getByText('Auto-refresh: 30s').closest('[data-route-header-slot]'))
      .toHaveAttribute('data-route-header-slot', 'status');
    expect(screen.getByRole('button', { name: 'Interval' }).closest('[data-route-header-slot]'))
      .toHaveAttribute('data-route-header-slot', 'controls');
  });
});

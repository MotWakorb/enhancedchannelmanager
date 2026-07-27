import { createRef } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { RoutePageHeading, SkipToMainContent } from './AppLandmarks';

describe('application landmarks', () => {
  it.each(['#guide', '#settings/appearance'])(
    'moves focus to main without changing the non-default route %s',
    (hash) => {
      window.history.replaceState({ existing: true }, '', hash);
      const push = vi.spyOn(window.history, 'pushState');
      const replace = vi.spyOn(window.history, 'replaceState');
      render(
        <>
          <SkipToMainContent />
          <main id="main-content" tabIndex={-1}>Content</main>
        </>,
      );

      fireEvent.click(screen.getByRole('link', { name: 'Skip to main content' }));

      expect(document.querySelector('main')).toHaveFocus();
      expect(window.location.hash).toBe(hash);
      expect(window.history.state).toEqual({ existing: true });
      expect(push).not.toHaveBeenCalled();
      expect(replace).not.toHaveBeenCalled();
      push.mockRestore();
      replace.mockRestore();
    },
  );

  it('provides a focusable semantic destination heading', () => {
    const ref = createRef<HTMLHeadingElement>();
    render(<RoutePageHeading ref={ref} activeTab="channel-pipeline" />);
    ref.current?.focus();
    expect(screen.getByRole('heading', { level: 1, name: 'Channel Pipeline' })).toHaveFocus();
  });
});

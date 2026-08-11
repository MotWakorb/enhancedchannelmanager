import { useRef, useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ModalOverlay } from '../components/ModalOverlay';
import { useModalFocusLifecycle } from './useModalFocusLifecycle';

function TestDialog({ name, onClose }: { name: string; onClose: () => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const initialFocusRef = useRef<HTMLButtonElement>(null);
  useModalFocusLifecycle({ containerRef, initialFocusRef });
  return (
    <ModalOverlay onClose={onClose} role="dialog" aria-modal="true" aria-label={name}>
      <div ref={containerRef}>
        <button ref={initialFocusRef}>First {name}</button>
        <button>Last {name}</button>
      </div>
    </ModalOverlay>
  );
}

describe('useModalFocusLifecycle', () => {
  it('focuses initially, traps Tab in the topmost dialog, and restores its opener', async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return <><button onClick={() => setOpen(true)}>Open</button>{open && <TestDialog name="Test" onClose={() => setOpen(false)} />}</>;
    }
    render(<Harness />);
    const opener = screen.getByRole('button', { name: 'Open' });
    opener.focus();
    fireEvent.click(opener);
    const first = await screen.findByRole('button', { name: 'First Test' });
    const last = screen.getByRole('button', { name: 'Last Test' });
    await waitFor(() => expect(first).toHaveFocus());
    last.focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(first).toHaveFocus();
    first.focus();
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
    expect(last).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it('ignores a disabled preferred target and focuses the first eligible control', async () => {
    function DisabledPreferredDialog() {
      const containerRef = useRef<HTMLDivElement>(null);
      const preferredRef = useRef<HTMLButtonElement>(null);
      useModalFocusLifecycle({ containerRef, initialFocusRef: preferredRef });
      return <ModalOverlay onClose={() => {}} role="dialog" aria-modal="true" aria-label="Disabled preferred">
        <div ref={containerRef}><button ref={preferredRef} disabled>Disabled</button><button>Fallback</button></div>
      </ModalOverlay>;
    }
    render(<DisabledPreferredDialog />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Fallback' })).toHaveFocus());
  });

  it('recaptures focus from outside the topmost dialog at the correct edge', async () => {
    render(<><TestDialog name="Recapture" onClose={() => {}} /><button>Outside</button></>);
    const first = screen.getByRole('button', { name: 'First Recapture' });
    const last = screen.getByRole('button', { name: 'Last Recapture' });
    await waitFor(() => expect(first).toHaveFocus());
    const outside = screen.getByRole('button', { name: 'Outside' });
    outside.focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(first).toHaveFocus();
    outside.focus();
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
    expect(last).toHaveFocus();
  });

  it('temporarily makes an empty container focusable and restores its tabindex on cleanup', async () => {
    let container: HTMLDivElement | null = null;
    function EmptyDialog() {
      const containerRef = useRef<HTMLDivElement>(null);
      useModalFocusLifecycle({ containerRef });
      return <ModalOverlay onClose={() => {}} role="dialog" aria-modal="true" aria-label="Empty">
        <div ref={(node) => { containerRef.current = node; container = node; }} />
      </ModalOverlay>;
    }
    const { unmount } = render(<EmptyDialog />);
    await waitFor(() => expect(container).toHaveFocus());
    expect(container).toHaveAttribute('tabindex', '-1');
    const detached = container;
    unmount();
    expect(detached).not.toHaveAttribute('tabindex');
  });

  it('keeps the parent dormant and restores nested focus in stack order', async () => {
    function NestedHarness() {
      const [parent, setParent] = useState(false);
      const [child, setChild] = useState(false);
      const parentContainer = useRef<HTMLDivElement>(null);
      const parentInitial = useRef<HTMLButtonElement>(null);
      useModalFocusLifecycle({ containerRef: parentContainer, initialFocusRef: parentInitial, active: parent });
      return <>
        <button onClick={() => setParent(true)}>Open parent</button>
        {parent && <ModalOverlay onClose={() => setParent(false)} role="dialog" aria-modal="true" aria-label="Parent">
          <div ref={parentContainer}><button ref={parentInitial} onClick={() => setChild(true)}>Open child</button><button>Parent last</button></div>
        </ModalOverlay>}
        {child && <TestDialog name="Child" onClose={() => setChild(false)} />}
      </>;
    }
    render(<NestedHarness />);
    const rootOpener = screen.getByRole('button', { name: 'Open parent' });
    rootOpener.focus(); fireEvent.click(rootOpener);
    const childOpener = await screen.findByRole('button', { name: 'Open child' });
    await waitFor(() => expect(childOpener).toHaveFocus());
    fireEvent.click(childOpener);
    const childFirst = await screen.findByRole('button', { name: 'First Child' });
    await waitFor(() => expect(childFirst).toHaveFocus());
    const childLast = screen.getByRole('button', { name: 'Last Child' });
    childLast.focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(childFirst).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(childOpener).toHaveFocus());
    expect(screen.getByRole('dialog', { name: 'Parent' })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(rootOpener).toHaveFocus());
  });

});

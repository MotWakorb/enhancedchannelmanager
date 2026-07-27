import { createRef } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PageHeader } from './PageHeader';

describe('PageHeader', () => {
  it('renders the primary page hierarchy with one semantic h1', () => {
    const ref = createRef<HTMLHeadingElement>();
    render(
      <PageHeader
        headingLevel={1}
        headingRef={ref}
        group="OPERATIONS"
        title="CHANNEL MANAGER"
        description="Build and maintain the channel lineup and its assigned streams."
        actions={<button type="button">Edit Mode</button>}
      />,
    );
    const heading = screen.getByRole('heading', { level: 1 });
    const action = screen.getByRole('button', { name: 'Edit Mode' });
    expect(heading).toHaveTextContent('OPERATIONS / CHANNEL MANAGER');
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(screen.getByText('Build and maintain the channel lineup and its assigned streams.')).toBeVisible();
    expect(ref.current).toBe(heading);
    expect(heading.compareDocumentPosition(action) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('renders stable contextual settings links without taking over native modified clicks', () => {
    const onClick = vi.fn((event: React.MouseEvent<HTMLAnchorElement>) => event.preventDefault());
    render(
      <PageHeader
        title="Channel Pipeline"
        relatedLinks={[{ href: '#settings/channel-pipeline', label: 'Channel Pipeline settings', onClick }]}
      />,
    );
    const link = screen.getByRole('link', { name: 'Channel Pipeline settings' });
    expect(link).toHaveAttribute('href', '#settings/channel-pipeline');
    fireEvent.click(link);
    expect(onClick).toHaveBeenCalledOnce();
  });
});

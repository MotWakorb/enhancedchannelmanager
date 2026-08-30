import type { ReactNode } from 'react';
import './DenseToolbar.css';

export interface DenseToolbarProps {
  label: string;
  search?: ReactNode;
  filters?: ReactNode;
  sortView?: ReactNode;
  selection?: ReactNode;
  bulkActions?: ReactNode;
  secondaryActions?: ReactNode;
}

export function DenseToolbar({
  label,
  search,
  filters,
  sortView,
  selection,
  bulkActions,
  secondaryActions,
}: DenseToolbarProps) {
  const group = (name: string, content?: ReactNode) => content
    ? <div className={`dense-toolbar-group dense-toolbar-${name}`} role="group" aria-label={name.replace('-', ' ')}>
        {content}
      </div>
    : null;

  return <div className="dense-toolbar" role="toolbar" aria-label={label}>
    {group('search', search)}
    {group('filters', filters)}
    {group('sort-view', sortView)}
    {group('selection', selection)}
    {group('bulk-actions', bulkActions)}
    {group('secondary-actions', secondaryActions)}
  </div>;
}

import type { MouseEventHandler, ReactNode, Ref } from 'react';
import './PageHeader.css';

interface PageHeaderProps {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  status?: ReactNode;
  controls?: ReactNode;
  group?: string;
  headingLevel?: 1 | 2;
  headingRef?: Ref<HTMLHeadingElement>;
  relatedLinks?: Array<{
    href: string;
    label: string;
    onClick?: MouseEventHandler<HTMLAnchorElement>;
  }>;
  /**
   * Extra class name(s) applied to the outer row, for tabs whose own CSS
   * still targets a legacy wrapper class (e.g. `.logo-header` in a
   * `@media (max-width: 600px)` rule that stacks the row vertically).
   */
  className?: string;
}

/**
 * Shared title/description/toolbar header row used at the top of each
 * manager tab (M3U Manager, EPG Sources, Logo Manager, Dummy EPG
 * Profiles, ...).
 *
 * Extracted (bd-7l7wi) after the identical
 * `display:flex; justify-content:space-between; align-items:flex-start`
 * markup had been hand-duplicated per tab (`.m3u-header`, `.epg-header`,
 * `.logo-header`, `.dep-manager-header`). That duplication is exactly what
 * let the EPG Dummy Profiles header wrap to a ~250px column (bd-b3g0r) in
 * one tab and not others — each copy could independently drift, and only
 * a long-enough title + wide-enough toolbar happened to trip the bug.
 * `.header-title` / `.header-description` / `.header-actions` are the
 * pre-existing shared classes (see shared/common.css § TAB HEADERS); this
 * component just standardizes the wrapper row so the layout can't diverge
 * per tab again.
 */
export function PageHeader({
  title,
  description,
  actions,
  status,
  controls,
  className,
  group,
  headingLevel = 2,
  headingRef,
  relatedLinks,
}: PageHeaderProps) {
  const heading = group ? `${group} / ${title}` : title;
  return (
    <div className={`page-header${className ? ` ${className}` : ''}`}>
      <div className="header-title">
        {headingLevel === 1
          ? <h1 ref={headingRef} tabIndex={-1}>{heading}</h1>
          : <h2 ref={headingRef}>{heading}</h2>}
        {description && <p className="header-description">{description}</p>}
      </div>
      {actions && <div className="header-actions" data-page-header-slot="primary-action">{actions}</div>}
      {status && <div className="page-header-status" data-page-header-slot="status" aria-live="polite">{status}</div>}
      {controls && <div className="page-header-controls" data-page-header-slot="controls">{controls}</div>}
      {relatedLinks && relatedLinks.length > 0 && (
        <nav className="page-header-related-links" aria-label="Related settings">
          {relatedLinks.map((link) => (
            <a key={link.href} href={link.href} onClick={link.onClick}>{link.label}</a>
          ))}
        </nav>
      )}
    </div>
  );
}

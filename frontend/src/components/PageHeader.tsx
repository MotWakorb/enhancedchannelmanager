import type { MouseEventHandler, ReactNode, Ref } from 'react';
import './PageHeader.css';

interface PageHeaderProps {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  status?: ReactNode;
  statusLive?: boolean;
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
  statusLive = false,
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
        {description && (
          <p
            className="header-description"
            title={typeof description === 'string' ? description : undefined}
          >
            {description}
          </p>
        )}
      </div>
      {actions && <div className="header-actions" data-page-header-slot="primary-action">{actions}</div>}
      {controls && <div className="page-header-controls" data-page-header-slot="controls">{controls}</div>}
      {/* Status and related links share one full-width meta row under the
          title. The status used to be a flex item of the header row itself,
          wedged between the primary action and the controls toolbar — and
          since `.page-header` declared only a `row-gap`, it sat edge-to-edge
          against both ("6 provider accounts" touching Add M3U Account on one
          side and Save Priorities on the other at 1600x1000). Grouping it
          with the related links puts every non-interactive element on one
          line, left-aligned with the title, and leaves the row above as a
          pure identity + actions line. Bead enhancedchannelmanager-sccol;
          this supersedes the action/status/controls interleave from 57pp3,
          which is what produced the collision. */}
      {(status || (relatedLinks && relatedLinks.length > 0)) && (
        <div className="page-header-meta">
          {status && (
            <div
              className="page-header-status"
              data-page-header-slot="status"
              {...(statusLive ? { 'aria-live': 'polite' as const } : {})}
            >
              {status}
            </div>
          )}
          {relatedLinks && relatedLinks.length > 0 && (
            <nav className="page-header-related-links" aria-label="Related settings">
              {relatedLinks.map((link) => (
                <a key={link.href} href={link.href} onClick={link.onClick}>{link.label}</a>
              ))}
            </nav>
          )}
        </div>
      )}
    </div>
  );
}

import { forwardRef, type MouseEvent } from 'react';
import type { TabId } from './TabNavigation';
import { ROUTE_TITLES } from './routeTitles';

export function SkipToMainContent() {
  const focusMain = (event: MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault();
    document.getElementById('main-content')?.focus();
  };

  return <a className="skip-link" href="#main-content" onClick={focusMain}>Skip to main content</a>;
}

export const RoutePageHeading = forwardRef<HTMLHeadingElement, { activeTab: TabId }>(
  function RoutePageHeading({ activeTab }, ref) {
    return (
      <h1 ref={ref} className="route-page-heading" tabIndex={-1}>
        {ROUTE_TITLES[activeTab]}
      </h1>
    );
  },
);

import type { MouseEvent } from 'react';

export function SkipToMainContent() {
  const focusMain = (event: MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault();
    document.getElementById('main-content')?.focus();
  };

  return <a className="skip-link" href="#main-content" onClick={focusMain}>Skip to main content</a>;
}

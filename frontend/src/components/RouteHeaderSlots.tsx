/* eslint-disable react-refresh/only-export-components -- the shared target hook and its provider must use the same context */
import { createContext, type ReactNode, useContext } from 'react';
import { createPortal } from 'react-dom';

const RouteHeaderActionTargetContext = createContext<HTMLElement | null>(null);

export function RouteHeaderActionTargetProvider({
  target,
  children,
}: {
  target: HTMLElement | null;
  children: ReactNode;
}) {
  return (
    <RouteHeaderActionTargetContext.Provider value={target}>
      {children}
    </RouteHeaderActionTargetContext.Provider>
  );
}

export function RouteHeaderActions({ children }: { children: ReactNode }) {
  const target = useContext(RouteHeaderActionTargetContext);
  return target ? createPortal(children, target) : <>{children}</>;
}

export function useRouteHeaderActionTarget() {
  return useContext(RouteHeaderActionTargetContext);
}

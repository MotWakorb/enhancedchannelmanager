import { createContext, type ReactNode, useContext } from 'react';
import { createPortal } from 'react-dom';

export type RouteHeaderSlotName = 'primary-action' | 'status' | 'controls';
export type RouteHeaderTargets = Record<RouteHeaderSlotName, HTMLElement | null>;

const RouteHeaderTargetsContext = createContext<RouteHeaderTargets>({
  'primary-action': null,
  status: null,
  controls: null,
});

export function RouteHeaderTargetProvider({
  targets,
  children,
}: {
  targets: RouteHeaderTargets;
  children: ReactNode;
}) {
  return (
    <RouteHeaderTargetsContext.Provider value={targets}>
      {children}
    </RouteHeaderTargetsContext.Provider>
  );
}

export function RouteHeaderSlot({
  name,
  children,
}: {
  name: RouteHeaderSlotName;
  children: ReactNode;
}) {
  const target = useContext(RouteHeaderTargetsContext)[name];
  return target
    ? createPortal(<div data-route-header-slot={name}>{children}</div>, target)
    : <div data-route-header-slot={name}>{children}</div>;
}

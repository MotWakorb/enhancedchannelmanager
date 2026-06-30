/**
 * BackupDestinationPromptContext — owns WHEN the backup-destination first-run
 * choice (SecurityFirstRunModal) appears (bead s5a3o).
 *
 * Rationale: the modal used to mount unconditionally on login, which wrongly
 * implied ECM was already sending backups somewhere. It must instead appear
 * only when the operator FIRST actively configures backups — on whichever of
 * these happens first:
 *   (A) enabling / creating a backup schedule (dbas_backup), or
 *   (B) adding / saving a cloud upload target.
 *
 * Those two flows live deep in the tree (Settings → Backup & Restore and the
 * task editor), so a context — matching the codebase's NotificationContext
 * cross-cutting pattern — is the minimal way to let them open a modal rendered
 * once at the app root. `promptBackupDestination()` is the single entry point;
 * it is a no-op once the operator has already answered (SECURITY_FIRST_RUN_KEY
 * === '1'), preserving the "never re-prompt" guarantee.
 */
import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';
import {
  SecurityFirstRunModal,
  SECURITY_FIRST_RUN_KEY,
} from '../components/SecurityFirstRunModal';

interface BackupDestinationPromptValue {
  /**
   * Open the backup-destination choice IF the operator has not already answered
   * it. No-op once answered (localStorage flag set) — existing operators are
   * never re-prompted. Non-blocking: callers should not await or branch on it,
   * the underlying configure action proceeds regardless.
   */
  promptBackupDestination: () => void;
}

const BackupDestinationPromptContext = createContext<BackupDestinationPromptValue | null>(
  null,
);

export function BackupDestinationPromptProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);

  const promptBackupDestination = useCallback(() => {
    // Respect "already answered": the localStorage flag is the single source of
    // truth for whether the operator has made (or skipped) the choice. Never
    // re-open once it is set, even mid-configuration.
    if (localStorage.getItem(SECURITY_FIRST_RUN_KEY) === '1') return;
    setOpen(true);
  }, []);

  return (
    <BackupDestinationPromptContext.Provider value={{ promptBackupDestination }}>
      {children}
      {open && <SecurityFirstRunModal onClose={() => setOpen(false)} />}
    </BackupDestinationPromptContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components -- hook + provider co-located by convention (matches NotificationContext)
export function useBackupDestinationPrompt(): BackupDestinationPromptValue {
  const context = useContext(BackupDestinationPromptContext);
  if (!context) {
    throw new Error(
      'useBackupDestinationPrompt must be used within a BackupDestinationPromptProvider',
    );
  }
  return context;
}

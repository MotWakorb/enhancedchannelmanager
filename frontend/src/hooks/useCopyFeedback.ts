import { useState, useCallback, useRef } from 'react';
import { copyToClipboard } from '../utils/clipboard';

export interface UseCopyFeedbackReturn {
  copySuccess: string | null;
  copyError: string | null;
  handleCopy: (url: string, itemName: string) => Promise<void>;
}

/**
 * Hook to manage copy feedback state and handle clipboard operations
 *
 * Consolidates duplicate copy feedback logic from ChannelsPane and StreamsPane.
 * Manages success/error state with automatic timeout clearing.
 *
 * @returns Object containing copySuccess, copyError state and handleCopy function
 */
export function useCopyFeedback(): UseCopyFeedbackReturn {
  const [copySuccess, setCopySuccess] = useState<string | null>(null);
  const [copyError, setCopyError] = useState<string | null>(null);

  // Store timeout IDs to clear them if component unmounts
  const successTimeoutRef = useRef<number | null>(null);
  const errorTimeoutRef = useRef<number | null>(null);

  const handleCopy = useCallback(async (url: string, itemName: string) => {
    // Clear any existing timeouts
    if (successTimeoutRef.current) {
      clearTimeout(successTimeoutRef.current);
    }
    if (errorTimeoutRef.current) {
      clearTimeout(errorTimeoutRef.current);
    }

    const success = await copyToClipboard(url, `URL for "${itemName}"`);

    if (success) {
      setCopySuccess(`Copied URL for "${itemName}"`);
      setCopyError(null);
      // Clear success message after 3 seconds
      successTimeoutRef.current = setTimeout(() => {
        setCopySuccess(null);
        successTimeoutRef.current = null;
      }, 3000);
    } else {
      setCopyError('Failed to copy to clipboard. Please check browser permissions and try again.');
      setCopySuccess(null);
      // Clear error message after 5 seconds
      errorTimeoutRef.current = setTimeout(() => {
        setCopyError(null);
        errorTimeoutRef.current = null;
      }, 5000);
    }
  }, []);

  return {
    copySuccess,
    copyError,
    handleCopy,
  };
}

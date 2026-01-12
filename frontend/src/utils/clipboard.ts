/**
 * Clipboard utility with error handling and user feedback
 */

import { logger } from './logger';

/**
 * Copy text to clipboard with proper error handling
 * @param text The text to copy
 * @param description Optional description of what's being copied (for logging)
 * @returns Promise that resolves to true if successful, false if failed
 */
export async function copyToClipboard(text: string, description: string = 'text'): Promise<boolean> {
  try {
    // Check if clipboard API is available
    if (!navigator.clipboard || !navigator.clipboard.writeText) {
      logger.error('Clipboard API not available - may require HTTPS or localhost');
      return false;
    }

    // Attempt to write to clipboard
    await navigator.clipboard.writeText(text);
    logger.info(`Copied ${description} to clipboard: ${text.substring(0, 50)}${text.length > 50 ? '...' : ''}`);
    return true;
  } catch (error) {
    // Log detailed error information
    if (error instanceof Error) {
      logger.error(`Failed to copy ${description} to clipboard: ${error.message}`);

      // Provide specific guidance based on error type
      if (error.name === 'NotAllowedError') {
        logger.warn('Clipboard access denied - check browser permissions or ensure HTTPS is used');
      } else if (error.name === 'SecurityError') {
        logger.warn('Clipboard access blocked by security policy - may require HTTPS or localhost');
      }
    } else {
      logger.error(`Failed to copy ${description} to clipboard: Unknown error`);
    }

    return false;
  }
}

/**
 * Copy text to clipboard with visual feedback (shows temporary message)
 * @param text The text to copy
 * @param description Optional description of what's being copied
 * @param onSuccess Optional callback for successful copy
 * @param onError Optional callback for failed copy
 * @returns Promise that resolves to true if successful, false if failed
 */
export async function copyToClipboardWithFeedback(
  text: string,
  description: string = 'text',
  onSuccess?: () => void,
  onError?: (errorMessage: string) => void
): Promise<boolean> {
  const success = await copyToClipboard(text, description);

  if (success) {
    onSuccess?.();
  } else {
    const errorMessage = 'Failed to copy to clipboard. Ensure you are using HTTPS or localhost, and clipboard permissions are granted.';
    onError?.(errorMessage);
  }

  return success;
}

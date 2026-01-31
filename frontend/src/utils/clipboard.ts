/**
 * Clipboard utility with error handling and user feedback
 * Supports both modern Clipboard API and legacy execCommand fallback for HTTP environments
 */

import { logger } from './logger';

/**
 * Fallback copy method using deprecated execCommand (works over HTTP)
 * @param text The text to copy
 * @returns boolean indicating success
 */
function copyWithExecCommand(text: string): boolean {
  // Create a temporary textarea element
  const textarea = document.createElement('textarea');
  textarea.value = text;

  // Position it in a way that works across browsers
  // Using clip rect instead of negative positioning for better compatibility
  textarea.style.position = 'fixed';
  textarea.style.top = '0';
  textarea.style.left = '0';
  textarea.style.width = '2em';
  textarea.style.height = '2em';
  textarea.style.padding = '0';
  textarea.style.border = 'none';
  textarea.style.outline = 'none';
  textarea.style.boxShadow = 'none';
  textarea.style.background = 'transparent';
  // Use clip to hide instead of opacity (some browsers ignore opacity for copy)
  textarea.style.clip = 'rect(0, 0, 0, 0)';
  // Don't use readonly - some browsers won't copy from readonly elements

  document.body.appendChild(textarea);

  try {
    // Focus first, then select (required for some browsers)
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, text.length);

    // Execute copy command
    const success = document.execCommand('copy');
    console.warn('[CLIPBOARD-DEBUG] execCommand returned:', success);

    return success;
  } catch (error) {
    console.warn('[CLIPBOARD-DEBUG] execCommand threw error:', error);
    logger.error('execCommand copy failed:', error);
    return false;
  } finally {
    // Remove focus before cleanup
    textarea.blur();
    document.body.removeChild(textarea);
  }
}

/**
 * Copy text to clipboard with proper error handling
 * Tries modern Clipboard API first, falls back to execCommand for HTTP environments
 * @param text The text to copy
 * @param description Optional description of what's being copied (for logging)
 * @returns Promise that resolves to true if successful, false if failed
 */
export async function copyToClipboard(text: string, description: string = 'text'): Promise<boolean> {
  console.warn('[CLIPBOARD-DEBUG] copyToClipboard called', { text: text.substring(0, 50), description });

  // Try modern Clipboard API first (works on HTTPS/localhost)
  if (navigator.clipboard && navigator.clipboard.writeText) {
    console.warn('[CLIPBOARD-DEBUG] Trying Clipboard API');
    try {
      await navigator.clipboard.writeText(text);
      console.warn('[CLIPBOARD-DEBUG] Clipboard API succeeded');
      logger.info(`Copied ${description} to clipboard (Clipboard API): ${text.substring(0, 50)}${text.length > 50 ? '...' : ''}`);
      return true;
    } catch (error) {
      // Log the error but continue to fallback
      console.warn('[CLIPBOARD-DEBUG] Clipboard API failed', error);
      if (error instanceof Error) {
        logger.warn(`Clipboard API failed for ${description}: ${error.message}, trying fallback method`);
      }
    }
  } else {
    console.warn('[CLIPBOARD-DEBUG] Clipboard API not available');
  }

  // Fallback to execCommand (works over HTTP)
  console.warn('[CLIPBOARD-DEBUG] Trying execCommand fallback');
  logger.debug(`Using execCommand fallback for ${description}`);
  const success = copyWithExecCommand(text);

  if (success) {
    console.warn('[CLIPBOARD-DEBUG] execCommand succeeded');
    logger.info(`Copied ${description} to clipboard (execCommand): ${text.substring(0, 50)}${text.length > 50 ? '...' : ''}`);
    return true;
  } else {
    console.warn('[CLIPBOARD-DEBUG] execCommand failed');
    logger.error(`Failed to copy ${description} to clipboard with both methods`);
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
    const errorMessage = 'Failed to copy to clipboard. Please check browser permissions and try again.';
    onError?.(errorMessage);
  }

  return success;
}

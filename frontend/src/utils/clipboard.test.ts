/**
 * Unit tests for clipboard utility.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { copyToClipboard, copyToClipboardWithFeedback } from './clipboard';

describe('clipboard', () => {
  let originalClipboard: Clipboard | undefined;
  let originalExecCommand: typeof document.execCommand;

  beforeEach(() => {
    // Save originals
    originalClipboard = navigator.clipboard;
    originalExecCommand = document.execCommand;

    // Reset mocks
    vi.clearAllMocks();
  });

  afterEach(() => {
    // Restore originals
    Object.defineProperty(navigator, 'clipboard', {
      value: originalClipboard,
      writable: true,
      configurable: true,
    });
    document.execCommand = originalExecCommand;
    vi.restoreAllMocks();
  });

  describe('copyToClipboard', () => {
    it('copies text using Clipboard API when available', async () => {
      const mockWriteText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: mockWriteText },
        writable: true,
        configurable: true,
      });

      const result = await copyToClipboard('test text');

      expect(result).toBe(true);
      expect(mockWriteText).toHaveBeenCalledWith('test text');
    });

    it('falls back to execCommand when Clipboard API is not available', async () => {
      // Remove Clipboard API
      Object.defineProperty(navigator, 'clipboard', {
        value: undefined,
        writable: true,
        configurable: true,
      });

      // Mock execCommand
      const mockExecCommand = vi.fn().mockReturnValue(true);
      document.execCommand = mockExecCommand;

      // Mock textarea creation
      const mockTextarea = {
        value: '',
        style: {} as CSSStyleDeclaration,
        setAttribute: vi.fn(),
        select: vi.fn(),
        setSelectionRange: vi.fn(),
        focus: vi.fn(),
        blur: vi.fn(),
      };
      vi.spyOn(document, 'createElement').mockReturnValue(mockTextarea as unknown as HTMLElement);
      vi.spyOn(document.body, 'appendChild').mockImplementation(() => mockTextarea as unknown as HTMLElement);
      vi.spyOn(document.body, 'removeChild').mockImplementation(() => mockTextarea as unknown as HTMLElement);

      const result = await copyToClipboard('fallback text');

      expect(result).toBe(true);
      expect(mockExecCommand).toHaveBeenCalledWith('copy');
      expect(mockTextarea.value).toBe('fallback text');
    });

    it('falls back to execCommand when Clipboard API fails', async () => {
      const mockWriteText = vi.fn().mockRejectedValue(new Error('Permission denied'));
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: mockWriteText },
        writable: true,
        configurable: true,
      });

      // Mock execCommand
      const mockExecCommand = vi.fn().mockReturnValue(true);
      document.execCommand = mockExecCommand;

      // Mock textarea creation
      const mockTextarea = {
        value: '',
        style: {} as CSSStyleDeclaration,
        setAttribute: vi.fn(),
        select: vi.fn(),
        setSelectionRange: vi.fn(),
        focus: vi.fn(),
        blur: vi.fn(),
      };
      vi.spyOn(document, 'createElement').mockReturnValue(mockTextarea as unknown as HTMLElement);
      vi.spyOn(document.body, 'appendChild').mockImplementation(() => mockTextarea as unknown as HTMLElement);
      vi.spyOn(document.body, 'removeChild').mockImplementation(() => mockTextarea as unknown as HTMLElement);

      const result = await copyToClipboard('fallback after failure');

      expect(result).toBe(true);
      expect(mockWriteText).toHaveBeenCalled();
      expect(mockExecCommand).toHaveBeenCalledWith('copy');
    });

    it('returns false when both methods fail', async () => {
      // Make Clipboard API fail
      const mockWriteText = vi.fn().mockRejectedValue(new Error('Permission denied'));
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: mockWriteText },
        writable: true,
        configurable: true,
      });

      // Make execCommand fail
      const mockExecCommand = vi.fn().mockReturnValue(false);
      document.execCommand = mockExecCommand;

      // Mock textarea creation
      const mockTextarea = {
        value: '',
        style: {} as CSSStyleDeclaration,
        setAttribute: vi.fn(),
        select: vi.fn(),
        setSelectionRange: vi.fn(),
        focus: vi.fn(),
        blur: vi.fn(),
      };
      vi.spyOn(document, 'createElement').mockReturnValue(mockTextarea as unknown as HTMLElement);
      vi.spyOn(document.body, 'appendChild').mockImplementation(() => mockTextarea as unknown as HTMLElement);
      vi.spyOn(document.body, 'removeChild').mockImplementation(() => mockTextarea as unknown as HTMLElement);

      const result = await copyToClipboard('failing text');

      expect(result).toBe(false);
    });

    it('includes description in logging', async () => {
      const mockWriteText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: mockWriteText },
        writable: true,
        configurable: true,
      });

      const result = await copyToClipboard('test', 'channel name');

      expect(result).toBe(true);
      expect(mockWriteText).toHaveBeenCalledWith('test');
    });

    it('truncates long text in logs', async () => {
      const mockWriteText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: mockWriteText },
        writable: true,
        configurable: true,
      });

      const longText = 'a'.repeat(100);
      const result = await copyToClipboard(longText);

      expect(result).toBe(true);
      expect(mockWriteText).toHaveBeenCalledWith(longText);
    });

    it('cleans up textarea element after execCommand', async () => {
      // Remove Clipboard API
      Object.defineProperty(navigator, 'clipboard', {
        value: undefined,
        writable: true,
        configurable: true,
      });

      const mockExecCommand = vi.fn().mockReturnValue(true);
      document.execCommand = mockExecCommand;

      const mockTextarea = {
        value: '',
        style: {} as CSSStyleDeclaration,
        setAttribute: vi.fn(),
        select: vi.fn(),
        setSelectionRange: vi.fn(),
        focus: vi.fn(),
        blur: vi.fn(),
      };

      const appendSpy = vi.spyOn(document.body, 'appendChild').mockImplementation(() => mockTextarea as unknown as HTMLElement);
      const removeSpy = vi.spyOn(document.body, 'removeChild').mockImplementation(() => mockTextarea as unknown as HTMLElement);
      vi.spyOn(document, 'createElement').mockReturnValue(mockTextarea as unknown as HTMLElement);

      await copyToClipboard('cleanup test');

      expect(appendSpy).toHaveBeenCalled();
      expect(removeSpy).toHaveBeenCalled();
    });
  });

  describe('copyToClipboardWithFeedback', () => {
    it('calls onSuccess callback when copy succeeds', async () => {
      const mockWriteText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: mockWriteText },
        writable: true,
        configurable: true,
      });

      const onSuccess = vi.fn();
      const onError = vi.fn();

      const result = await copyToClipboardWithFeedback('success text', 'test', onSuccess, onError);

      expect(result).toBe(true);
      expect(onSuccess).toHaveBeenCalled();
      expect(onError).not.toHaveBeenCalled();
    });

    it('calls onError callback when copy fails', async () => {
      // Make Clipboard API fail
      const mockWriteText = vi.fn().mockRejectedValue(new Error('Permission denied'));
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: mockWriteText },
        writable: true,
        configurable: true,
      });

      // Make execCommand fail
      const mockExecCommand = vi.fn().mockReturnValue(false);
      document.execCommand = mockExecCommand;

      // Mock textarea creation
      const mockTextarea = {
        value: '',
        style: {} as CSSStyleDeclaration,
        setAttribute: vi.fn(),
        select: vi.fn(),
        setSelectionRange: vi.fn(),
        focus: vi.fn(),
        blur: vi.fn(),
      };
      vi.spyOn(document, 'createElement').mockReturnValue(mockTextarea as unknown as HTMLElement);
      vi.spyOn(document.body, 'appendChild').mockImplementation(() => mockTextarea as unknown as HTMLElement);
      vi.spyOn(document.body, 'removeChild').mockImplementation(() => mockTextarea as unknown as HTMLElement);

      const onSuccess = vi.fn();
      const onError = vi.fn();

      const result = await copyToClipboardWithFeedback('fail text', 'test', onSuccess, onError);

      expect(result).toBe(false);
      expect(onSuccess).not.toHaveBeenCalled();
      expect(onError).toHaveBeenCalledWith(expect.stringContaining('Failed to copy'));
    });

    it('works without callbacks', async () => {
      const mockWriteText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: mockWriteText },
        writable: true,
        configurable: true,
      });

      const result = await copyToClipboardWithFeedback('no callbacks');

      expect(result).toBe(true);
    });

    it('uses default description when not provided', async () => {
      const mockWriteText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: mockWriteText },
        writable: true,
        configurable: true,
      });

      const result = await copyToClipboardWithFeedback('default desc');

      expect(result).toBe(true);
      expect(mockWriteText).toHaveBeenCalledWith('default desc');
    });
  });
});

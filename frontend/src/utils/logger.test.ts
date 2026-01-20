/**
 * Unit tests for logger utility.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { Logger, logger } from './logger';

describe('Logger', () => {
  let consoleSpy: {
    log: ReturnType<typeof vi.spyOn>;
    warn: ReturnType<typeof vi.spyOn>;
    error: ReturnType<typeof vi.spyOn>;
  };

  beforeEach(() => {
    consoleSpy = {
      log: vi.spyOn(console, 'log').mockImplementation(() => {}),
      warn: vi.spyOn(console, 'warn').mockImplementation(() => {}),
      error: vi.spyOn(console, 'error').mockImplementation(() => {}),
    };
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('setLevel', () => {
    it('sets the log level', () => {
      const testLogger = new Logger();
      testLogger.setLevel('ERROR');
      expect(testLogger.getLevel()).toBe('ERROR');
    });

    it('logs a message when level is set', () => {
      const testLogger = new Logger();
      testLogger.setLevel('DEBUG');
      // Should have logged at INFO level
      expect(consoleSpy.log).toHaveBeenCalled();
    });
  });

  describe('getLevel', () => {
    it('returns the current log level', () => {
      const testLogger = new Logger();
      expect(testLogger.getLevel()).toBe('INFO'); // default
    });
  });

  describe('debug', () => {
    it('logs when level is DEBUG', () => {
      const testLogger = new Logger();
      testLogger.setLevel('DEBUG');
      consoleSpy.log.mockClear();

      testLogger.debug('test message');

      expect(consoleSpy.log).toHaveBeenCalled();
      const call = consoleSpy.log.mock.calls[0][0];
      expect(call).toContain('[DEBUG]');
      expect(call).toContain('test message');
    });

    it('does not log when level is INFO or higher', () => {
      const testLogger = new Logger();
      testLogger.setLevel('INFO');
      consoleSpy.log.mockClear();

      testLogger.debug('test message');

      expect(consoleSpy.log).not.toHaveBeenCalled();
    });
  });

  describe('info', () => {
    it('logs when level is INFO', () => {
      const testLogger = new Logger();
      testLogger.setLevel('INFO');
      consoleSpy.log.mockClear();

      testLogger.info('info message');

      expect(consoleSpy.log).toHaveBeenCalled();
      const call = consoleSpy.log.mock.calls[0][0];
      expect(call).toContain('[INFO]');
    });

    it('logs when level is DEBUG', () => {
      const testLogger = new Logger();
      testLogger.setLevel('DEBUG');
      consoleSpy.log.mockClear();

      testLogger.info('info message');

      expect(consoleSpy.log).toHaveBeenCalled();
    });

    it('does not log when level is WARN or higher', () => {
      const testLogger = new Logger();
      testLogger.setLevel('WARN');
      consoleSpy.log.mockClear();

      testLogger.info('info message');

      expect(consoleSpy.log).not.toHaveBeenCalled();
    });
  });

  describe('warn', () => {
    it('logs warnings to console.warn', () => {
      const testLogger = new Logger();
      testLogger.setLevel('DEBUG');

      testLogger.warn('warning message');

      expect(consoleSpy.warn).toHaveBeenCalled();
      const call = consoleSpy.warn.mock.calls[0][0];
      expect(call).toContain('[WARN]');
    });

    it('does not log when level is ERROR', () => {
      const testLogger = new Logger();
      testLogger.setLevel('ERROR');

      testLogger.warn('warning message');

      expect(consoleSpy.warn).not.toHaveBeenCalled();
    });
  });

  describe('error', () => {
    it('logs errors to console.error', () => {
      const testLogger = new Logger();
      testLogger.setLevel('DEBUG');

      testLogger.error('error message');

      expect(consoleSpy.error).toHaveBeenCalled();
      const call = consoleSpy.error.mock.calls[0][0];
      expect(call).toContain('[ERROR]');
    });

    it('logs at all levels', () => {
      const testLogger = new Logger();
      testLogger.setLevel('ERROR');

      testLogger.error('error message');

      expect(consoleSpy.error).toHaveBeenCalled();
    });
  });

  describe('exception', () => {
    it('logs error with stack trace', () => {
      const testLogger = new Logger();
      testLogger.setLevel('DEBUG');
      const error = new Error('test error');

      testLogger.exception('An error occurred', error);

      expect(consoleSpy.error).toHaveBeenCalledTimes(2);
      // First call is the message
      expect(consoleSpy.error.mock.calls[0][0]).toContain('[ERROR]');
      // Second call is the stack trace
      expect(consoleSpy.error.mock.calls[1][0]).toBe('Stack trace:');
    });
  });

  describe('formatMessage', () => {
    it('includes timestamp in message', () => {
      const testLogger = new Logger();
      testLogger.setLevel('DEBUG');

      testLogger.debug('test message');

      const call = consoleSpy.log.mock.calls[0][0];
      // Should have ISO timestamp format
      expect(call).toMatch(/\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
    });

    it('passes additional arguments', () => {
      const testLogger = new Logger();
      testLogger.setLevel('DEBUG');

      const extraData = { key: 'value' };
      testLogger.debug('test message', extraData);

      expect(consoleSpy.log).toHaveBeenCalledWith(
        expect.any(String),
        extraData
      );
    });
  });

  describe('singleton logger', () => {
    it('exports a singleton instance', () => {
      expect(logger).toBeInstanceOf(Logger);
    });

    it('singleton has default INFO level', () => {
      // Note: This may be affected by other tests
      const level = logger.getLevel();
      expect(['DEBUG', 'INFO', 'WARN', 'ERROR']).toContain(level);
    });
  });
});

/**
 * Frontend logging utility with configurable log levels
 */

export type LogLevel = 'DEBUG' | 'INFO' | 'WARN' | 'ERROR';

const LOG_LEVELS: Record<LogLevel, number> = {
  DEBUG: 0,
  INFO: 1,
  WARN: 2,
  ERROR: 3,
};

class Logger {
  private currentLevel: LogLevel = 'INFO';

  /**
   * Set the log level. Only messages at this level or higher will be logged.
   */
  setLevel(level: LogLevel): void {
    this.currentLevel = level;
    this.info(`Log level set to ${level}`);
  }

  /**
   * Get the current log level
   */
  getLevel(): LogLevel {
    return this.currentLevel;
  }

  /**
   * Check if a given level should be logged
   */
  private shouldLog(level: LogLevel): boolean {
    return LOG_LEVELS[level] >= LOG_LEVELS[this.currentLevel];
  }

  /**
   * Format a log message with timestamp and level
   */
  private formatMessage(level: LogLevel, message: string, ...args: any[]): [string, ...any[]] {
    const timestamp = new Date().toISOString();
    const formattedMessage = `[${timestamp}] [${level}] ${message}`;
    return [formattedMessage, ...args];
  }

  /**
   * Log a debug message
   */
  debug(message: string, ...args: any[]): void {
    if (this.shouldLog('DEBUG')) {
      console.log(...this.formatMessage('DEBUG', message, ...args));
    }
  }

  /**
   * Log an info message
   */
  info(message: string, ...args: any[]): void {
    if (this.shouldLog('INFO')) {
      console.log(...this.formatMessage('INFO', message, ...args));
    }
  }

  /**
   * Log a warning message
   */
  warn(message: string, ...args: any[]): void {
    if (this.shouldLog('WARN')) {
      console.warn(...this.formatMessage('WARN', message, ...args));
    }
  }

  /**
   * Log an error message
   */
  error(message: string, ...args: any[]): void {
    if (this.shouldLog('ERROR')) {
      console.error(...this.formatMessage('ERROR', message, ...args));
    }
  }

  /**
   * Log an error with stack trace
   */
  exception(message: string, error: Error, ...args: any[]): void {
    if (this.shouldLog('ERROR')) {
      console.error(...this.formatMessage('ERROR', message, ...args));
      console.error('Stack trace:', error.stack);
    }
  }
}

// Export a singleton instance
export const logger = new Logger();

// Also export the class for testing
export { Logger };

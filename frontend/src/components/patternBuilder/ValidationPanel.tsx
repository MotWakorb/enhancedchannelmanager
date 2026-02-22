/**
 * Shows generated regex patterns and per-example validation results.
 * When an example doesn't match, shows which pattern(s) failed.
 */
import { memo, useMemo } from 'react';
import type { ValidationResult } from './types';
import { diagnoseRegexFailure } from './regexEngine';

interface ValidationPanelProps {
  titlePattern: string;
  timePattern: string;
  datePattern: string;
  results: ValidationResult[];
  hasAnnotations: boolean;
}

interface FailureInfo {
  label: string;
  reason: string | null;
}

function getFailureDetail(text: string, titlePattern: string, timePattern: string, datePattern: string): FailureInfo[] {
  const failures: FailureInfo[] = [];
  const tryRegex = (label: string, pattern: string) => {
    if (!pattern) return;
    try {
      if (!new RegExp(pattern).test(text)) {
        const reason = diagnoseRegexFailure(text, pattern);
        failures.push({ label, reason });
      }
    } catch {
      failures.push({ label, reason: 'invalid regex' });
    }
  };
  tryRegex('Title', titlePattern);
  tryRegex('Time', timePattern);
  tryRegex('Date', datePattern);
  return failures;
}

export const ValidationPanel = memo(function ValidationPanel({
  titlePattern,
  timePattern,
  datePattern,
  results,
  hasAnnotations,
}: ValidationPanelProps) {
  if (!hasAnnotations) {
    return (
      <div className="pb-validation pb-validation-empty">
        <span className="material-icons">info</span>
        <span>Select text in the canvas above and name your variables to generate patterns.</span>
      </div>
    );
  }

  const matchCount = results.filter(r => r.matched).length;
  const totalCount = results.length;

  // Compute failure details for non-matching examples
  const failureDetails = useMemo(() => {
    const details: Record<number, FailureInfo[]> = {};
    results.forEach((r, i) => {
      if (!r.matched) {
        details[i] = getFailureDetail(r.text, titlePattern, timePattern, datePattern);
      }
    });
    return details;
  }, [results, titlePattern, timePattern, datePattern]);

  return (
    <div className="pb-validation">
      <div className="pb-validation-header">
        <span className="pb-validation-title">Generated Patterns</span>
        {totalCount > 0 && (
          <span className={`pb-validation-summary ${matchCount === totalCount ? 'pb-all-match' : 'pb-some-fail'}`}>
            {matchCount}/{totalCount} matched
          </span>
        )}
      </div>

      <div className="pb-validation-patterns">
        {titlePattern && (
          <div className="pb-pattern-row">
            <span className="pb-pattern-label">Title</span>
            <code className="pb-pattern-value">{titlePattern}</code>
          </div>
        )}
        {timePattern && (
          <div className="pb-pattern-row">
            <span className="pb-pattern-label">Time</span>
            <code className="pb-pattern-value">{timePattern}</code>
          </div>
        )}
        {datePattern && (
          <div className="pb-pattern-row">
            <span className="pb-pattern-label">Date</span>
            <code className="pb-pattern-value">{datePattern}</code>
          </div>
        )}
      </div>

      {results.length > 0 && results.some(r => r.matched && r.groups) && (
        <div className="pb-validation-groups">
          <span className="pb-validation-groups-label">Captured from first match:</span>
          <code className="pb-validation-groups-code">
            {JSON.stringify(results.find(r => r.matched && r.groups)?.groups, null, 2)}
          </code>
        </div>
      )}

      {/* Failure detail for non-matching examples */}
      {results.some(r => !r.matched) && (
        <div className="pb-validation-failures">
          {results.map((r, i) => {
            if (r.matched) return null;
            const failed = failureDetails[i] || [];
            return (
              <div key={i} className="pb-validation-failure-row">
                <span className="material-icons pb-example-icon pb-no-match">cancel</span>
                <div className="pb-validation-failure-content">
                  <code className="pb-validation-failure-text">{r.text.length > 80 ? r.text.slice(0, 80) + '...' : r.text}</code>
                  {failed.length > 0 && (
                    <span className="pb-validation-failure-detail">
                      {failed.map(f => f.reason ? `${f.label}: ${f.reason}` : `${f.label}: no match`).join(' · ')}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
});

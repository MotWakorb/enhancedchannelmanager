import { useState, useMemo, useCallback, useRef } from 'react';
import type { FFMPEGBuilderState } from '../../types/ffmpegBuilder';
import { createStreamProfile } from '../../services/api';
import { useNotifications } from '../../contexts/NotificationContext';
import { generateCommand } from './commandGenerator';

// ---------------------------------------------------------------------------
// CommandPreview component
// ---------------------------------------------------------------------------

interface CommandPreviewProps {
  config: FFMPEGBuilderState;
  annotated?: boolean;
}

export function CommandPreview({ config, annotated: initialAnnotated }: CommandPreviewProps) {
  const notifications = useNotifications();
  const [highlightedIdx, setHighlightedIdx] = useState<number | null>(null);
  const [showAnnotated, setShowAnnotated] = useState(initialAnnotated ?? false);
  const [showPushForm, setShowPushForm] = useState(false);
  const [profileName, setProfileName] = useState('');
  const [pushStatus, setPushStatus] = useState<'idle' | 'pushing'>('idle');
  const nameInputRef = useRef<HTMLInputElement>(null);

  const generated = useMemo(() => {
    if (!config) return null;
    return generateCommand(config);
  }, [config]);

  const handleCopy = useCallback(async () => {
    if (!generated) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(generated.command);
      }
      notifications.success('Command copied to clipboard');
    } catch {
      notifications.error('Failed to copy to clipboard');
    }
  }, [generated, notifications]);

  const handlePushToDispatcharr = useCallback(async () => {
    if (!generated || !profileName.trim()) return;
    setPushStatus('pushing');
    try {
      // Split "ffmpeg <params>" into command="ffmpeg" and parameters="<params>"
      const fullCmd = generated.command;
      const spaceIdx = fullCmd.indexOf(' ');
      const command = spaceIdx > 0 ? fullCmd.substring(0, spaceIdx) : fullCmd;
      const parameters = spaceIdx > 0 ? fullCmd.substring(spaceIdx + 1) : '';
      await createStreamProfile({
        name: profileName.trim(),
        command,
        parameters,
        is_active: true,
      });
      notifications.success(`Stream profile "${profileName.trim()}" created in Dispatcharr`);
      setShowPushForm(false);
      setProfileName('');
      setPushStatus('idle');
    } catch (e: unknown) {
      notifications.error(e instanceof Error ? e.message : 'Failed to create profile');
      setPushStatus('idle');
    }
  }, [generated, profileName, notifications]);

  const handleAnnotationClick = (idx: number) => {
    setHighlightedIdx(idx);
  };

  if (!config) {
    return (
      <div data-testid="command-preview" className="command-preview">
        <div className="command-empty">No configuration — configure your input to begin</div>
      </div>
    );
  }

  if (!generated) {
    return (
      <div data-testid="command-preview" className="command-preview">
        <div>Command Preview</div>
      </div>
    );
  }

  return (
    <div data-testid="command-preview" className="command-preview">
      <div className="command-preview-header">
        <span>Command Preview</span>
        <div className="command-preview-actions">
          <button
            type="button"
            aria-label="Toggle annotated view"
            onClick={() => setShowAnnotated(!showAnnotated)}
          >
            {showAnnotated ? 'Plain' : 'Annotated'}
          </button>
          <button type="button" aria-label="Copy" onClick={handleCopy}>
            Copy
          </button>
          <button
            type="button"
            data-testid="push-to-dispatcharr"
            aria-label="Push to Dispatcharr"
            onClick={() => {
              setShowPushForm(!showPushForm);
              setPushStatus('idle');
              setTimeout(() => nameInputRef.current?.focus(), 50);
            }}
          >
            <span className="material-icons" style={{ fontSize: '0.875rem', verticalAlign: 'middle', marginRight: '0.25rem' }}>cloud_upload</span>
            Push to Dispatcharr
          </button>
        </div>
      </div>

      {/* Push to Dispatcharr inline form */}
      {showPushForm && (
        <div data-testid="push-form" className="push-to-dispatcharr-form">
          <div className="push-form-row">
            <input
              ref={nameInputRef}
              type="text"
              data-testid="push-profile-name"
              className="push-form-input"
              placeholder="Stream profile name..."
              value={profileName}
              onChange={(e) => setProfileName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handlePushToDispatcharr();
                if (e.key === 'Escape') setShowPushForm(false);
              }}
              disabled={pushStatus === 'pushing'}
            />
            <button
              type="button"
              data-testid="push-confirm"
              className="btn-primary"
              onClick={handlePushToDispatcharr}
              disabled={!profileName.trim() || pushStatus === 'pushing'}
            >
              {pushStatus === 'pushing' ? 'Creating...' : 'Create Profile'}
            </button>
            <button
              type="button"
              className="btn-cancel"
              onClick={() => { setShowPushForm(false); setPushStatus('idle'); }}
            >
              <span className="material-icons" style={{ fontSize: '1rem' }}>close</span>
            </button>
          </div>
        </div>
      )}

      {/* Command text with clickable flags */}
      <div data-testid="command-text" className="command-text">
        {generated.flags.map((flag, i) => (
          <span
            key={i}
            data-testid="command-flag"
            className={highlightedIdx === i ? 'flag-highlight' : ''}
            data-highlighted={highlightedIdx === i ? 'true' : undefined}
            onMouseEnter={() => setHighlightedIdx(i)}
            onMouseLeave={() => setHighlightedIdx(null)}
          >
            {flag.text}
            {highlightedIdx === i && (
              <div role="tooltip" className="tooltip flag-tooltip">{flag.explanation}</div>
            )}
            {' '}
          </span>
        ))}
      </div>

      {/* Warnings */}
      {generated.warnings.map((w, i) => (
        <div key={i} data-testid="command-warning" className="command-warning">
          {w}
        </div>
      ))}

      {/* Annotation list (toggled by Annotated button) */}
      {showAnnotated && (
        <div data-testid="annotation-list" className="annotation-list">
          {generated.annotations.map((ann, i) => (
            <div
              key={i}
              data-testid="annotation-item"
              data-category={ann.category}
              className={`annotation-item category-${ann.category}${highlightedIdx === i ? ' highlighted' : ''}`}
              onClick={() => handleAnnotationClick(i)}
            >
              <code className="annotation-flag">{ann.flag}</code>
              <span data-testid="annotation-explanation" className="annotation-explanation">
                {ann.explanation}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

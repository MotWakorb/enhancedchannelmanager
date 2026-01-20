/**
 * TagChip Component
 *
 * A chip component for displaying and managing normalization tags.
 * Supports built-in tags (toggleable) and custom tags (removable).
 */
import './TagChip.css';
import { NormalizationTagMode } from '../services/api';

export interface TagChipProps {
  /** The tag value to display */
  label: string;
  /** Whether the tag is enabled (for built-in tags) */
  enabled?: boolean;
  /** Callback when tag enabled state is toggled (for built-in tags) */
  onToggleEnabled?: () => void;
  /** Callback when tag is removed (for custom tags) */
  onRemove?: () => void;
  /** Tag matching mode (prefix, suffix, or both) */
  mode?: NormalizationTagMode;
  /** Show in compact mode (for modals) */
  compact?: boolean;
  /** Whether this is a custom tag (affects styling) */
  isCustom?: boolean;
}

/**
 * Get abbreviated mode label
 */
function getModeLabel(mode: NormalizationTagMode): string {
  switch (mode) {
    case 'prefix':
      return 'PRE';
    case 'suffix':
      return 'SUF';
    case 'both':
      return 'ANY';
    default:
      return '';
  }
}

export function TagChip({
  label,
  enabled = true,
  onToggleEnabled,
  onRemove,
  mode,
  compact = false,
  isCustom = false,
}: TagChipProps) {
  const isToggleable = !!onToggleEnabled;
  const isRemovable = !!onRemove;

  const handleClick = () => {
    if (isToggleable) {
      onToggleEnabled();
    }
  };

  const handleRemove = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onRemove) {
      onRemove();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (isToggleable && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      onToggleEnabled();
    }
  };

  const classNames = [
    'tag-chip',
    enabled ? 'tag-chip-enabled' : 'tag-chip-disabled',
    isToggleable && 'tag-chip-toggleable',
    isCustom && 'tag-chip-custom',
    compact && 'tag-chip-compact',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div
      className={classNames}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      role={isToggleable ? 'checkbox' : undefined}
      aria-checked={isToggleable ? enabled : undefined}
      tabIndex={isToggleable ? 0 : undefined}
      title={`${label}${mode ? ` (${mode})` : ''}`}
    >
      {isToggleable && (
        <span className="tag-chip-toggle">
          <span className="material-icons">check</span>
        </span>
      )}
      <span className="tag-chip-label">{label}</span>
      {mode && <span className="tag-chip-mode">{getModeLabel(mode)}</span>}
      {isRemovable && (
        <button
          className="tag-chip-remove"
          onClick={handleRemove}
          aria-label={`Remove ${label}`}
          title="Remove tag"
        >
          <span className="material-icons">close</span>
        </button>
      )}
    </div>
  );
}

export default TagChip;

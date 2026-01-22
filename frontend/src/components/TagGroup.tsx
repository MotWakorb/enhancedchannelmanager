/**
 * TagGroup Component
 *
 * An expandable group of normalization tags with a title, icon, and toggle capability.
 * Used to organize tags by category (country, league, network, etc.)
 */
import { useState } from 'react';
import './TagGroup.css';
import { TagChip } from './TagChip';
import { AddTagInput } from './AddTagInput';
import { NormalizationTagMode } from '../services/api';

export interface TagItem {
  /** Tag value */
  value: string;
  /** Whether tag is enabled (for built-in tags) */
  enabled: boolean;
  /** Tag matching mode */
  mode?: NormalizationTagMode;
  /** Whether this is a custom tag (removable) */
  isCustom?: boolean;
}

export interface TagGroupProps {
  /** Group title */
  title: string;
  /** Material icon name */
  icon: string;
  /** Optional description */
  description?: string;
  /** List of tags in this group */
  tags: TagItem[];
  /** Callback when a built-in tag is toggled */
  onToggleTag?: (value: string, enabled: boolean) => void;
  /** Callback when a custom tag is removed */
  onRemoveTag?: (value: string) => void;
  /** Whether to show the add custom tag input */
  showAddInput?: boolean;
  /** Callback when a custom tag is added */
  onAddTag?: (value: string, mode: NormalizationTagMode) => void;
  /** Whether the group starts expanded */
  defaultExpanded?: boolean;
  /** Show in compact mode */
  compact?: boolean;
}

export function TagGroup({
  title,
  icon,
  description,
  tags,
  onToggleTag,
  onRemoveTag,
  showAddInput = false,
  onAddTag,
  defaultExpanded = false,
  compact = false,
}: TagGroupProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  // Count enabled tags
  const enabledCount = tags.filter((t) => t.enabled).length;
  const totalCount = tags.length;

  const handleToggleTag = (tag: TagItem) => {
    if (onToggleTag) {
      onToggleTag(tag.value, !tag.enabled);
    }
  };

  const handleRemoveTag = (tag: TagItem) => {
    if (onRemoveTag) {
      onRemoveTag(tag.value);
    }
  };

  const handleEnableAll = () => {
    tags.forEach((tag) => {
      if (!tag.enabled && !tag.isCustom && onToggleTag) {
        onToggleTag(tag.value, true);
      }
    });
  };

  const handleDisableAll = () => {
    tags.forEach((tag) => {
      if (tag.enabled && !tag.isCustom && onToggleTag) {
        onToggleTag(tag.value, false);
      }
    });
  };

  // Separate built-in and custom tags
  const builtinTags = tags.filter((t) => !t.isCustom);
  const customTags = tags.filter((t) => t.isCustom);

  const classNames = [
    'tag-group',
    expanded && 'tag-group-expanded',
    compact && 'tag-group-compact',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={classNames}>
      <div
        className="tag-group-header"
        onClick={() => setExpanded(!expanded)}
        role="button"
        aria-expanded={expanded}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setExpanded(!expanded);
          }
        }}
      >
        <div className="tag-group-icon">
          <span className="material-icons">{icon}</span>
        </div>
        <span className="tag-group-title">{title}</span>
        <span className="tag-group-count">
          {enabledCount} / {totalCount}
        </span>
        <span className="tag-group-expand">
          <span className="material-icons">expand_more</span>
        </span>
      </div>
      <div className="tag-group-content">
        <div className="tag-group-inner">
          {description && (
            <p className="tag-group-description">{description}</p>
          )}
          {builtinTags.length > 0 ? (
            <>
              <div className="tag-group-tags">
                {builtinTags.map((tag) => (
                  <TagChip
                    key={tag.value}
                    label={tag.value}
                    enabled={tag.enabled}
                    onToggleEnabled={() => handleToggleTag(tag)}
                    mode={tag.mode}
                    compact={compact}
                  />
                ))}
              </div>
              {onToggleTag && (
                <div className="tag-group-actions">
                  <button
                    className="tag-group-action-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleEnableAll();
                    }}
                    type="button"
                  >
                    <span className="material-icons">check_box</span>
                    Enable All
                  </button>
                  <button
                    className="tag-group-action-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDisableAll();
                    }}
                    type="button"
                  >
                    <span className="material-icons">check_box_outline_blank</span>
                    Disable All
                  </button>
                </div>
              )}
            </>
          ) : (
            !customTags.length && (
              <div className="tag-group-empty">No tags in this group</div>
            )
          )}
          {/* Custom tags section */}
          {(customTags.length > 0 || showAddInput) && (
            <>
              {builtinTags.length > 0 && <hr className="tag-group-divider" />}
              {customTags.length > 0 && (
                <>
                  <div className="tag-group-section-label">Custom Tags</div>
                  <div className="tag-group-tags">
                    {customTags.map((tag) => (
                      <TagChip
                        key={tag.value}
                        label={tag.value}
                        enabled={tag.enabled}
                        onRemove={() => handleRemoveTag(tag)}
                        mode={tag.mode}
                        compact={compact}
                        isCustom
                      />
                    ))}
                  </div>
                </>
              )}
              {showAddInput && onAddTag && (
                <div style={{ marginTop: customTags.length > 0 ? '0.75rem' : 0 }}>
                  <AddTagInput
                    onAdd={onAddTag}
                    placeholder="Add custom tag..."
                    compact={compact}
                  />
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default TagGroup;

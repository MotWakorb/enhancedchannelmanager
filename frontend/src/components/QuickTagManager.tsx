/**
 * QuickTagManager Component
 *
 * Compact tag manager for modals (like bulk channel creation).
 * Shows summary badges for each group with expand-to-edit capability.
 */
import { useState, useMemo, useCallback } from 'react';
import './QuickTagManager.css';
import { TagChip } from './TagChip';
import { AddTagInput } from './AddTagInput';
import { TAG_GROUPS, TagGroupName } from '../constants/streamNormalization';
import { NormalizationSettings, NormalizationTag, NormalizationTagMode } from '../services/api';

export interface QuickTagManagerProps {
  /** Current normalization settings */
  settings: NormalizationSettings;
  /** Callback when settings change */
  onChange: (settings: NormalizationSettings) => void;
  /** Whether to start expanded */
  defaultExpanded?: boolean;
}

/**
 * Build the key for a disabled tag (group:value)
 */
function buildTagKey(group: TagGroupName, value: string): string {
  return `${group}:${value}`;
}

export function QuickTagManager({
  settings,
  onChange,
  defaultExpanded = false,
}: QuickTagManagerProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [expandedGroups, setExpandedGroups] = useState<Set<TagGroupName>>(new Set());

  // Calculate enabled counts per group
  const groupStats = useMemo(() => {
    const stats: Record<TagGroupName, { enabled: number; total: number }> = {
      country: { enabled: 0, total: 0 },
      league: { enabled: 0, total: 0 },
      network: { enabled: 0, total: 0 },
      quality: { enabled: 0, total: 0 },
      timezone: { enabled: 0, total: 0 },
    };

    (Object.keys(TAG_GROUPS) as TagGroupName[]).forEach((groupName) => {
      const group = TAG_GROUPS[groupName];
      stats[groupName].total = group.tags.length;
      stats[groupName].enabled = group.tags.filter((tag) => {
        const key = buildTagKey(groupName, tag);
        return !settings.disabledBuiltinTags.includes(key);
      }).length;
    });

    return stats;
  }, [settings.disabledBuiltinTags]);

  // Total active tags
  const totalActive = useMemo(() => {
    return (
      Object.values(groupStats).reduce((sum, g) => sum + g.enabled, 0) +
      settings.customTags.length
    );
  }, [groupStats, settings.customTags]);

  // Handle toggling a built-in tag
  const handleToggleTag = useCallback(
    (group: TagGroupName, value: string, enabled: boolean) => {
      const key = buildTagKey(group, value);
      let newDisabledTags: string[];

      if (enabled) {
        newDisabledTags = settings.disabledBuiltinTags.filter((k) => k !== key);
      } else {
        newDisabledTags = [...settings.disabledBuiltinTags, key];
      }

      onChange({
        ...settings,
        disabledBuiltinTags: newDisabledTags,
      });
    },
    [settings, onChange]
  );

  // Handle adding a custom tag
  const handleAddCustomTag = useCallback(
    (value: string, mode: NormalizationTagMode) => {
      const exists = settings.customTags.some(
        (t) => t.value.toUpperCase() === value.toUpperCase()
      );
      if (exists) return;

      const newTag: NormalizationTag = { value, mode };
      onChange({
        ...settings,
        customTags: [...settings.customTags, newTag],
      });
    },
    [settings, onChange]
  );

  // Handle removing a custom tag
  const handleRemoveCustomTag = useCallback(
    (value: string) => {
      onChange({
        ...settings,
        customTags: settings.customTags.filter((t) => t.value !== value),
      });
    },
    [settings, onChange]
  );

  // Toggle group expansion
  const toggleGroup = (group: TagGroupName) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(group)) {
        next.delete(group);
      } else {
        next.add(group);
      }
      return next;
    });
  };

  const classNames = [
    'quick-tag-manager',
    expanded && 'quick-tag-manager-expanded',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={classNames}>
      <div
        className="quick-tag-manager-header"
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
        <div className="quick-tag-manager-header-left">
          <span className="quick-tag-manager-title">Normalization Tags</span>
          <div className="quick-tag-manager-summary">
            <span className="quick-tag-manager-badge">
              <span className="quick-tag-manager-badge-count">{totalActive}</span>
              active
            </span>
          </div>
        </div>
        <span className="quick-tag-manager-expand">
          <span className="material-icons">expand_more</span>
        </span>
      </div>

      <div className="quick-tag-manager-content">
        <div className="quick-tag-manager-inner">
          {/* Group rows */}
          {(Object.keys(TAG_GROUPS) as TagGroupName[]).map((groupName) => {
            const group = TAG_GROUPS[groupName];
            const stats = groupStats[groupName];
            const isGroupExpanded = expandedGroups.has(groupName);

            return (
              <div
                key={groupName}
                className={`quick-tag-manager-group ${
                  isGroupExpanded ? 'quick-tag-manager-group-expanded' : ''
                }`}
              >
                <div
                  className="quick-tag-manager-group-header"
                  onClick={() => toggleGroup(groupName)}
                >
                  <span className="quick-tag-manager-group-icon">
                    <span className="material-icons">{group.icon}</span>
                  </span>
                  <span className="quick-tag-manager-group-name">{group.title}</span>
                  <span className="quick-tag-manager-group-count">
                    {stats.enabled}/{stats.total}
                  </span>
                  <span className="quick-tag-manager-group-toggle">
                    <span className="material-icons">expand_more</span>
                  </span>
                </div>
                <div className="quick-tag-manager-group-tags">
                  {group.tags.map((tag) => {
                    const key = buildTagKey(groupName, tag);
                    const isEnabled = !settings.disabledBuiltinTags.includes(key);
                    return (
                      <TagChip
                        key={tag}
                        label={tag}
                        enabled={isEnabled}
                        onToggleEnabled={() =>
                          handleToggleTag(groupName, tag, !isEnabled)
                        }
                        compact
                      />
                    );
                  })}
                </div>
              </div>
            );
          })}

          {/* Custom tags */}
          <div className="quick-tag-manager-custom">
            <div className="quick-tag-manager-custom-header">
              <span className="quick-tag-manager-custom-title">Custom</span>
              {settings.customTags.length > 0 && (
                <span className="quick-tag-manager-custom-count">
                  ({settings.customTags.length})
                </span>
              )}
            </div>
            {settings.customTags.length > 0 ? (
              <div className="quick-tag-manager-custom-tags">
                {settings.customTags.map((tag) => (
                  <TagChip
                    key={tag.value}
                    label={tag.value}
                    enabled
                    mode={tag.mode}
                    onRemove={() => handleRemoveCustomTag(tag.value)}
                    isCustom
                    compact
                  />
                ))}
              </div>
            ) : (
              <div className="quick-tag-manager-custom-empty">No custom tags</div>
            )}
            <AddTagInput
              onAdd={handleAddCustomTag}
              placeholder="Add custom..."
              compact
              inline
            />
          </div>
        </div>
      </div>
    </div>
  );
}

export default QuickTagManager;

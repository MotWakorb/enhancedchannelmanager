/**
 * NormalizationTagsSection Component
 *
 * Main container for the tag-based normalization UI in the Settings tab.
 * Orchestrates all tag groups and custom tags management.
 */
import { useMemo, useCallback } from 'react';
import './NormalizationTagsSection.css';
import { TagGroup, TagItem } from '../TagGroup';
import { TagChip } from '../TagChip';
import { AddTagInput } from '../AddTagInput';
import { TAG_GROUPS, TagGroupName } from '../../constants/streamNormalization';
import { NormalizationSettings, NormalizationTag, NormalizationTagMode } from '../../services/api';

export interface NormalizationTagsSectionProps {
  /** Current normalization settings */
  settings: NormalizationSettings;
  /** Callback when settings change */
  onChange: (settings: NormalizationSettings) => void;
}

/**
 * Build the key for a disabled tag (group:value)
 */
function buildTagKey(group: TagGroupName, value: string): string {
  return `${group}:${value}`;
}

/**
 * Parse a disabled tag key to get group and value
 */
function parseTagKey(key: string): { group: string; value: string } | null {
  const [group, ...rest] = key.split(':');
  if (!group || rest.length === 0) return null;
  return { group, value: rest.join(':') };
}

export function NormalizationTagsSection({
  settings,
  onChange,
}: NormalizationTagsSectionProps) {
  // Build tag items for each group
  const groupTagItems = useMemo(() => {
    const items: Record<TagGroupName, TagItem[]> = {
      country: [],
      league: [],
      network: [],
      quality: [],
      timezone: [],
    };

    // Process each group's built-in tags
    (Object.keys(TAG_GROUPS) as TagGroupName[]).forEach((groupName) => {
      const group = TAG_GROUPS[groupName];
      items[groupName] = group.tags.map((tag) => {
        const key = buildTagKey(groupName, tag);
        const isDisabled = settings.disabledBuiltinTags.includes(key);
        return {
          value: tag,
          enabled: !isDisabled,
          mode: getDefaultModeForGroup(groupName),
          isCustom: false,
        };
      });
    });

    return items;
  }, [settings.disabledBuiltinTags]);

  // Get custom tags as TagItem array
  const customTagItems: TagItem[] = useMemo(() => {
    return settings.customTags.map((tag) => ({
      value: tag.value,
      enabled: true,
      mode: tag.mode,
      isCustom: true,
    }));
  }, [settings.customTags]);

  // Count totals
  const stats = useMemo(() => {
    let totalBuiltin = 0;
    let enabledBuiltin = 0;
    (Object.keys(groupTagItems) as TagGroupName[]).forEach((group) => {
      totalBuiltin += groupTagItems[group].length;
      enabledBuiltin += groupTagItems[group].filter((t) => t.enabled).length;
    });
    return {
      totalBuiltin,
      enabledBuiltin,
      customCount: customTagItems.length,
    };
  }, [groupTagItems, customTagItems]);

  // Handle toggling a built-in tag
  const handleToggleBuiltinTag = useCallback(
    (group: TagGroupName, value: string, enabled: boolean) => {
      const key = buildTagKey(group, value);
      let newDisabledTags: string[];

      if (enabled) {
        // Remove from disabled list
        newDisabledTags = settings.disabledBuiltinTags.filter((k) => k !== key);
      } else {
        // Add to disabled list
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
      // Check for duplicates
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

  // Handle reset to defaults
  const handleReset = useCallback(() => {
    onChange({
      disabledBuiltinTags: [],
      customTags: [],
    });
  }, [onChange]);

  return (
    <div className="normalization-tags-section">
      <div className="normalization-tags-header">
        <div className="normalization-tags-title-wrapper">
          <span className="material-icons normalization-tags-icon">sell</span>
          <h3 className="normalization-tags-title">Channel Name Normalization Tags</h3>
        </div>
        <button
          className="normalization-tags-reset"
          onClick={handleReset}
          title="Reset to default settings"
          type="button"
        >
          <span className="material-icons">restart_alt</span>
          Reset
        </button>
      </div>

      <p className="normalization-tags-subtitle">
        Configure which patterns are stripped from channel names during bulk channel
        creation. Disable tags you want to keep, or add custom tags for your specific
        provider naming conventions.
      </p>

      <div className="normalization-tags-summary">
        <div className="normalization-tags-stat">
          <span className="normalization-tags-stat-value">{stats.enabledBuiltin}</span>
          <span className="normalization-tags-stat-label">Active Tags</span>
        </div>
        <div className="normalization-tags-stat">
          <span className="normalization-tags-stat-value">{stats.totalBuiltin - stats.enabledBuiltin}</span>
          <span className="normalization-tags-stat-label">Disabled</span>
        </div>
        <div className="normalization-tags-stat">
          <span className="normalization-tags-stat-value">{stats.customCount}</span>
          <span className="normalization-tags-stat-label">Custom Tags</span>
        </div>
      </div>

      <div className="normalization-tags-groups">
        {(Object.keys(TAG_GROUPS) as TagGroupName[]).map((groupName) => (
          <TagGroup
            key={groupName}
            title={TAG_GROUPS[groupName].title}
            icon={TAG_GROUPS[groupName].icon}
            description={TAG_GROUPS[groupName].description}
            tags={groupTagItems[groupName]}
            onToggleTag={(value, enabled) =>
              handleToggleBuiltinTag(groupName, value, enabled)
            }
            defaultExpanded={false}
          />
        ))}
      </div>

      {/* Custom tags section */}
      <div className="normalization-tags-custom-section">
        <div className="normalization-tags-custom-header">
          <span className="normalization-tags-custom-title">Custom Tags</span>
          {customTagItems.length > 0 && (
            <span className="normalization-tags-custom-count">
              {customTagItems.length}
            </span>
          )}
        </div>

        {customTagItems.length > 0 ? (
          <div className="normalization-tags-custom-list">
            {customTagItems.map((tag) => (
              <TagChip
                key={tag.value}
                label={tag.value}
                enabled={tag.enabled}
                mode={tag.mode}
                onRemove={() => handleRemoveCustomTag(tag.value)}
                isCustom
              />
            ))}
          </div>
        ) : (
          <div className="normalization-tags-custom-empty">
            No custom tags added. Add tags below for patterns specific to your provider.
          </div>
        )}

        <AddTagInput
          onAdd={handleAddCustomTag}
          placeholder="Add custom tag (e.g., MY_PROVIDER)..."
        />

        <div className="normalization-tags-custom-help">
          <span className="material-icons normalization-tags-custom-help-icon">info</span>
          <span className="normalization-tags-custom-help-text">
            Add tag text only. Special characters like ( ) [ ] | - : will be handled automatically.
          </span>
        </div>
      </div>
    </div>
  );
}

/**
 * Get the default mode for a tag group
 */
function getDefaultModeForGroup(group: TagGroupName): NormalizationTagMode {
  switch (group) {
    case 'country':
    case 'league':
    case 'network':
      return 'prefix';
    case 'quality':
    case 'timezone':
      return 'suffix';
    default:
      return 'both';
  }
}

export default NormalizationTagsSection;

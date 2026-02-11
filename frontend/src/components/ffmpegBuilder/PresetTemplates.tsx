import { useState, useEffect, useCallback } from 'react';
import type {
  PresetTemplate,
  PresetCategory,
  FFMPEGBuilderState,
} from '../../types/ffmpegBuilder';
import { useNotifications } from '../../contexts/NotificationContext';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CATEGORIES: { value: PresetCategory | 'all'; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'web', label: 'Web' },
  { value: 'streaming', label: 'Streaming' },
  { value: 'archive', label: 'Archive' },
  { value: 'custom', label: 'Custom' },
];

// ---------------------------------------------------------------------------
// PresetTemplates component
// ---------------------------------------------------------------------------

interface PresetTemplatesProps {
  onPresetLoad: (config: FFMPEGBuilderState) => void;
  onPresetSave: (preset: { name: string; description: string; config: FFMPEGBuilderState }) => void;
  currentConfig: FFMPEGBuilderState;
}

export function PresetTemplates({ onPresetLoad, onPresetSave, currentConfig }: PresetTemplatesProps) {
  const notifications = useNotifications();
  const [presets, setPresets] = useState<PresetTemplate[]>([]);
  const [selectedCategory, setSelectedCategory] = useState<PresetCategory | 'all'>('all');
  const [showSaveDialog, setShowSaveDialog] = useState(false);
  const [saveName, setSaveName] = useState('');
  const [saveDescription, setSaveDescription] = useState('');
  const [saveCategory, setSaveCategory] = useState<PresetCategory>('custom');
  const [saveCategoryOpen, setSaveCategoryOpen] = useState(false);

  // Fetch presets from API
  useEffect(() => {
    fetch('/api/ffmpeg/presets')
      .then(res => res.json())
      .then(data => {
        setPresets(data.presets || []);
      })
      .catch(() => {
        // Fallback: empty
      });
  }, []);

  const handlePresetClick = useCallback((preset: PresetTemplate) => {
    // Merge preset config with current input/output paths
    const merged: FFMPEGBuilderState = {
      ...preset.config,
      input: {
        ...preset.config.input,
        path: currentConfig.input.path,
      },
      output: {
        ...preset.config.output,
        path: currentConfig.output.path,
      },
    };
    onPresetLoad(merged);
    notifications.success('Preset applied successfully');
  }, [currentConfig, onPresetLoad, notifications]);

  const handleSave = useCallback(() => {
    onPresetSave({
      name: saveName,
      description: saveDescription,
      config: currentConfig,
    });
    // Add to local list
    setPresets(prev => [
      ...prev,
      {
        id: `custom-${Date.now()}`,
        name: saveName,
        description: saveDescription,
        category: saveCategory,
        isBuiltIn: false,
        config: currentConfig,
      },
    ]);
    setShowSaveDialog(false);
    setSaveName('');
    setSaveDescription('');
    setSaveCategory('custom');
  }, [saveName, saveDescription, saveCategory, currentConfig, onPresetSave]);

  const filteredPresets = selectedCategory === 'all'
    ? presets
    : presets.filter(p => p.category === selectedCategory);

  return (
    <div className="preset-templates" aria-label="Preset">
      {/* Category Tabs */}
      <div className="preset-categories" role="tablist">
        {CATEGORIES.map(cat => (
          <button
            key={cat.value}
            role="tab"
            aria-selected={selectedCategory === cat.value}
            className={`category-tab${selectedCategory === cat.value ? ' active' : ''}`}
            onClick={() => setSelectedCategory(cat.value)}
          >
            {cat.label}
          </button>
        ))}
      </div>

      {/* Save Preset Button — hidden when dialog is open to avoid getByText collision */}
      {!showSaveDialog && (
        <button
          className="btn-secondary"
          onClick={() => setShowSaveDialog(true)}
        >
          Save Preset
        </button>
      )}

      {/* Preset List */}
      <div className="preset-list">
        {filteredPresets.map(preset => (
          <div
            key={preset.id}
            className="preset-card"
            role="button"
            tabIndex={0}
            onClick={() => handlePresetClick(preset)}
            onKeyDown={e => { if (e.key === 'Enter') handlePresetClick(preset); }}
          >
            <div className="preset-name">{preset.name}</div>
            <div className="preset-description">{preset.description}</div>
          </div>
        ))}
      </div>

      {/* Save Dialog */}
      {showSaveDialog && (
        <div className="preset-save-dialog" role="dialog" aria-label="Save Preset">
          <h4>Save New Preset</h4>

          <div className="form-group">
            <label htmlFor="preset-name">Name</label>
            <input
              id="preset-name"
              type="text"
              value={saveName}
              onChange={e => setSaveName(e.target.value)}
            />
          </div>

          <div className="form-group">
            <label htmlFor="preset-description">Description</label>
            <input
              id="preset-description"
              type="text"
              value={saveDescription}
              onChange={e => setSaveDescription(e.target.value)}
            />
          </div>

          <div className="form-group">
            <label htmlFor="preset-category">Category</label>
            <div
              id="preset-category"
              role="combobox"
              aria-label="Category"
              aria-expanded={saveCategoryOpen}
              aria-valuetext={CATEGORIES.find(c => c.value === saveCategory)?.label || saveCategory}
              className="dropdown-trigger"
              title={CATEGORIES.find(c => c.value === saveCategory)?.label || saveCategory}
              onClick={() => setSaveCategoryOpen(!saveCategoryOpen)}
            >
              {CATEGORIES.find(c => c.value === saveCategory)?.label || saveCategory}
            </div>
            {saveCategoryOpen && (
              <div role="listbox" className="dropdown-list">
                {CATEGORIES.filter(c => c.value !== 'all').map(cat => (
                  <div
                    key={cat.value}
                    role="option"
                    aria-label={cat.label}
                    aria-selected={cat.value === saveCategory}
                    className={`dropdown-option${cat.value === saveCategory ? ' selected' : ''}`}
                    onClick={() => {
                      setSaveCategory(cat.value as PresetCategory);
                      setSaveCategoryOpen(false);
                    }}
                  >
                    {cat.label}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="dialog-actions">
            <button className="btn-cancel" onClick={() => setShowSaveDialog(false)}>Cancel</button>
            <button className="btn-primary" onClick={handleSave}>Save</button>
          </div>
        </div>
      )}
    </div>
  );
}

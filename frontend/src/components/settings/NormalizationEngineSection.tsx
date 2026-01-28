/**
 * NormalizationEngineSection Component
 *
 * Advanced normalization rules management UI for the Settings tab.
 * Allows viewing, creating, editing, and testing normalization rules.
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import * as api from '../../services/api';
import type {
  NormalizationRuleGroup,
  NormalizationRule,
  NormalizationConditionType,
  NormalizationActionType,
  NormalizationConditionLogic,
  NormalizationCondition,
  NormalizationResult,
  TestRuleResult,
  TagGroup,
  TagMatchPosition,
} from '../../types';
import './NormalizationEngineSection.css';
import '../ModalBase.css';
import { CustomSelect, type SelectOption } from '../CustomSelect';

// Condition type options for dropdowns
const CONDITION_TYPES: { value: NormalizationConditionType; label: string; description: string }[] = [
  { value: 'starts_with', label: 'Starts With', description: 'Match text at the beginning' },
  { value: 'ends_with', label: 'Ends With', description: 'Match text at the end' },
  { value: 'contains', label: 'Contains', description: 'Match text anywhere' },
  { value: 'regex', label: 'Regex', description: 'Match using regular expression' },
  { value: 'tag_group', label: 'Tag Group', description: 'Match against a tag vocabulary' },
  { value: 'always', label: 'Always', description: 'Always match (use with caution)' },
];

// Tag match position options
const TAG_MATCH_POSITIONS: { value: TagMatchPosition; label: string; description: string }[] = [
  { value: 'prefix', label: 'Prefix', description: 'Tag appears at the start' },
  { value: 'suffix', label: 'Suffix', description: 'Tag appears at the end' },
  { value: 'contains', label: 'Anywhere', description: 'Tag appears anywhere in text' },
];

// Action type options for dropdowns
const ACTION_TYPES: { value: NormalizationActionType; label: string; description: string }[] = [
  { value: 'strip_prefix', label: 'Strip Prefix', description: 'Remove matched text from start' },
  { value: 'strip_suffix', label: 'Strip Suffix', description: 'Remove matched text from end' },
  { value: 'remove', label: 'Remove', description: 'Remove matched text' },
  { value: 'replace', label: 'Replace', description: 'Replace matched text with value' },
  { value: 'regex_replace', label: 'Regex Replace', description: 'Replace using regex substitution' },
  { value: 'normalize_prefix', label: 'Normalize Prefix', description: 'Standardize prefix format' },
];

// Sample stream names for testing
const SAMPLE_STREAMS = [
  'US: ESPN HD',
  'UK | BBC One FHD',
  'NFL: FOX Sports 1 EAST',
  'CA: TSN 4K',
  'ESPN+ Live',
  'NBA TV HD',
];

interface RuleEditorState {
  isOpen: boolean;
  editingRule: NormalizationRule | null;
  groupId: number | null;
  name: string;
  description: string;
  // Simple condition mode (legacy)
  conditionType: NormalizationConditionType;
  conditionValue: string;
  caseSensitive: boolean;
  // Compound conditions mode
  useCompoundConditions: boolean;
  conditions: NormalizationCondition[];
  conditionLogic: NormalizationConditionLogic;
  // Tag group condition settings
  tagGroupId: number | null;
  tagMatchPosition: TagMatchPosition;
  // Action settings
  actionType: NormalizationActionType;
  actionValue: string;
  stopProcessing: boolean;
  // Else branch settings
  hasElseBranch: boolean;
  elseActionType: NormalizationActionType;
  elseActionValue: string;
}

interface GroupEditorState {
  isOpen: boolean;
  editingGroup: NormalizationRuleGroup | null;
  name: string;
  description: string;
}

// Sortable rule item for drag-and-drop reordering
function SortableRuleItem({
  rule,
  isSelected,
  canDrag,
  onSelect,
  onToggleEnabled,
  onEdit,
  onDelete,
}: {
  rule: NormalizationRule;
  isSelected: boolean;
  canDrag: boolean;
  onSelect: () => void;
  onToggleEnabled: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: rule.id, disabled: !canDrag });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    zIndex: isDragging ? 1000 : undefined,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`norm-engine-rule ${!rule.enabled ? 'disabled' : ''} ${isSelected ? 'selected' : ''} ${isDragging ? 'dragging' : ''}`}
      onClick={onSelect}
    >
      {canDrag && (
        <span
          className="norm-engine-rule-drag-handle"
          {...attributes}
          {...listeners}
        >
          <span className="material-icons">drag_indicator</span>
        </span>
      )}
      <div className="norm-engine-rule-info">
        <span className="norm-engine-rule-name">{rule.name}</span>
        <span className="norm-engine-rule-pattern">
          {rule.condition_type === 'tag_group' ? (
            <>tag_group: {rule.tag_group_name || 'Unknown'} ({rule.tag_match_position})</>
          ) : (
            <>{rule.condition_type}: "{rule.condition_value}"</>
          )}
        </span>
      </div>
      <div className="norm-engine-rule-actions" onClick={(e) => e.stopPropagation()}>
        <label className="norm-engine-toggle small">
          <input
            type="checkbox"
            checked={rule.enabled}
            onChange={onToggleEnabled}
          />
          <span className="norm-engine-toggle-slider"></span>
        </label>
        {!rule.is_builtin && (
          <>
            <button
              className="norm-engine-btn-icon small"
              onClick={onEdit}
              title="Edit rule"
              type="button"
            >
              <span className="material-icons">edit</span>
            </button>
            <button
              className="norm-engine-btn-icon small danger"
              onClick={onDelete}
              title="Delete rule"
              type="button"
            >
              <span className="material-icons">delete</span>
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// Sortable group item for drag-and-drop reordering of groups
function SortableGroupItem({
  group,
  children,
}: {
  group: NormalizationRuleGroup;
  children: React.ReactNode;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: group.id });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    zIndex: isDragging ? 1000 : undefined,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`norm-engine-group ${!group.enabled ? 'disabled' : ''} ${isDragging ? 'dragging' : ''}`}
    >
      <div className="norm-engine-group-drag-handle" {...attributes} {...listeners}>
        <span className="material-icons">drag_indicator</span>
      </div>
      <div className="norm-engine-group-content">
        {children}
      </div>
    </div>
  );
}

export function NormalizationEngineSection() {
  // Data state
  const [groups, setGroups] = useState<NormalizationRuleGroup[]>([]);
  const [tagGroups, setTagGroups] = useState<TagGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // UI state
  const [expandedGroups, setExpandedGroups] = useState<Set<number>>(new Set());
  const [selectedRule, setSelectedRule] = useState<NormalizationRule | null>(null);

  // Test panel state
  const [testInput, setTestInput] = useState('');
  const [testResults, setTestResults] = useState<NormalizationResult[]>([]);
  const [testing, setTesting] = useState(false);
  const [testPanelExpanded, setTestPanelExpanded] = useState(false);

  // Rule editor state
  const [ruleEditor, setRuleEditor] = useState<RuleEditorState>({
    isOpen: false,
    editingRule: null,
    groupId: null,
    name: '',
    description: '',
    conditionType: 'starts_with',
    conditionValue: '',
    caseSensitive: false,
    useCompoundConditions: false,
    conditions: [],
    conditionLogic: 'AND',
    tagGroupId: null,
    tagMatchPosition: 'prefix',
    actionType: 'strip_prefix',
    actionValue: '',
    stopProcessing: false,
    hasElseBranch: false,
    elseActionType: 'remove',
    elseActionValue: '',
  });

  // Group editor state
  const [groupEditor, setGroupEditor] = useState<GroupEditorState>({
    isOpen: false,
    editingGroup: null,
    name: '',
    description: '',
  });

  // Live preview state
  const [previewResult, setPreviewResult] = useState<TestRuleResult | null>(null);

  // Drag-and-drop sensors for rule reordering
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  // Load groups, rules, and tag groups
  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [rulesResponse, tagsResponse] = await Promise.all([
        api.getNormalizationRules(),
        api.getTagGroups(),
      ]);
      setGroups(rulesResponse.groups);
      setTagGroups(tagsResponse.groups);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load rules');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Toggle group expansion
  const toggleGroup = useCallback((groupId: number) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupId)) {
        next.delete(groupId);
      } else {
        next.add(groupId);
      }
      return next;
    });
  }, []);

  // Handle rule drag end for reordering
  const handleRuleDragEnd = useCallback(async (event: DragEndEvent, group: NormalizationRuleGroup) => {
    const { active, over } = event;

    if (!over || active.id === over.id || !group.rules) {
      return;
    }

    const oldIndex = group.rules.findIndex((r) => r.id === active.id);
    const newIndex = group.rules.findIndex((r) => r.id === over.id);

    if (oldIndex === -1 || newIndex === -1) {
      return;
    }

    // Optimistically update local state
    const newRules = arrayMove(group.rules, oldIndex, newIndex);
    setGroups((prev) =>
      prev.map((g) =>
        g.id === group.id ? { ...g, rules: newRules } : g
      )
    );

    // Persist to backend
    try {
      const ruleIds = newRules.map((r) => r.id);
      await api.reorderNormalizationRules(group.id, ruleIds);
    } catch (err) {
      // Revert on error
      setError(err instanceof Error ? err.message : 'Failed to reorder rules');
      await loadData();
    }
  }, [loadData]);

  // Handle group drag end for reordering
  const handleGroupDragEnd = useCallback(async (event: DragEndEvent) => {
    const { active, over } = event;

    if (!over || active.id === over.id) {
      return;
    }

    const oldIndex = groups.findIndex((g) => g.id === active.id);
    const newIndex = groups.findIndex((g) => g.id === over.id);

    if (oldIndex === -1 || newIndex === -1) {
      return;
    }

    // Optimistically update local state
    const newGroups = arrayMove(groups, oldIndex, newIndex);
    setGroups(newGroups);

    // Persist to backend
    try {
      const groupIds = newGroups.map((g) => g.id);
      await api.reorderNormalizationGroups(groupIds);
    } catch (err) {
      // Revert on error
      setError(err instanceof Error ? err.message : 'Failed to reorder groups');
      await loadData();
    }
  }, [groups, loadData]);

  // Toggle group enabled state
  const toggleGroupEnabled = useCallback(async (group: NormalizationRuleGroup) => {
    const newEnabled = !group.enabled;
    // Optimistic update
    setGroups((prev) =>
      prev.map((g) => (g.id === group.id ? { ...g, enabled: newEnabled } : g))
    );
    try {
      await api.updateNormalizationGroup(group.id, { enabled: newEnabled });
    } catch (err) {
      // Revert on error
      setGroups((prev) =>
        prev.map((g) => (g.id === group.id ? { ...g, enabled: !newEnabled } : g))
      );
      setError(err instanceof Error ? err.message : 'Failed to update group');
    }
  }, []);

  // Toggle rule enabled state
  const toggleRuleEnabled = useCallback(async (rule: NormalizationRule) => {
    const newEnabled = !rule.enabled;
    // Optimistic update
    setGroups((prev) =>
      prev.map((g) => ({
        ...g,
        rules: g.rules?.map((r) =>
          r.id === rule.id ? { ...r, enabled: newEnabled } : r
        ),
      }))
    );
    try {
      await api.updateNormalizationRule(rule.id, { enabled: newEnabled });
    } catch (err) {
      // Revert on error
      setGroups((prev) =>
        prev.map((g) => ({
          ...g,
          rules: g.rules?.map((r) =>
            r.id === rule.id ? { ...r, enabled: !newEnabled } : r
          ),
        }))
      );
      setError(err instanceof Error ? err.message : 'Failed to update rule');
    }
  }, []);

  // Delete rule
  const deleteRule = useCallback(async (rule: NormalizationRule) => {
    if (!confirm(`Delete rule "${rule.name}"?`)) return;
    try {
      await api.deleteNormalizationRule(rule.id);
      await loadData();
      if (selectedRule?.id === rule.id) {
        setSelectedRule(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete rule');
    }
  }, [loadData, selectedRule]);

  // Delete group
  const deleteGroup = useCallback(async (group: NormalizationRuleGroup) => {
    if (!confirm(`Delete group "${group.name}" and all its rules?`)) return;
    try {
      await api.deleteNormalizationGroup(group.id);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete group');
    }
  }, [loadData]);

  // Open rule editor for new rule
  const openNewRuleEditor = useCallback((groupId: number) => {
    setRuleEditor({
      isOpen: true,
      editingRule: null,
      groupId,
      name: '',
      description: '',
      conditionType: 'starts_with',
      conditionValue: '',
      caseSensitive: false,
      useCompoundConditions: false,
      conditions: [],
      conditionLogic: 'AND',
      tagGroupId: null,
      tagMatchPosition: 'prefix',
      actionType: 'strip_prefix',
      actionValue: '',
      stopProcessing: false,
      hasElseBranch: false,
      elseActionType: 'remove',
      elseActionValue: '',
    });
    setPreviewResult(null);
  }, []);

  // Open rule editor for editing
  const openEditRuleEditor = useCallback((rule: NormalizationRule) => {
    const hasCompoundConditions = rule.conditions && rule.conditions.length > 0;
    setRuleEditor({
      isOpen: true,
      editingRule: rule,
      groupId: rule.group_id,
      name: rule.name,
      description: rule.description || '',
      conditionType: rule.condition_type,
      conditionValue: rule.condition_value || '',
      caseSensitive: rule.case_sensitive,
      useCompoundConditions: hasCompoundConditions,
      conditions: rule.conditions || [],
      conditionLogic: rule.condition_logic || 'AND',
      tagGroupId: rule.tag_group_id || null,
      tagMatchPosition: rule.tag_match_position || 'prefix',
      actionType: rule.action_type,
      actionValue: rule.action_value || '',
      stopProcessing: rule.stop_processing,
      hasElseBranch: !!(rule.else_action_type),
      elseActionType: rule.else_action_type || 'remove',
      elseActionValue: rule.else_action_value || '',
    });
    setPreviewResult(null);
  }, []);

  // Close rule editor
  const closeRuleEditor = useCallback(() => {
    setRuleEditor((prev) => ({ ...prev, isOpen: false }));
    setPreviewResult(null);
  }, []);

  // Save rule
  const saveRule = useCallback(async () => {
    try {
      // Build the request with compound conditions if enabled
      const conditionsData = ruleEditor.useCompoundConditions && ruleEditor.conditions.length > 0
        ? ruleEditor.conditions
        : undefined;
      const conditionLogicData = ruleEditor.useCompoundConditions
        ? ruleEditor.conditionLogic
        : undefined;

      // Tag group fields (only when condition type is tag_group)
      const tagGroupId = ruleEditor.conditionType === 'tag_group' ? ruleEditor.tagGroupId : null;
      const tagMatchPosition = ruleEditor.conditionType === 'tag_group' ? ruleEditor.tagMatchPosition : null;

      // Else branch fields (only when enabled)
      const elseActionType = ruleEditor.hasElseBranch ? ruleEditor.elseActionType : null;
      const elseActionValue = ruleEditor.hasElseBranch ? (ruleEditor.elseActionValue || null) : null;

      if (ruleEditor.editingRule) {
        // Update existing rule
        await api.updateNormalizationRule(ruleEditor.editingRule.id, {
          name: ruleEditor.name,
          description: ruleEditor.description || undefined,
          condition_type: ruleEditor.conditionType,
          condition_value: ruleEditor.conditionValue || undefined,
          case_sensitive: ruleEditor.caseSensitive,
          conditions: ruleEditor.useCompoundConditions ? conditionsData : null,  // null to clear compound conditions
          condition_logic: conditionLogicData,
          tag_group_id: tagGroupId,
          tag_match_position: tagMatchPosition,
          action_type: ruleEditor.actionType,
          action_value: ruleEditor.actionValue || undefined,
          stop_processing: ruleEditor.stopProcessing,
          else_action_type: elseActionType,
          else_action_value: elseActionValue,
        });
      } else if (ruleEditor.groupId) {
        // Create new rule
        await api.createNormalizationRule({
          group_id: ruleEditor.groupId,
          name: ruleEditor.name,
          description: ruleEditor.description || undefined,
          condition_type: ruleEditor.conditionType,
          condition_value: ruleEditor.conditionValue || undefined,
          case_sensitive: ruleEditor.caseSensitive,
          conditions: conditionsData,
          condition_logic: conditionLogicData,
          tag_group_id: tagGroupId ?? undefined,
          tag_match_position: tagMatchPosition ?? undefined,
          action_type: ruleEditor.actionType,
          action_value: ruleEditor.actionValue || undefined,
          stop_processing: ruleEditor.stopProcessing,
          else_action_type: elseActionType ?? undefined,
          else_action_value: elseActionValue ?? undefined,
        });
      }
      closeRuleEditor();
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save rule');
    }
  }, [ruleEditor, closeRuleEditor, loadData]);

  // Open group editor for new group
  const openNewGroupEditor = useCallback(() => {
    setGroupEditor({
      isOpen: true,
      editingGroup: null,
      name: '',
      description: '',
    });
  }, []);

  // Open group editor for editing
  const openEditGroupEditor = useCallback((group: NormalizationRuleGroup) => {
    setGroupEditor({
      isOpen: true,
      editingGroup: group,
      name: group.name,
      description: group.description || '',
    });
  }, []);

  // Close group editor
  const closeGroupEditor = useCallback(() => {
    setGroupEditor((prev) => ({ ...prev, isOpen: false }));
  }, []);

  // Save group
  const saveGroup = useCallback(async () => {
    try {
      if (groupEditor.editingGroup) {
        await api.updateNormalizationGroup(groupEditor.editingGroup.id, {
          name: groupEditor.name,
          description: groupEditor.description || undefined,
        });
      } else {
        const maxPriority = groups.length > 0 ? Math.max(...groups.map((g) => g.priority)) + 1 : 0;
        await api.createNormalizationGroup({
          name: groupEditor.name,
          description: groupEditor.description || undefined,
          priority: maxPriority,
        });
      }
      closeGroupEditor();
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save group');
    }
  }, [groupEditor, groups, closeGroupEditor, loadData]);

  // Test normalization
  const runTest = useCallback(async () => {
    const texts = testInput.trim()
      ? testInput.split('\n').filter((t) => t.trim())
      : SAMPLE_STREAMS;

    try {
      setTesting(true);
      const response = await api.testNormalizationBatch(texts);
      setTestResults(response.results);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to test normalization');
    } finally {
      setTesting(false);
    }
  }, [testInput]);

  // Live preview for rule editor
  const updatePreview = useCallback(async () => {
    // Check if we have enough info to preview
    if (ruleEditor.useCompoundConditions) {
      if (ruleEditor.conditions.length === 0) {
        setPreviewResult(null);
        return;
      }
    } else if (ruleEditor.conditionType === 'tag_group') {
      // Tag group condition needs a tag group selected
      if (!ruleEditor.tagGroupId) {
        setPreviewResult(null);
        return;
      }
    } else if (ruleEditor.conditionType !== 'always') {
      // Other simple conditions need a value
      if (!ruleEditor.conditionValue) {
        setPreviewResult(null);
        return;
      }
    }

    const sampleText = testInput.trim().split('\n')[0] || SAMPLE_STREAMS[0];

    try {
      const result = await api.testNormalizationRule({
        text: sampleText,
        condition_type: ruleEditor.conditionType,
        condition_value: ruleEditor.conditionValue,
        case_sensitive: ruleEditor.caseSensitive,
        conditions: ruleEditor.useCompoundConditions ? ruleEditor.conditions : undefined,
        condition_logic: ruleEditor.useCompoundConditions ? ruleEditor.conditionLogic : undefined,
        tag_group_id: ruleEditor.conditionType === 'tag_group' ? ruleEditor.tagGroupId ?? undefined : undefined,
        tag_match_position: ruleEditor.conditionType === 'tag_group' ? ruleEditor.tagMatchPosition : undefined,
        action_type: ruleEditor.actionType,
        action_value: ruleEditor.actionValue || undefined,
        else_action_type: ruleEditor.hasElseBranch ? ruleEditor.elseActionType : undefined,
        else_action_value: ruleEditor.hasElseBranch ? (ruleEditor.elseActionValue || undefined) : undefined,
      });
      setPreviewResult(result);
    } catch {
      setPreviewResult(null);
    }
  }, [ruleEditor, testInput]);

  // Update preview when rule editor changes
  useEffect(() => {
    if (ruleEditor.isOpen) {
      const timer = setTimeout(updatePreview, 300);
      return () => clearTimeout(timer);
    }
  }, [ruleEditor.isOpen, ruleEditor.conditionType, ruleEditor.conditionValue, ruleEditor.caseSensitive, ruleEditor.useCompoundConditions, ruleEditor.conditions, ruleEditor.conditionLogic, ruleEditor.tagGroupId, ruleEditor.tagMatchPosition, ruleEditor.actionType, ruleEditor.actionValue, ruleEditor.hasElseBranch, ruleEditor.elseActionType, ruleEditor.elseActionValue, updatePreview]);

  // Stats
  const stats = useMemo(() => {
    let totalRules = 0;
    let enabledRules = 0;
    let builtinRules = 0;

    groups.forEach((group) => {
      group.rules?.forEach((rule) => {
        totalRules++;
        if (rule.enabled) enabledRules++;
        if (rule.is_builtin) builtinRules++;
      });
    });

    return {
      totalGroups: groups.length,
      enabledGroups: groups.filter((g) => g.enabled).length,
      totalRules,
      enabledRules,
      builtinRules,
      customRules: totalRules - builtinRules,
    };
  }, [groups]);

  if (loading) {
    return (
      <div className="norm-engine-section">
        <div className="norm-engine-loading">
          <span className="material-icons spin">sync</span>
          Loading normalization rules...
        </div>
      </div>
    );
  }

  return (
    <div className="norm-engine-section">
      {/* Header */}
      <div className="norm-engine-header">
        <div className="norm-engine-title-wrapper">
          <span className="material-icons norm-engine-icon">auto_fix_high</span>
          <h3 className="norm-engine-title">Normalization Rules Engine</h3>
        </div>
        <button
          className="norm-engine-btn norm-engine-btn-primary"
          onClick={openNewGroupEditor}
          type="button"
        >
          <span className="material-icons">add</span>
          New Group
        </button>
      </div>

      <p className="norm-engine-subtitle">
        Configure rules to automatically normalize stream names when creating channels.
        Rules are processed in priority order within each group.
      </p>

      {/* Error message */}
      {error && (
        <div className="norm-engine-error">
          <span className="material-icons">error</span>
          {error}
          <button onClick={() => setError(null)} type="button">
            <span className="material-icons">close</span>
          </button>
        </div>
      )}

      {/* Stats */}
      <div className="norm-engine-stats">
        <div className="norm-engine-stat">
          <span className="norm-engine-stat-value">{stats.enabledGroups}/{stats.totalGroups}</span>
          <span className="norm-engine-stat-label">Groups Active</span>
        </div>
        <div className="norm-engine-stat">
          <span className="norm-engine-stat-value">{stats.enabledRules}/{stats.totalRules}</span>
          <span className="norm-engine-stat-label">Rules Active</span>
        </div>
        <div className="norm-engine-stat">
          <span className="norm-engine-stat-value">{stats.builtinRules}</span>
          <span className="norm-engine-stat-label">Built-in</span>
        </div>
        <div className="norm-engine-stat">
          <span className="norm-engine-stat-value">{stats.customRules}</span>
          <span className="norm-engine-stat-label">Custom</span>
        </div>
      </div>

      {/* Collapsible Test Panel */}
      <div className={`norm-engine-test-panel collapsible ${testPanelExpanded ? 'expanded' : ''}`}>
        <div
          className="norm-engine-test-header clickable"
          onClick={() => setTestPanelExpanded(!testPanelExpanded)}
        >
          <span className={`material-icons norm-engine-expand ${testPanelExpanded ? 'expanded' : ''}`}>
            chevron_right
          </span>
          <span className="material-icons">science</span>
          <h4>Test Rules</h4>
        </div>

        {testPanelExpanded && (
          <div className="norm-engine-test-body">
            <div className="norm-engine-test-input">
              <textarea
                placeholder="Enter stream names to test (one per line)&#10;or leave empty to use samples..."
                value={testInput}
                onChange={(e) => setTestInput(e.target.value)}
                rows={4}
              />
              <button
                className="norm-engine-btn norm-engine-btn-primary"
                onClick={runTest}
                disabled={testing}
                type="button"
              >
                {testing ? (
                  <>
                    <span className="material-icons spin">sync</span>
                    Testing...
                  </>
                ) : (
                  <>
                    <span className="material-icons">play_arrow</span>
                    Run Test
                  </>
                )}
              </button>
            </div>

            {testResults.length > 0 && (
              <div className="norm-engine-test-results">
                <h5>Results</h5>
                {testResults.map((result, index) => (
                  <div key={index} className="norm-engine-test-result">
                    <div className="norm-engine-test-original">
                      <span className="label">Original:</span>
                      <span className="value">{result.original}</span>
                    </div>
                    <div className="norm-engine-test-arrow">
                      <span className="material-icons">arrow_downward</span>
                    </div>
                    <div className="norm-engine-test-normalized">
                      <span className="label">Normalized:</span>
                      <span className="value">{result.normalized}</span>
                    </div>
                    {result.transformations && result.transformations.length > 0 && (
                      <div className="norm-engine-test-transforms">
                        {result.transformations.map((t, i) => (
                          <div key={i} className="norm-engine-test-transform">
                            <span className="material-icons">chevron_right</span>
                            Rule {t.rule_id}: "{t.before}" → "{t.after}"
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Groups and Rules */}
      <div className="norm-engine-groups">
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleGroupDragEnd}
          >
            <SortableContext
              items={groups.map((g) => g.id)}
              strategy={verticalListSortingStrategy}
            >
              {groups.map((group) => (
                <SortableGroupItem key={group.id} group={group}>
                  <div className="norm-engine-group-header" onClick={() => toggleGroup(group.id)}>
                <span className={`material-icons norm-engine-expand ${expandedGroups.has(group.id) ? 'expanded' : ''}`}>
                  chevron_right
                </span>
                <div className="norm-engine-group-info">
                  <span className="norm-engine-group-name">{group.name}</span>
                  {group.is_builtin && (
                    <span className="norm-engine-badge builtin">Built-in</span>
                  )}
                  <span className="norm-engine-group-count">
                    {group.rules?.length || 0} rules
                  </span>
                </div>
                <div className="norm-engine-group-actions" onClick={(e) => e.stopPropagation()}>
                  <label className="norm-engine-toggle">
                    <input
                      type="checkbox"
                      checked={group.enabled}
                      onChange={() => toggleGroupEnabled(group)}
                    />
                    <span className="norm-engine-toggle-slider"></span>
                  </label>
                  {!group.is_builtin && (
                    <>
                      <button
                        className="norm-engine-btn-icon"
                        onClick={() => openEditGroupEditor(group)}
                        title="Edit group"
                        type="button"
                      >
                        <span className="material-icons">edit</span>
                      </button>
                      <button
                        className="norm-engine-btn-icon danger"
                        onClick={() => deleteGroup(group)}
                        title="Delete group"
                        type="button"
                      >
                        <span className="material-icons">delete</span>
                      </button>
                    </>
                  )}
                </div>
              </div>

              {expandedGroups.has(group.id) && (
                <div className="norm-engine-rules">
                  {group.description && (
                    <p className="norm-engine-group-description">{group.description}</p>
                  )}

                  <DndContext
                    sensors={sensors}
                    collisionDetection={closestCenter}
                    onDragEnd={(event) => handleRuleDragEnd(event, group)}
                  >
                    <SortableContext
                      items={group.rules?.map((r) => r.id) || []}
                      strategy={verticalListSortingStrategy}
                    >
                      {group.rules?.map((rule) => (
                        <SortableRuleItem
                          key={rule.id}
                          rule={rule}
                          isSelected={selectedRule?.id === rule.id}
                          canDrag={!group.is_builtin}
                          onSelect={() => setSelectedRule(rule)}
                          onToggleEnabled={() => toggleRuleEnabled(rule)}
                          onEdit={() => openEditRuleEditor(rule)}
                          onDelete={() => deleteRule(rule)}
                        />
                      ))}
                    </SortableContext>
                  </DndContext>

                  <button
                    className="norm-engine-add-rule"
                    onClick={() => openNewRuleEditor(group.id)}
                    type="button"
                  >
                    <span className="material-icons">add</span>
                    Add Rule
                  </button>
                </div>
              )}
                </SortableGroupItem>
              ))}
            </SortableContext>
          </DndContext>

          {groups.length === 0 && (
            <div className="norm-engine-empty">
              <span className="material-icons">rule</span>
              <p>No normalization rules configured.</p>
              <button
                className="norm-engine-btn norm-engine-btn-primary"
                onClick={openNewGroupEditor}
                type="button"
              >
                Create First Group
              </button>
            </div>
          )}
        </div>

      {/* Rule Editor Modal */}
      {ruleEditor.isOpen && (
        <div className="modal-overlay" onClick={closeRuleEditor}>
          <div className="modal-container modal-md" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2 className="modal-title">{ruleEditor.editingRule ? 'Edit Rule' : 'New Rule'}</h2>
              <button
                className="modal-close-btn"
                onClick={closeRuleEditor}
                type="button"
              >
                <span className="material-icons">close</span>
              </button>
            </div>

            <div className="modal-body">
              <div className="modal-form-group">
                <label>Name</label>
                <input
                  type="text"
                  value={ruleEditor.name}
                  onChange={(e) => setRuleEditor((prev) => ({ ...prev, name: e.target.value }))}
                  placeholder="e.g., Strip HD suffix"
                />
              </div>

              <div className="modal-form-group">
                <label>Description (optional)</label>
                <input
                  type="text"
                  value={ruleEditor.description}
                  onChange={(e) => setRuleEditor((prev) => ({ ...prev, description: e.target.value }))}
                  placeholder="What this rule does..."
                />
              </div>

              {/* Condition Mode Toggle */}
              <div className="norm-engine-condition-mode">
                <label className="norm-engine-mode-label">Condition Mode:</label>
                <div className="norm-engine-mode-toggle">
                  <button
                    type="button"
                    className={`norm-engine-mode-btn ${!ruleEditor.useCompoundConditions ? 'active' : ''}`}
                    onClick={() => setRuleEditor((prev) => ({ ...prev, useCompoundConditions: false }))}
                  >
                    Simple
                  </button>
                  <button
                    type="button"
                    className={`norm-engine-mode-btn ${ruleEditor.useCompoundConditions ? 'active' : ''}`}
                    onClick={() => setRuleEditor((prev) => ({
                      ...prev,
                      useCompoundConditions: true,
                      // Initialize with one condition if empty
                      conditions: prev.conditions.length === 0
                        ? [{ type: prev.conditionType, value: prev.conditionValue, negate: false, case_sensitive: prev.caseSensitive }]
                        : prev.conditions,
                    }))}
                  >
                    Compound (AND/OR/NOT)
                  </button>
                </div>
              </div>

              {/* Simple Condition Mode */}
              {!ruleEditor.useCompoundConditions && (
                <>
                  <div className="modal-form-row">
                    <div className="modal-form-group">
                      <label>Condition Type</label>
                      <select
                        value={ruleEditor.conditionType}
                        onChange={(e) => setRuleEditor((prev) => ({
                          ...prev,
                          conditionType: e.target.value as NormalizationConditionType,
                        }))}
                      >
                        {CONDITION_TYPES.map((ct) => (
                          <option key={ct.value} value={ct.value}>
                            {ct.label}
                          </option>
                        ))}
                      </select>
                    </div>

                    {/* Pattern input for non-tag_group conditions */}
                    {ruleEditor.conditionType !== 'tag_group' && (
                      <div className="modal-form-group">
                        <label>Pattern</label>
                        <input
                          type="text"
                          value={ruleEditor.conditionValue}
                          onChange={(e) => setRuleEditor((prev) => ({ ...prev, conditionValue: e.target.value }))}
                          placeholder="e.g., HD"
                          disabled={ruleEditor.conditionType === 'always'}
                        />
                      </div>
                    )}
                  </div>

                  {/* Tag Group selector (when condition type is tag_group) */}
                  {ruleEditor.conditionType === 'tag_group' && (
                    <div className="modal-form-row">
                      <div className="modal-form-group">
                        <label>Tag Group</label>
                        <select
                          value={ruleEditor.tagGroupId ?? ''}
                          onChange={(e) => setRuleEditor((prev) => ({
                            ...prev,
                            tagGroupId: e.target.value ? Number(e.target.value) : null,
                          }))}
                        >
                          <option value="">Select a tag group...</option>
                          {tagGroups.map((tg) => (
                            <option key={tg.id} value={tg.id}>
                              {tg.name} ({tg.tag_count ?? 0} tags)
                            </option>
                          ))}
                        </select>
                      </div>

                      <div className="modal-form-group">
                        <label>Match Position</label>
                        <select
                          value={ruleEditor.tagMatchPosition}
                          onChange={(e) => setRuleEditor((prev) => ({
                            ...prev,
                            tagMatchPosition: e.target.value as TagMatchPosition,
                          }))}
                        >
                          {TAG_MATCH_POSITIONS.map((pos) => (
                            <option key={pos.value} value={pos.value}>
                              {pos.label}
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>
                  )}
                </>
              )}

              {/* Compound Conditions Mode */}
              {ruleEditor.useCompoundConditions && (
                <div className="norm-engine-compound-conditions">
                  <div className="norm-engine-compound-header">
                    <label>Combine conditions with:</label>
                    <CustomSelect
                      value={ruleEditor.conditionLogic}
                      onChange={(value) => setRuleEditor((prev) => ({
                        ...prev,
                        conditionLogic: value as NormalizationConditionLogic,
                      }))}
                      options={[
                        { value: 'AND', label: 'AND (all must match)' },
                        { value: 'OR', label: 'OR (any must match)' },
                      ]}
                      className="norm-engine-logic-select"
                    />
                  </div>

                  <div className="norm-engine-conditions-list">
                    {ruleEditor.conditions.map((condition, index) => (
                      <div key={index} className="norm-engine-condition-row">
                        <div className="norm-engine-condition-fields">
                          <CustomSelect
                            value={condition.type}
                            onChange={(value) => {
                              const newConditions = [...ruleEditor.conditions];
                              newConditions[index] = { ...condition, type: value as NormalizationConditionType };
                              setRuleEditor((prev) => ({ ...prev, conditions: newConditions }));
                            }}
                            options={CONDITION_TYPES.map((ct) => ({
                              value: ct.value,
                              label: ct.label,
                            }))}
                            className="norm-engine-condition-type-select"
                          />
                          <input
                            type="text"
                            value={condition.value}
                            onChange={(e) => {
                              const newConditions = [...ruleEditor.conditions];
                              newConditions[index] = { ...condition, value: e.target.value };
                              setRuleEditor((prev) => ({ ...prev, conditions: newConditions }));
                            }}
                            placeholder="Pattern"
                            disabled={condition.type === 'always'}
                          />
                        </div>
                        <div className="norm-engine-condition-options">
                          <label className="norm-engine-condition-checkbox" title="Negate (NOT)">
                            <input
                              type="checkbox"
                              checked={condition.negate || false}
                              onChange={(e) => {
                                const newConditions = [...ruleEditor.conditions];
                                newConditions[index] = { ...condition, negate: e.target.checked };
                                setRuleEditor((prev) => ({ ...prev, conditions: newConditions }));
                              }}
                            />
                            NOT
                          </label>
                          <label className="norm-engine-condition-checkbox" title="Case Sensitive">
                            <input
                              type="checkbox"
                              checked={condition.case_sensitive || false}
                              onChange={(e) => {
                                const newConditions = [...ruleEditor.conditions];
                                newConditions[index] = { ...condition, case_sensitive: e.target.checked };
                                setRuleEditor((prev) => ({ ...prev, conditions: newConditions }));
                              }}
                            />
                            Aa
                          </label>
                          <button
                            type="button"
                            className="norm-engine-btn-icon small danger"
                            onClick={() => {
                              const newConditions = ruleEditor.conditions.filter((_, i) => i !== index);
                              setRuleEditor((prev) => ({ ...prev, conditions: newConditions }));
                            }}
                            disabled={ruleEditor.conditions.length <= 1}
                            title="Remove condition"
                          >
                            <span className="material-icons">remove_circle</span>
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>

                  <button
                    type="button"
                    className="norm-engine-add-condition"
                    onClick={() => {
                      setRuleEditor((prev) => ({
                        ...prev,
                        conditions: [...prev.conditions, { type: 'contains', value: '', negate: false, case_sensitive: false }],
                      }));
                    }}
                  >
                    <span className="material-icons">add</span>
                    Add Condition
                  </button>
                </div>
              )}

              <div className="modal-form-row">
                <div className="modal-form-group">
                  <label>Action Type</label>
                  <select
                    value={ruleEditor.actionType}
                    onChange={(e) => setRuleEditor((prev) => ({
                      ...prev,
                      actionType: e.target.value as NormalizationActionType,
                    }))}
                  >
                    {ACTION_TYPES.map((at) => (
                      <option key={at.value} value={at.value}>
                        {at.label}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="modal-form-group">
                  <label>Replacement Value</label>
                  <input
                    type="text"
                    value={ruleEditor.actionValue}
                    onChange={(e) => setRuleEditor((prev) => ({ ...prev, actionValue: e.target.value }))}
                    placeholder="Leave empty to remove"
                    disabled={!['replace', 'regex_replace', 'normalize_prefix'].includes(ruleEditor.actionType)}
                  />
                </div>
              </div>

              <div className="norm-engine-form-checkboxes">
                {/* Case Sensitive only shown in simple mode (non-tag_group) - compound mode has per-condition settings */}
                {!ruleEditor.useCompoundConditions && ruleEditor.conditionType !== 'tag_group' && (
                  <label className="modal-checkbox-label">
                    <input
                      type="checkbox"
                      checked={ruleEditor.caseSensitive}
                      onChange={(e) => setRuleEditor((prev) => ({ ...prev, caseSensitive: e.target.checked }))}
                    />
                    Case Sensitive
                  </label>
                )}
                <label className="modal-checkbox-label">
                  <input
                    type="checkbox"
                    checked={ruleEditor.stopProcessing}
                    onChange={(e) => setRuleEditor((prev) => ({ ...prev, stopProcessing: e.target.checked }))}
                  />
                  Stop Processing After Match
                </label>
              </div>

              {/* Else Branch Configuration */}
              <div className="norm-engine-else-branch">
                <label className="modal-checkbox-label">
                  <input
                    type="checkbox"
                    checked={ruleEditor.hasElseBranch}
                    onChange={(e) => setRuleEditor((prev) => ({ ...prev, hasElseBranch: e.target.checked }))}
                  />
                  Execute alternate action if condition doesn't match (Else)
                </label>

                {ruleEditor.hasElseBranch && (
                  <div className="norm-engine-else-actions">
                    <div className="modal-form-row">
                      <div className="modal-form-group">
                        <label>Else Action Type</label>
                        <select
                          value={ruleEditor.elseActionType}
                          onChange={(e) => setRuleEditor((prev) => ({
                            ...prev,
                            elseActionType: e.target.value as NormalizationActionType,
                          }))}
                        >
                          {ACTION_TYPES.map((at) => (
                            <option key={at.value} value={at.value}>
                              {at.label}
                            </option>
                          ))}
                        </select>
                      </div>

                      <div className="modal-form-group">
                        <label>Else Replacement Value</label>
                        <input
                          type="text"
                          value={ruleEditor.elseActionValue}
                          onChange={(e) => setRuleEditor((prev) => ({ ...prev, elseActionValue: e.target.value }))}
                          placeholder="Leave empty to remove"
                          disabled={!['replace', 'regex_replace', 'normalize_prefix'].includes(ruleEditor.elseActionType)}
                        />
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {/* Live Preview */}
              {previewResult && (
                <div className="norm-engine-preview">
                  <h5>Live Preview</h5>
                  <div className={`norm-engine-preview-result ${previewResult.matched ? 'matched' : previewResult.else_applied ? 'else-applied' : 'no-match'}`}>
                    {previewResult.matched ? (
                      <>
                        <span className="material-icons">check_circle</span>
                        <span className="before">{previewResult.before}</span>
                        <span className="arrow">→</span>
                        <span className="after">{previewResult.after}</span>
                        {previewResult.matched_tag && (
                          <span className="matched-tag" title="Matched tag">
                            <span className="material-icons">label</span>
                            {previewResult.matched_tag}
                          </span>
                        )}
                      </>
                    ) : previewResult.else_applied ? (
                      <>
                        <span className="material-icons">swap_horiz</span>
                        <span className="before">{previewResult.before}</span>
                        <span className="arrow">→</span>
                        <span className="after">{previewResult.after}</span>
                        <span className="else-badge">Else</span>
                      </>
                    ) : (
                      <>
                        <span className="material-icons">cancel</span>
                        <span>No match</span>
                      </>
                    )}
                  </div>
                </div>
              )}
            </div>

            <div className="modal-footer">
              <button
                className="modal-btn modal-btn-secondary"
                onClick={closeRuleEditor}
                type="button"
              >
                Cancel
              </button>
              <button
                className="modal-btn modal-btn-primary"
                onClick={saveRule}
                disabled={!ruleEditor.name.trim()}
                type="button"
              >
                {ruleEditor.editingRule ? 'Save Changes' : 'Create Rule'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Group Editor Modal */}
      {groupEditor.isOpen && (
        <div className="modal-overlay" onClick={closeGroupEditor}>
          <div className="modal-container modal-sm" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2 className="modal-title">{groupEditor.editingGroup ? 'Edit Group' : 'New Rule Group'}</h2>
              <button
                className="modal-close-btn"
                onClick={closeGroupEditor}
                type="button"
              >
                <span className="material-icons">close</span>
              </button>
            </div>

            <div className="modal-body">
              <div className="modal-form-group">
                <label>Name</label>
                <input
                  type="text"
                  value={groupEditor.name}
                  onChange={(e) => setGroupEditor((prev) => ({ ...prev, name: e.target.value }))}
                  placeholder="e.g., My Custom Rules"
                />
              </div>

              <div className="modal-form-group">
                <label>Description (optional)</label>
                <textarea
                  value={groupEditor.description}
                  onChange={(e) => setGroupEditor((prev) => ({ ...prev, description: e.target.value }))}
                  placeholder="What rules in this group do..."
                  rows={3}
                />
              </div>
            </div>

            <div className="modal-footer">
              <button
                className="modal-btn modal-btn-secondary"
                onClick={closeGroupEditor}
                type="button"
              >
                Cancel
              </button>
              <button
                className="modal-btn modal-btn-primary"
                onClick={saveGroup}
                disabled={!groupEditor.name.trim()}
                type="button"
              >
                {groupEditor.editingGroup ? 'Save Changes' : 'Create Group'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default NormalizationEngineSection;

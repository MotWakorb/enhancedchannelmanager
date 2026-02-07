/**
 * TagEngineSection Component
 *
 * Tag vocabulary management UI for the Settings tab.
 * Allows viewing, creating, editing tag groups and their tags.
 * Tags are used by the normalization engine for pattern matching.
 */
import { useState, useEffect, useCallback } from 'react';
import * as api from '../../services/api';
import type { TagGroup, Tag } from '../../types';
import { useNotifications } from '../../contexts/NotificationContext';
import { ModalOverlay } from '../ModalOverlay';
import './TagEngineSection.css';

interface TagGroupCardProps {
  group: TagGroup;
  isExpanded: boolean;
  onToggleExpand: () => void;
  onRefresh: () => void;
}

function TagGroupCard({ group, isExpanded, onToggleExpand, onRefresh }: TagGroupCardProps) {
  const [tags, setTags] = useState<Tag[]>([]);
  const [loading, setLoading] = useState(false);
  const [newTagInput, setNewTagInput] = useState('');
  const [bulkInput, setBulkInput] = useState('');
  const [showBulkInput, setShowBulkInput] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingDescription, setEditingDescription] = useState(false);
  const [description, setDescription] = useState(group.description || '');

  // Load tags when expanded
  useEffect(() => {
    if (isExpanded && tags.length === 0) {
      loadTags();
    }
  }, [isExpanded]);

  const loadTags = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getTagGroup(group.id);
      setTags(data.tags || []);
    } catch (err) {
      setError('Failed to load tags');
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleAddTag = async () => {
    const tagValue = newTagInput.trim();
    if (!tagValue) return;

    try {
      const result = await api.addTagsToGroup(group.id, { tags: [tagValue] });
      if (result.created.length > 0) {
        await loadTags();
        onRefresh();
      } else if (result.skipped.length > 0) {
        setError(`Tag "${tagValue}" already exists`);
      }
      setNewTagInput('');
    } catch (err) {
      setError('Failed to add tag');
      console.error(err);
    }
  };

  const handleBulkAdd = async () => {
    const tagValues = bulkInput
      .split(/[,\n]/)
      .map(t => t.trim())
      .filter(t => t.length > 0);

    if (tagValues.length === 0) return;

    try {
      const result = await api.addTagsToGroup(group.id, { tags: tagValues });
      await loadTags();
      onRefresh();
      setBulkInput('');
      setShowBulkInput(false);
      if (result.skipped.length > 0) {
        setError(`${result.created.length} added, ${result.skipped.length} skipped (duplicates)`);
      }
    } catch (err) {
      setError('Failed to add tags');
      console.error(err);
    }
  };

  const handleDeleteTag = async (tagId: number) => {
    try {
      await api.deleteTag(group.id, tagId);
      setTags(tags.filter(t => t.id !== tagId));
      onRefresh();
    } catch (err: unknown) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to delete tag';
      if (errorMessage.includes('built-in')) {
        setError('Cannot delete built-in tag');
      } else {
        setError('Failed to delete tag');
      }
      console.error(err);
    }
  };

  const handleToggleTag = async (tag: Tag) => {
    try {
      const updated = await api.updateTag(group.id, tag.id, { enabled: !tag.enabled });
      setTags(tags.map(t => t.id === tag.id ? updated : t));
    } catch (err) {
      setError('Failed to update tag');
      console.error(err);
    }
  };

  const handleUpdateDescription = async () => {
    try {
      await api.updateTagGroup(group.id, { description });
      setEditingDescription(false);
      onRefresh();
    } catch (err) {
      setError('Failed to update description');
      console.error(err);
    }
  };

  const enabledCount = tags.filter(t => t.enabled).length;

  return (
    <div className={`tag-group-card ${isExpanded ? 'expanded' : ''}`}>
      <div className="tag-group-header" onClick={onToggleExpand}>
        <div className="tag-group-info">
          <span className="material-icons expand-icon">
            {isExpanded ? 'expand_less' : 'expand_more'}
          </span>
          <div className="tag-group-title">
            <h4>{group.name}</h4>
            {group.is_builtin && <span className="builtin-badge">Built-in</span>}
          </div>
        </div>
        <div className="tag-group-meta">
          <span className="tag-count" title="Total tags">
            <span className="material-icons">label</span>
            {group.tag_count ?? tags.length}
          </span>
        </div>
      </div>

      {isExpanded && (
        <div className="tag-group-content">
          {error && (
            <div className="tag-error">
              <span className="material-icons">error</span>
              {error}
              <button className="dismiss-error" onClick={() => setError(null)}>
                <span className="material-icons">close</span>
              </button>
            </div>
          )}

          {/* Description */}
          <div className="tag-group-description">
            {editingDescription ? (
              <div className="description-edit">
                <input
                  type="text"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Enter description..."
                  autoFocus
                />
                <button className="btn-icon" onClick={handleUpdateDescription}>
                  <span className="material-icons">check</span>
                </button>
                <button className="btn-icon" onClick={() => setEditingDescription(false)}>
                  <span className="material-icons">close</span>
                </button>
              </div>
            ) : (
              <div className="description-view" onClick={() => !group.is_builtin && setEditingDescription(true)}>
                <span className="description-text">
                  {group.description || 'No description'}
                </span>
                {!group.is_builtin && (
                  <span className="material-icons edit-icon">edit</span>
                )}
              </div>
            )}
          </div>

          {/* Tags list */}
          {loading ? (
            <div className="tags-loading">Loading tags...</div>
          ) : (
            <div className="tags-container">
              <div className="tags-header">
                <span>{enabledCount} of {tags.length} tags enabled</span>
              </div>
              <div className="tags-list">
                {tags.map(tag => (
                  <div
                    key={tag.id}
                    className={`tag-chip ${tag.enabled ? 'enabled' : 'disabled'} ${tag.is_builtin ? 'builtin' : ''}`}
                  >
                    <span
                      className="tag-value"
                      onClick={() => handleToggleTag(tag)}
                      title={`Click to ${tag.enabled ? 'disable' : 'enable'}`}
                    >
                      {tag.value}
                    </span>
                    {tag.case_sensitive && (
                      <span className="case-sensitive-badge" title="Case sensitive">Aa</span>
                    )}
                    {!tag.is_builtin && (
                      <button
                        className="tag-delete"
                        onClick={() => handleDeleteTag(tag.id)}
                        title="Delete tag"
                      >
                        <span className="material-icons">close</span>
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Add tag input */}
          <div className="add-tag-section">
            <div className="add-tag-row">
              <input
                type="text"
                value={newTagInput}
                onChange={(e) => setNewTagInput(e.target.value)}
                placeholder="Add new tag..."
                onKeyDown={(e) => e.key === 'Enter' && handleAddTag()}
              />
              <button className="btn-secondary" onClick={handleAddTag} disabled={!newTagInput.trim()}>
                <span className="material-icons">add</span>
                Add
              </button>
              <button
                className="btn-secondary"
                onClick={() => setShowBulkInput(!showBulkInput)}
                title="Bulk add tags"
              >
                <span className="material-icons">playlist_add</span>
              </button>
            </div>

            {showBulkInput && (
              <div className="bulk-add-section">
                <textarea
                  value={bulkInput}
                  onChange={(e) => setBulkInput(e.target.value)}
                  placeholder="Paste comma or newline separated tags..."
                  rows={3}
                />
                <div className="bulk-add-actions">
                  <button className="btn-secondary" onClick={handleBulkAdd} disabled={!bulkInput.trim()}>
                    <span className="material-icons">upload</span>
                    Import Tags
                  </button>
                  <button className="btn-secondary" onClick={() => { setBulkInput(''); setShowBulkInput(false); }}>
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function TagEngineSection() {
  const notifications = useNotifications();
  const [groups, setGroups] = useState<TagGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedGroupId, setExpandedGroupId] = useState<number | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newGroupName, setNewGroupName] = useState('');
  const [newGroupDescription, setNewGroupDescription] = useState('');
  const [searchQuery, setSearchQuery] = useState('');

  const loadGroups = useCallback(async () => {
    try {
      const data = await api.getTagGroups();
      setGroups(data.groups);
    } catch (err) {
      notifications.error('Failed to load tag groups', 'Tags');
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadGroups();
  }, [loadGroups]);

  const handleCreateGroup = async () => {
    if (!newGroupName.trim()) return;

    try {
      await api.createTagGroup({
        name: newGroupName.trim(),
        description: newGroupDescription.trim() || undefined,
      });
      await loadGroups();
      setShowCreateModal(false);
      setNewGroupName('');
      setNewGroupDescription('');
    } catch (err) {
      notifications.error('Failed to create tag group', 'Tags');
      console.error(err);
    }
  };

  const handleDeleteGroup = async (groupId: number) => {
    const group = groups.find(g => g.id === groupId);
    if (!group || group.is_builtin) return;

    if (!confirm(`Delete tag group "${group.name}" and all its tags?`)) return;

    try {
      await api.deleteTagGroup(groupId);
      await loadGroups();
    } catch (err) {
      notifications.error('Failed to delete tag group', 'Tags');
      console.error(err);
    }
  };

  const filteredGroups = searchQuery
    ? groups.filter(g => g.name.toLowerCase().includes(searchQuery.toLowerCase()))
    : groups;

  const totalTags = groups.reduce((sum, g) => sum + (g.tag_count || 0), 0);

  return (
    <div className="tag-engine-section">
      <div className="tag-engine-header">
        <div className="header-info">
          <h3>Tags</h3>
          <p className="header-description">
            Manage tag vocabularies used by normalization rules for pattern matching.
          </p>
        </div>
        <div className="header-stats">
          <span className="stat">
            <span className="material-icons">folder</span>
            {groups.length} groups
          </span>
          <span className="stat">
            <span className="material-icons">label</span>
            {totalTags} tags
          </span>
        </div>
      </div>

      <div className="tag-engine-toolbar">
        <div className="search-box">
          <span className="material-icons">search</span>
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search groups..."
          />
          {searchQuery && (
            <button className="clear-search" onClick={() => setSearchQuery('')}>
              <span className="material-icons">close</span>
            </button>
          )}
        </div>
        <button className="btn-primary" onClick={() => setShowCreateModal(true)}>
          <span className="material-icons">add</span>
          New Group
        </button>
      </div>

      {loading ? (
        <div className="tag-engine-loading">
          <span className="material-icons spinning">sync</span>
          Loading tag groups...
        </div>
      ) : filteredGroups.length === 0 ? (
        <div className="tag-engine-empty">
          {searchQuery ? (
            <>No groups match "{searchQuery}"</>
          ) : (
            <>No tag groups found. Create one to get started.</>
          )}
        </div>
      ) : (
        <div className="tag-groups-list">
          {filteredGroups.map(group => (
            <div key={group.id} className="tag-group-wrapper">
              <TagGroupCard
                group={group}
                isExpanded={expandedGroupId === group.id}
                onToggleExpand={() => setExpandedGroupId(
                  expandedGroupId === group.id ? null : group.id
                )}
                onRefresh={loadGroups}
              />
              {!group.is_builtin && (
                <button
                  className="delete-group-btn"
                  onClick={() => handleDeleteGroup(group.id)}
                  title="Delete group"
                >
                  <span className="material-icons">delete</span>
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Create Group Modal */}
      {showCreateModal && (
        <ModalOverlay onClose={() => setShowCreateModal(false)}>
          <div className="modal-content">
            <div className="modal-header">
              <h3>Create Tag Group</h3>
              <button className="modal-close" onClick={() => setShowCreateModal(false)}>
                <span className="material-icons">close</span>
              </button>
            </div>
            <div className="modal-body">
              <div className="form-group">
                <label>Name</label>
                <input
                  type="text"
                  value={newGroupName}
                  onChange={(e) => setNewGroupName(e.target.value)}
                  placeholder="e.g., Custom Tags"
                  autoFocus
                />
              </div>
              <div className="form-group">
                <label>Description (optional)</label>
                <input
                  type="text"
                  value={newGroupDescription}
                  onChange={(e) => setNewGroupDescription(e.target.value)}
                  placeholder="e.g., Custom vocabulary for matching"
                />
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={() => setShowCreateModal(false)}>
                Cancel
              </button>
              <button
                className="btn-primary"
                onClick={handleCreateGroup}
                disabled={!newGroupName.trim()}
              >
                Create Group
              </button>
            </div>
          </div>
        </ModalOverlay>
      )}
    </div>
  );
}

import { useState, useEffect, useCallback } from 'react';
import type { Logo } from '../../types';
import * as api from '../../services/api';
import { LogoModal } from '../LogoModal';
import { ModalOverlay } from '../ModalOverlay';
import './LogoManagerTab.css';
import { useNotifications } from '../../contexts/NotificationContext';

type ViewMode = 'list' | 'grid';

export function LogoManagerTab() {
  const notifications = useNotifications();

  // Data state
  const [logos, setLogos] = useState<Logo[]>([]);
  const [loading, setLoading] = useState(true);

  // Search state
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');

  // View state
  const [viewMode, setViewMode] = useState<ViewMode>('list');

  // Modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [editingLogo, setEditingLogo] = useState<Logo | null>(null);

  // Delete confirmation state
  const [deletingLogo, setDeletingLogo] = useState<Logo | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);

  // Track logos with failed image loads
  const [failedImages, setFailedImages] = useState<Set<number>>(new Set());

  // Load all logos (no pagination)
  const loadLogos = useCallback(async () => {
    setLoading(true);
    setFailedImages(new Set()); // Clear failed images on reload
    try {
      // Request a large page size to get all logos
      const result = await api.getLogos({ page: 1, pageSize: 10000, search });
      setLogos(result.results);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to load logos', 'Logos');
    } finally {
      setLoading(false);
    }
  }, [search]);

  // Handle image load error
  const handleImageError = useCallback((logoId: number) => {
    setFailedImages(prev => new Set(prev).add(logoId));
  }, []);

  useEffect(() => {
    loadLogos();
  }, [loadLogos]);

  // Debounced search
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const handleAddLogo = () => {
    setEditingLogo(null);
    setModalOpen(true);
  };

  const handleEditLogo = (logo: Logo) => {
    setEditingLogo(logo);
    setModalOpen(true);
  };

  const handleDeleteClick = (logo: Logo) => {
    setDeletingLogo(logo);
  };

  const handleConfirmDelete = async () => {
    if (!deletingLogo) return;

    setDeleteLoading(true);
    try {
      await api.deleteLogo(deletingLogo.id);
      setDeletingLogo(null);
      loadLogos();
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to delete logo', 'Logos');
    } finally {
      setDeleteLoading(false);
    }
  };

  const handleCopyUrl = async (url: string) => {
    try {
      await navigator.clipboard.writeText(url);
    } catch (err) {
      console.error('Failed to copy URL:', err);
    }
  };

  const handleModalSaved = () => {
    loadLogos();
  };

  const handleModalClose = () => {
    setModalOpen(false);
    setEditingLogo(null);
  };

  // Render loading state
  if (loading && logos.length === 0) {
    return (
      <div className="logo-manager-tab">
        <div className="loading-state">
          <span className="material-icons spinning">sync</span>
          <p>Loading logos...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="logo-manager-tab">
      {/* Header */}
      <div className="logo-header">
        <div className="header-title">
          <h2>Logos</h2>
          <p className="header-description">
            Manage logos for your channels ({logos.length} total)
          </p>
        </div>
        <div className="header-actions">
          {/* Search */}
          <div className="search-box">
            <span className="material-icons">search</span>
            <input
              type="text"
              placeholder="Search logos..."
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
            />
            {searchInput && (
              <button
                type="button"
                className="clear-search"
                onClick={() => setSearchInput('')}
                title="Clear search"
              >
                <span className="material-icons">close</span>
              </button>
            )}
          </div>

          {/* View Toggle */}
          <div className="view-toggle">
            <button
              className={viewMode === 'list' ? 'active' : ''}
              onClick={() => setViewMode('list')}
              title="List view"
            >
              <span className="material-icons">view_list</span>
            </button>
            <button
              className={viewMode === 'grid' ? 'active' : ''}
              onClick={() => setViewMode('grid')}
              title="Grid view"
            >
              <span className="material-icons">grid_view</span>
            </button>
          </div>

          {/* Add Logo Button */}
          <button className="btn-primary" onClick={handleAddLogo}>
            <span className="material-icons">add</span>
            Add Logo
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="logos-container">
        {logos.length === 0 ? (
          // Empty State
          <div className="empty-state">
            <span className="material-icons">image</span>
            <h3>{search ? 'No logos found' : 'No logos yet'}</h3>
            <p>
              {search
                ? 'Try adjusting your search terms'
                : 'Add your first logo to get started'}
            </p>
            {!search && (
              <button className="btn-primary" onClick={handleAddLogo}>
                <span className="material-icons">add</span>
                Add Logo
              </button>
            )}
          </div>
        ) : viewMode === 'list' ? (
          // List View
          <div className="logos-list">
            <div className="list-header">
              <span>Logo</span>
              <span>Name</span>
              <span>URL</span>
              <span>Used By</span>
              <span>Actions</span>
            </div>
            {logos.map((logo) => (
              <div key={logo.id} className="logo-row">
                <div className="logo-thumbnail">
                  {failedImages.has(logo.id) ? (
                    <span className="placeholder">
                      <span className="material-icons">broken_image</span>
                    </span>
                  ) : logo.cache_url || logo.url ? (
                    <img
                      src={logo.cache_url || logo.url}
                      alt={logo.name}
                      onError={() => handleImageError(logo.id)}
                    />
                  ) : (
                    <span className="placeholder">
                      <span className="material-icons">image</span>
                    </span>
                  )}
                </div>
                <div className="logo-name">{logo.name}</div>
                <div className="logo-url-cell">
                  <span className="logo-url" title={logo.url}>
                    {logo.url}
                  </span>
                  <button
                    className="copy-btn"
                    onClick={() => handleCopyUrl(logo.url)}
                    title="Copy URL"
                  >
                    <span className="material-icons">content_copy</span>
                  </button>
                </div>
                <div className={`logo-count ${logo.channel_count > 0 ? 'in-use' : ''}`}>
                  {logo.channel_count} {logo.channel_count === 1 ? 'channel' : 'channels'}
                </div>
                <div className="logo-actions">
                  <button
                    className="action-btn"
                    onClick={() => handleEditLogo(logo)}
                    title="Edit"
                  >
                    <span className="material-icons">edit</span>
                  </button>
                  <button
                    className="action-btn delete"
                    onClick={() => handleDeleteClick(logo)}
                    title="Delete"
                  >
                    <span className="material-icons">delete</span>
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          // Grid View
          <div className="logos-grid">
            {logos.map((logo) => (
              <div key={logo.id} className="logo-card">
                <div className="card-thumbnail">
                  {failedImages.has(logo.id) ? (
                    <span className="placeholder">
                      <span className="material-icons">broken_image</span>
                    </span>
                  ) : logo.cache_url || logo.url ? (
                    <img
                      src={logo.cache_url || logo.url}
                      alt={logo.name}
                      onError={() => handleImageError(logo.id)}
                    />
                  ) : (
                    <span className="placeholder">
                      <span className="material-icons">image</span>
                    </span>
                  )}
                </div>
                <div className="card-info">
                  <div className="card-name">
                    <span title={logo.name}>{logo.name}</span>
                    <span
                      className={`channel-badge ${logo.channel_count > 0 ? 'in-use' : ''}`}
                    >
                      {logo.channel_count}
                    </span>
                  </div>
                  <div className="card-actions">
                    <button
                      className="action-btn"
                      onClick={() => handleEditLogo(logo)}
                      title="Edit"
                    >
                      <span className="material-icons">edit</span>
                    </button>
                    <button
                      className="action-btn"
                      onClick={() => handleCopyUrl(logo.url)}
                      title="Copy URL"
                    >
                      <span className="material-icons">content_copy</span>
                    </button>
                    <button
                      className="action-btn delete"
                      onClick={() => handleDeleteClick(logo)}
                      title="Delete"
                    >
                      <span className="material-icons">delete</span>
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Logo Modal */}
      <LogoModal
        isOpen={modalOpen}
        onClose={handleModalClose}
        onSaved={handleModalSaved}
        logo={editingLogo}
      />

      {/* Delete Confirmation Modal */}
      {deletingLogo && (
        <ModalOverlay onClose={() => setDeletingLogo(null)}>
          <div
            className="modal-content delete-confirm-modal"
          >
            <div className="modal-header">
              <h2>Delete Logo</h2>
              <button className="close-btn" onClick={() => setDeletingLogo(null)}>
                &times;
              </button>
            </div>
            <div className="modal-body">
              <p>
                Are you sure you want to delete <strong>{deletingLogo.name}</strong>?
              </p>
              {deletingLogo.channel_count > 0 && (
                <div className="warning-message">
                  <span className="material-icons">warning</span>
                  <span>
                    This logo is used by {deletingLogo.channel_count}{' '}
                    {deletingLogo.channel_count === 1 ? 'channel' : 'channels'}.
                  </span>
                </div>
              )}
            </div>
            <div className="modal-footer">
              <button
                className="btn-secondary"
                onClick={() => setDeletingLogo(null)}
                disabled={deleteLoading}
              >
                Cancel
              </button>
              <button
                className="btn-danger"
                onClick={handleConfirmDelete}
                disabled={deleteLoading}
              >
                {deleteLoading ? 'Deleting...' : 'Delete'}
              </button>
            </div>
          </div>
        </ModalOverlay>
      )}
    </div>
  );
}

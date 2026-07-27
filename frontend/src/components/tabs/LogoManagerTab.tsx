import { logger } from '../../utils/logger';
import { useState, useEffect, useCallback } from 'react';
import type { Logo } from '../../types';
import * as api from '../../services/api';
import { LogoModal } from '../LogoModal';
import { ModalOverlay } from '../ModalOverlay';
import { RouteHeaderSlot } from '../RouteHeaderSlots';
import { SourceLoadStatus } from '../SourceLoadStatus';
import { classifySourceLoadError, type SourceLoadState } from '../sourceLoadState';
import './LogoManagerTab.css';
import { useNotifications } from '../../contexts/NotificationContext';

type ViewMode = 'list' | 'grid';
type SortColumn = 'name' | 'channel_count';
type SortOrder = 'asc' | 'desc';
const PAGE_SIZE_OPTIONS = [25, 50, 100, 250] as const;

export function LogoManagerTab() {
  const notifications = useNotifications();

  // Data state — one server-fetched page at a time (bead
  // enhancedchannelmanager-09x38.13). Previously this component called
  // api.getAllLogos(1000) to pull the ENTIRE catalog (2,946+ logos) into the
  // browser and did search/sort/pagination client-side. Sort and the
  // unused-only filter are now server-side (see api.getLogos /
  // backend/routers/channels.py get_logos()), so the tab fetches only the
  // current page — sort/filter/search/pagination all compose in one request.
  const [logos, setLogos] = useState<Logo[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [sourceLoadState, setSourceLoadState] = useState<SourceLoadState>('loading');

  // Search state
  const [searchInput, setSearchInput] = useState('');

  // Sort state — defaults match Dispatcharr's own default ordering (by name,
  // ascending) so a fresh page load looks identical to the old behavior.
  const [sortBy, setSortBy] = useState<SortColumn>('name');
  const [sortOrder, setSortOrder] = useState<SortOrder>('asc');

  // Unused-only filter (channel_count === 0)
  const [unusedOnly, setUnusedOnly] = useState(false);

  // Pagination state
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<number>(50);

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

  // Load the current page of logos from the server, with the active
  // search/sort/unused-only filter composed into one request.
  const loadLogos = useCallback(async () => {
    setLoading(true);
    setSourceLoadState('loading');
    setFailedImages(new Set());
    try {
      const response = await api.getLogos({
        page,
        pageSize,
        search: searchInput || undefined,
        sortBy,
        sortOrder,
        unusedOnly,
      });
      setLogos(response.results);
      setTotalCount(response.count);
      setSourceLoadState('success');
    } catch (err) {
      setSourceLoadState(classifySourceLoadError(err));
      notifications.error(err instanceof Error ? err.message : 'Failed to load logos', 'Logos');
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, searchInput, sortBy, sortOrder, unusedOnly, notifications]);

  // Handle image load error
  const handleImageError = useCallback((logoId: number) => {
    setFailedImages(prev => new Set(prev).add(logoId));
  }, []);

  useEffect(() => {
    loadLogos();
  }, [loadLogos]);

  // Reset to page 1 whenever search, sort, the unused-only filter, or page
  // size changes — the previous page number is meaningless against a new
  // result set.
  useEffect(() => {
    setPage(1);
  }, [searchInput, sortBy, sortOrder, unusedOnly, pageSize]);

  // Column sort handler — mirrors M3UChangesTab's sortable-header pattern
  // (docs/style_guide.md): same column toggles direction, a new column
  // defaults to ascending.
  const handleSort = (column: SortColumn) => {
    if (sortBy === column) {
      setSortOrder(prev => (prev === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortBy(column);
      setSortOrder('asc');
    }
  };

  const getSortIndicator = (column: SortColumn) => {
    if (sortBy !== column) return null;
    return sortOrder === 'asc' ? 'arrow_upward' : 'arrow_downward';
  };

  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));

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
      logger.error('Failed to copy URL:', err);
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
  if (loading && logos.length === 0 && totalCount === 0) {
    return (
      <div className="logo-manager-tab">
        <RouteHeaderSlot name="status">
          <SourceLoadStatus state={sourceLoadState} successText="0 total logos" />
        </RouteHeaderSlot>
        <div className="tab-loading">
          <span className="material-icons spinning">sync</span>
          <p>Loading logos...</p>
        </div>
      </div>
    );
  }
  if (sourceLoadState !== 'success' && logos.length === 0) {
    return (
      <div className="logo-manager-tab">
        <RouteHeaderSlot name="status">
          <SourceLoadStatus state={sourceLoadState} successText="" />
        </RouteHeaderSlot>
        <div className="tab-load-unavailable">
          <SourceLoadStatus state={sourceLoadState} successText="" />
        </div>
      </div>
    );
  }

  const filtersActive = Boolean(searchInput) || unusedOnly;

  return (
    <div className="logo-manager-tab">
      <RouteHeaderSlot name="primary-action">
        <button className="btn-primary" onClick={handleAddLogo}>
          <span className="material-icons">add</span>
          Add Logo
        </button>
      </RouteHeaderSlot>
      <RouteHeaderSlot name="status">
        <SourceLoadStatus state={sourceLoadState} successText={`${totalCount}${filtersActive ? ' matching' : ' total'} logos`} />
      </RouteHeaderSlot>
      <RouteHeaderSlot name="controls">
        <div className="logo-header header-actions">
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
                  aria-label="Clear search"
                >
                  <span className="material-icons" aria-hidden="true">close</span>
                </button>
              )}
            </div>

            {/* Unused-only filter toggle */}
            <button
              type="button"
              className={`unused-only-toggle${unusedOnly ? ' active' : ''}`}
              onClick={() => setUnusedOnly(prev => !prev)}
              title="Show only logos with no channels using them"
              aria-label="Show only unused logos"
              aria-pressed={unusedOnly}
            >
              <span className="material-icons" aria-hidden="true">filter_alt</span>
              Unused only
            </button>

            {/* View Toggle */}
            <div className="view-toggle">
              <button
                className={viewMode === 'list' ? 'active' : ''}
                onClick={() => setViewMode('list')}
                title="List view"
                aria-label="List view"
                aria-pressed={viewMode === 'list'}
              >
                <span className="material-icons" aria-hidden="true">view_list</span>
              </button>
              <button
                className={viewMode === 'grid' ? 'active' : ''}
                onClick={() => setViewMode('grid')}
                title="Grid view"
                aria-label="Grid view"
                aria-pressed={viewMode === 'grid'}
              >
                <span className="material-icons" aria-hidden="true">grid_view</span>
              </button>
            </div>

        </div>
      </RouteHeaderSlot>

      {/* Content */}
      <div className="logos-container">
        {logos.length === 0 ? (
          // Empty State
          <div className="empty-state">
            <span className="material-icons">image</span>
            <h3>{filtersActive ? 'No logos found' : 'No logos yet'}</h3>
            <p>
              {filtersActive
                ? 'Try adjusting your search or filters'
                : 'Add your first logo to get started'}
            </p>
            {!filtersActive && (
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
              <span className="sortable" onClick={() => handleSort('name')}>
                Name
                {getSortIndicator('name') && (
                  <span className="material-icons sort-icon">{getSortIndicator('name')}</span>
                )}
              </span>
              <span>URL</span>
              <span className="sortable" onClick={() => handleSort('channel_count')}>
                Used By
                {getSortIndicator('channel_count') && (
                  <span className="material-icons sort-icon">{getSortIndicator('channel_count')}</span>
                )}
              </span>
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
                  <span className="logo-url" title={logo.cache_url || logo.url}>
                    {logo.cache_url || logo.url}
                  </span>
                  <button
                    className="copy-btn"
                    onClick={() => handleCopyUrl(logo.cache_url || logo.url)}
                    title="Copy URL"
                    aria-label="Copy logo URL"
                  >
                    <span className="material-icons" aria-hidden="true">content_copy</span>
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
                    aria-label="Edit logo"
                  >
                    <span className="material-icons" aria-hidden="true">edit</span>
                  </button>
                  <button
                    className="action-btn delete"
                    onClick={() => handleDeleteClick(logo)}
                    title="Delete"
                    aria-label="Delete logo"
                  >
                    <span className="material-icons" aria-hidden="true">delete</span>
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          // Grid View — same server-sorted/filtered page as List View, just
          // no column headers to click (there's nothing to sort by in a grid).
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
                      aria-label="Edit logo"
                    >
                      <span className="material-icons" aria-hidden="true">edit</span>
                    </button>
                    <button
                      className="action-btn"
                      onClick={() => handleCopyUrl(logo.cache_url || logo.url)}
                      title="Copy URL"
                      aria-label="Copy logo URL"
                    >
                      <span className="material-icons" aria-hidden="true">content_copy</span>
                    </button>
                    <button
                      className="action-btn delete"
                      onClick={() => handleDeleteClick(logo)}
                      title="Delete"
                      aria-label="Delete logo"
                    >
                      <span className="material-icons" aria-hidden="true">delete</span>
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Pagination */}
      {totalCount > 0 && (
        <div className="logo-pagination">
          <div className="pagination-left">
            <span className="pagination-info">
              {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, totalCount)} of {totalCount}
            </span>
          </div>
          <div className="pagination-center">
            <button
              className="page-btn"
              onClick={() => setPage(1)}
              disabled={page <= 1 || loading}
              title="First page"
              aria-label="First page"
            >
              <span className="material-icons" aria-hidden="true">first_page</span>
            </button>
            <button
              className="page-btn"
              onClick={() => setPage(p => p - 1)}
              disabled={page <= 1 || loading}
              title="Previous page"
              aria-label="Previous page"
            >
              <span className="material-icons" aria-hidden="true">chevron_left</span>
            </button>
            <span className="page-indicator">
              Page {page} of {totalPages}
            </span>
            <button
              className="page-btn"
              onClick={() => setPage(p => p + 1)}
              disabled={page >= totalPages || loading}
              title="Next page"
              aria-label="Next page"
            >
              <span className="material-icons" aria-hidden="true">chevron_right</span>
            </button>
            <button
              className="page-btn"
              onClick={() => setPage(totalPages)}
              disabled={page >= totalPages || loading}
              title="Last page"
              aria-label="Last page"
            >
              <span className="material-icons" aria-hidden="true">last_page</span>
            </button>
          </div>
          <div className="pagination-right">
            <label className="page-size-label">
              Per page:
              <select
                value={pageSize}
                onChange={(e) => setPageSize(Number(e.target.value))}
              >
                {PAGE_SIZE_OPTIONS.map(size => (
                  <option key={size} value={size}>{size}</option>
                ))}
              </select>
            </label>
          </div>
        </div>
      )}

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

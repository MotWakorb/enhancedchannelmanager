import { useState, useEffect, useRef, useCallback, memo } from 'react';
import type { Logo } from '../types';
import * as api from '../services/api';
import './ModalBase.css';
import './LogoModal.css';

interface LogoModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
  logo: Logo | null; // null = creating new
}

export const LogoModal = memo(function LogoModal({ isOpen, onClose, onSaved, logo }: LogoModalProps) {
  const isEdit = logo !== null;
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Form state
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  // Drag & drop state
  const [isDragging, setIsDragging] = useState(false);

  // UI state
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset form when modal opens/closes or logo changes
  useEffect(() => {
    if (isOpen) {
      if (logo) {
        // Editing existing logo
        setName(logo.name);
        setUrl(logo.url);
        setFile(null);
        setPreviewUrl(logo.cache_url || logo.url);
      } else {
        // Creating new logo - reset to defaults
        setName('');
        setUrl('');
        setFile(null);
        setPreviewUrl(null);
      }
      setError(null);
      setIsDragging(false);
    }
  }, [isOpen, logo]);

  // Clean up preview URL when file changes
  useEffect(() => {
    if (file) {
      const objectUrl = URL.createObjectURL(file);
      setPreviewUrl(objectUrl);
      return () => URL.revokeObjectURL(objectUrl);
    }
  }, [file]);

  // Update preview when URL changes (debounced)
  useEffect(() => {
    if (url && !file) {
      const timer = setTimeout(() => {
        setPreviewUrl(url);
      }, 500);
      return () => clearTimeout(timer);
    }
  }, [url, file]);

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    // Only set dragging false if we're leaving the drop zone entirely
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setIsDragging(false);
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    const files = e.dataTransfer.files;
    if (files.length > 0) {
      const droppedFile = files[0];
      if (isValidImageFile(droppedFile)) {
        setFile(droppedFile);
        setUrl(''); // Clear URL when file is dropped
        setError(null);
        // Auto-fill name from filename if empty
        if (!name) {
          const nameWithoutExt = droppedFile.name.replace(/\.[^.]+$/, '');
          setName(nameWithoutExt);
        }
      } else {
        setError('Invalid file type. Please use PNG, JPG, GIF, SVG, or WebP.');
      }
    }
  }, [name]);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      const selectedFile = files[0];
      if (isValidImageFile(selectedFile)) {
        setFile(selectedFile);
        setUrl(''); // Clear URL when file is selected
        setError(null);
        // Auto-fill name from filename if empty
        if (!name) {
          const nameWithoutExt = selectedFile.name.replace(/\.[^.]+$/, '');
          setName(nameWithoutExt);
        }
      } else {
        setError('Invalid file type. Please use PNG, JPG, GIF, SVG, or WebP.');
      }
    }
  }, [name]);

  const isValidImageFile = (file: File): boolean => {
    const validTypes = ['image/png', 'image/jpeg', 'image/gif', 'image/svg+xml', 'image/webp'];
    return validTypes.includes(file.type);
  };

  const handleBrowseClick = () => {
    fileInputRef.current?.click();
  };

  const handleClearFile = () => {
    setFile(null);
    setPreviewUrl(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleSave = async () => {
    // Validation
    if (!name.trim()) {
      setError('Name is required');
      return;
    }

    if (isEdit) {
      // Edit mode requires URL
      if (!url.trim()) {
        setError('Logo URL is required');
        return;
      }
    } else {
      // Create mode requires file or URL
      if (!file && !url.trim()) {
        setError('Please upload an image or provide a URL');
        return;
      }
    }

    setLoading(true);
    setError(null);

    try {
      if (isEdit) {
        // Update existing logo
        await api.updateLogo(logo!.id, {
          name: name.trim(),
          url: url.trim(),
        });
      } else if (file) {
        // Upload new logo file
        // Note: The uploadLogo API should handle the name, but if not,
        // we may need to update immediately after
        const newLogo = await api.uploadLogo(file);
        // Update name if different from what was uploaded
        if (newLogo.name !== name.trim()) {
          await api.updateLogo(newLogo.id, { name: name.trim() });
        }
      } else {
        // Create from URL
        await api.createLogo({
          name: name.trim(),
          url: url.trim(),
        });
      }

      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save logo');
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-container logo-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{isEdit ? 'Edit Logo' : 'Add Logo'}</h2>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="modal-body">
          {/* Logo Name */}
          <div className="modal-form-group">
            <label htmlFor="logoName">Logo Name</label>
            <input
              id="logoName"
              type="text"
              placeholder="ESPN, Fox Sports, CNN..."
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          {isEdit ? (
            // Edit mode - show current logo and URL input
            <>
              {previewUrl && (
                <div className="current-logo-preview">
                  <label>Current Logo</label>
                  <div className="preview-image-container">
                    <img
                      src={previewUrl}
                      alt={name || 'Logo preview'}
                      onError={() => setPreviewUrl(null)}
                    />
                  </div>
                </div>
              )}

              <div className="modal-form-group">
                <label htmlFor="logoUrl">Logo URL</label>
                <input
                  id="logoUrl"
                  type="text"
                  placeholder="https://example.com/logo.png"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                />
              </div>
            </>
          ) : (
            // Create mode - drag & drop or URL
            <>
              {/* Hidden file input */}
              <input
                ref={fileInputRef}
                type="file"
                accept="image/png,image/jpeg,image/gif,image/svg+xml,image/webp"
                onChange={handleFileSelect}
                style={{ display: 'none' }}
              />

              {/* Drag & Drop Zone */}
              <div
                className={`drop-zone ${isDragging ? 'dragging' : ''} ${file ? 'has-file' : ''}`}
                onDragEnter={handleDragEnter}
                onDragLeave={handleDragLeave}
                onDragOver={handleDragOver}
                onDrop={handleDrop}
                onClick={!file ? handleBrowseClick : undefined}
              >
                {file ? (
                  <div className="file-preview">
                    {previewUrl && (
                      <img
                        src={previewUrl}
                        alt="Preview"
                        className="preview-thumbnail"
                      />
                    )}
                    <div className="file-info">
                      <span className="file-name">{file.name}</span>
                      <span className="file-size">
                        {(file.size / 1024).toFixed(1)} KB
                      </span>
                    </div>
                    <button
                      className="clear-file-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleClearFile();
                      }}
                      title="Remove file"
                    >
                      <span className="material-icons">close</span>
                    </button>
                  </div>
                ) : (
                  <div className="drop-zone-content">
                    <span className="material-icons drop-icon">cloud_upload</span>
                    <p className="drop-text">
                      Drag & drop an image here
                      <br />
                      <span className="drop-subtext">or click to browse</span>
                    </p>
                    <p className="file-types">PNG, JPG, GIF, SVG, WebP</p>
                  </div>
                )}
              </div>

              {/* Or divider */}
              <div className="or-divider">
                <span className="divider-line"></span>
                <span className="or-text">or</span>
                <span className="divider-line"></span>
              </div>

              {/* URL Input */}
              <div className="modal-form-group">
                <label htmlFor="logoUrlCreate">Logo URL</label>
                <input
                  id="logoUrlCreate"
                  type="text"
                  placeholder="https://example.com/logo.png"
                  value={url}
                  onChange={(e) => {
                    setUrl(e.target.value);
                    setFile(null); // Clear file when URL is entered
                  }}
                />
                {url && previewUrl && !file && (
                  <div className="url-preview">
                    <img
                      src={previewUrl}
                      alt="URL preview"
                      onError={() => setPreviewUrl(null)}
                    />
                  </div>
                )}
              </div>
            </>
          )}

          {error && <div className="error-message">{error}</div>}
        </div>

        <div className="modal-footer">
          <button className="modal-btn modal-btn-primary" onClick={handleSave} disabled={loading}>
            {loading ? 'Saving...' : isEdit ? 'Save Changes' : 'Add Logo'}
          </button>
        </div>
      </div>
    </div>
  );
});

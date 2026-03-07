import { useState, useCallback } from 'react';
import * as exportApi from '../../services/exportApi';
import type { ProfilePreview } from '../../types/export';

interface ExportPreviewProps {
  profileId: number;
}

export function ExportPreview({ profileId }: ExportPreviewProps) {
  const [preview, setPreview] = useState<ProfilePreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const loadPreview = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await exportApi.previewProfile(profileId);
      setPreview(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load preview');
    } finally {
      setLoading(false);
    }
  }, [profileId]);

  return (
    <div className="export-preview">
      <div className="export-preview-header">
        <h4>M3U Preview</h4>
        <button
          type="button"
          className="btn btn-sm"
          onClick={loadPreview}
          disabled={loading}
        >
          <span className={`material-icons${loading ? ' spinning' : ''}`}>
            {loading ? 'sync' : 'refresh'}
          </span>
          {preview ? 'Refresh' : 'Load Preview'}
        </button>
      </div>
      {error && <div className="export-preview-error">{error}</div>}
      {preview && (
        <>
          <div className="export-preview-stats">
            <span>Total channels: {preview.total_count}</span>
          </div>
          <pre className="export-preview-code">{preview.m3u_preview}</pre>
        </>
      )}
    </div>
  );
}

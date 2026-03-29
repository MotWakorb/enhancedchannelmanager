import { useState } from 'react';
import * as exportApi from '../../services/exportApi';
import type { PlaylistProfile, GenerationResult } from '../../types/export';
import { useNotifications } from '../../contexts/NotificationContext';

interface GenerateControlsProps {
  profile: PlaylistProfile;
  onGenerated: () => void;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function GenerateControls({ profile, onGenerated }: GenerateControlsProps) {
  const notifications = useNotifications();
  const [generating, setGenerating] = useState(false);
  const [lastResult, setLastResult] = useState<GenerationResult | null>(null);

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      const result = await exportApi.generateExport(profile.id);
      setLastResult(result);
      notifications.success(
        `Generated ${result.channels_count} channels (M3U: ${formatSize(result.m3u_size)}, XMLTV: ${formatSize(result.xmltv_size)}) in ${result.duration_ms}ms`
      );
      onGenerated();
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Generation failed');
    } finally {
      setGenerating(false);
    }
  };

  const handleDownloadM3U = async () => {
    try {
      await exportApi.downloadM3U(profile.id);
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Download failed');
    }
  };

  const handleDownloadXMLTV = async () => {
    try {
      await exportApi.downloadXMLTV(profile.id);
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Download failed');
    }
  };

  const hasFiles = profile.has_generated || lastResult;

  return (
    <div className="generate-controls">
      <div className="generate-controls-buttons">
        <button
          className="btn btn-primary"
          onClick={handleGenerate}
          disabled={generating}
        >
          <span className={`material-icons${generating ? ' spinning' : ''}`}>
            {generating ? 'sync' : 'play_arrow'}
          </span>
          {generating ? 'Generating...' : 'Generate'}
        </button>
        <button
          className="btn"
          onClick={handleDownloadM3U}
          disabled={!hasFiles}
          title={hasFiles ? 'Download M3U file' : 'Generate first'}
        >
          <span className="material-icons">download</span>
          M3U
        </button>
        <button
          className="btn"
          onClick={handleDownloadXMLTV}
          disabled={!hasFiles}
          title={hasFiles ? 'Download XMLTV file' : 'Generate first'}
        >
          <span className="material-icons">download</span>
          XMLTV
        </button>
      </div>
      {(lastResult || profile.has_generated) && (
        <div className="generate-controls-stats">
          {lastResult ? (
            <>
              <span>{lastResult.channels_count} channels</span>
              <span>M3U: {formatSize(lastResult.m3u_size)}</span>
              <span>XMLTV: {formatSize(lastResult.xmltv_size)}</span>
              <span>{lastResult.duration_ms}ms</span>
            </>
          ) : (
            <>
              {profile.m3u_size != null && <span>M3U: {formatSize(profile.m3u_size)}</span>}
              {profile.xmltv_size != null && <span>XMLTV: {formatSize(profile.xmltv_size)}</span>}
            </>
          )}
        </div>
      )}
    </div>
  );
}

import { memo } from 'react';
import './ModalBase.css';

interface VLCProtocolHelperModalProps {
  isOpen: boolean;
  onClose: () => void;
  onDownloadM3U: () => void;
  streamName: string;
}

export const VLCProtocolHelperModal = memo(function VLCProtocolHelperModal({
  isOpen,
  onClose,
  onDownloadM3U,
  streamName: _streamName,
}: VLCProtocolHelperModalProps) {
  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-container modal-lg" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>VLC Protocol Not Available</h2>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="modal-body">
          <div className="modal-info-icon">
            <span className="material-icons">info</span>
          </div>

          <p className="modal-info-intro">
            The VLC protocol (vlc://) couldn't be opened. This usually happens when:
          </p>

          <ul className="modal-bullet-list">
            <li>VLC is not installed on your device</li>
            <li>Your browser requires a protocol handler extension</li>
            <li>VLC protocol handlers are not registered with your operating system</li>
          </ul>

          <div className="modal-section">
            <h3 className="modal-section-title">Browser-Specific Solutions</h3>

            <div className="modal-info-card">
              <strong>Chrome/Edge:</strong>
              <p>
                Install the "Open in VLC media player" extension from your browser's web store.
                This extension enables the vlc:// protocol handler.
              </p>
            </div>

            <div className="modal-info-card">
              <strong>Firefox:</strong>
              <p>
                Install the "Open in VLC" add-on from Firefox Add-ons.
                Firefox may also prompt you to set up the protocol handler when you first try to use it.
              </p>
            </div>

            <div className="modal-info-card">
              <strong>Safari:</strong>
              <p>
                VLC protocol support is built-in if VLC is installed.
                Make sure VLC is installed and set as the default handler for streaming URLs.
              </p>
            </div>
          </div>

          <div className="modal-section">
            <h3 className="modal-section-title">Alternative: Download M3U File</h3>
            <p className="modal-info-text">
              You can download an M3U playlist file for this stream. Most systems will automatically
              open M3U files with VLC if it's installed.
            </p>
          </div>
        </div>

        <div className="modal-footer">
          <button
            type="button"
            className="modal-btn modal-btn-primary"
            onClick={() => {
              onDownloadM3U();
              onClose();
            }}
          >
            <span className="material-icons">download</span>
            Download M3U File
          </button>
        </div>
      </div>
    </div>
  );
});

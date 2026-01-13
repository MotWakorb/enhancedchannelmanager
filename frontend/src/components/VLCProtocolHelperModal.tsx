import './VLCProtocolHelperModal.css';

interface VLCProtocolHelperModalProps {
  isOpen: boolean;
  onClose: () => void;
  onDownloadM3U: () => void;
  streamName: string;
}

export function VLCProtocolHelperModal({
  isOpen,
  onClose,
  onDownloadM3U,
  streamName: _streamName,
}: VLCProtocolHelperModalProps) {
  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content vlc-helper-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>VLC Protocol Not Available</h2>
          <button className="close-btn" onClick={onClose}>×</button>
        </div>

        <div className="modal-body">
          <div className="helper-icon">
            <span className="material-icons">info</span>
          </div>

          <p className="helper-intro">
            The VLC protocol (vlc://) couldn't be opened. This usually happens when:
          </p>

          <ul className="helper-reasons">
            <li>VLC is not installed on your device</li>
            <li>Your browser requires a protocol handler extension</li>
            <li>VLC protocol handlers are not registered with your operating system</li>
          </ul>

          <div className="helper-section">
            <h3>Browser-Specific Solutions</h3>

            <div className="browser-solution">
              <strong>Chrome/Edge:</strong>
              <p>
                Install the "Open in VLC media player" extension from your browser's web store.
                This extension enables the vlc:// protocol handler.
              </p>
            </div>

            <div className="browser-solution">
              <strong>Firefox:</strong>
              <p>
                Install the "Open in VLC" add-on from Firefox Add-ons.
                Firefox may also prompt you to set up the protocol handler when you first try to use it.
              </p>
            </div>

            <div className="browser-solution">
              <strong>Safari:</strong>
              <p>
                VLC protocol support is built-in if VLC is installed.
                Make sure VLC is installed and set as the default handler for streaming URLs.
              </p>
            </div>
          </div>

          <div className="helper-section">
            <h3>Alternative: Download M3U File</h3>
            <p>
              You can download an M3U playlist file for this stream. Most systems will automatically
              open M3U files with VLC if it's installed.
            </p>
          </div>
        </div>

        <div className="modal-footer">
          <button
            type="button"
            className="btn-secondary"
            onClick={onClose}
          >
            Close
          </button>
          <button
            type="button"
            className="btn-primary"
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
}

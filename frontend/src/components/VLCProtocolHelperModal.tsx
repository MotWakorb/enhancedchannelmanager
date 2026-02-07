import { memo, useState } from 'react';
import './ModalBase.css';
import './VLCProtocolHelperModal.css';
import { ModalOverlay } from './ModalOverlay';

interface VLCProtocolHelperModalProps {
  isOpen: boolean;
  onClose: () => void;
  onDownloadM3U: () => void;
  streamName: string;
}

type OSTab = 'windows' | 'linux' | 'macos' | 'browser';

export const VLCProtocolHelperModal = memo(function VLCProtocolHelperModal({
  isOpen,
  onClose,
  onDownloadM3U,
  streamName: _streamName,
}: VLCProtocolHelperModalProps) {
  const [activeTab, setActiveTab] = useState<OSTab>('windows');
  const [copiedCommand, setCopiedCommand] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleDownloadScript = (scriptPath: string, filename: string) => {
    const link = document.createElement('a');
    link.href = scriptPath;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const handleCopyCommand = async (command: string) => {
    try {
      await navigator.clipboard.writeText(command);
      setCopiedCommand(command);
      setTimeout(() => setCopiedCommand(null), 2000);
    } catch {
      // Fallback for older browsers
      const textArea = document.createElement('textarea');
      textArea.value = command;
      document.body.appendChild(textArea);
      textArea.select();
      document.execCommand('copy');
      document.body.removeChild(textArea);
      setCopiedCommand(command);
      setTimeout(() => setCopiedCommand(null), 2000);
    }
  };

  const renderCopyableCommand = (command: string) => (
    <div className="vlc-copyable-command">
      <code>{command}</code>
      <button
        type="button"
        className="vlc-copy-btn"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          handleCopyCommand(command);
        }}
        title="Copy to clipboard"
      >
        <span className="material-icons">
          {copiedCommand === command ? 'check' : 'content_copy'}
        </span>
      </button>
    </div>
  );

  return (
    <ModalOverlay onClose={onClose}>
      <div className="modal-container modal-lg">
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
            The VLC protocol (vlc://) couldn't be opened. Choose your operating system below to set up the protocol handler:
          </p>

          {/* OS Tabs */}
          <div className="vlc-os-tabs">
            <button
              className={`vlc-os-tab ${activeTab === 'windows' ? 'active' : ''}`}
              onClick={() => setActiveTab('windows')}
            >
              <span className="material-icons">desktop_windows</span>
              Windows
            </button>
            <button
              className={`vlc-os-tab ${activeTab === 'linux' ? 'active' : ''}`}
              onClick={() => setActiveTab('linux')}
            >
              <span className="material-icons">computer</span>
              Linux
            </button>
            <button
              className={`vlc-os-tab ${activeTab === 'macos' ? 'active' : ''}`}
              onClick={() => setActiveTab('macos')}
            >
              <span className="material-icons">laptop_mac</span>
              macOS
            </button>
            <button
              className={`vlc-os-tab ${activeTab === 'browser' ? 'active' : ''}`}
              onClick={() => setActiveTab('browser')}
            >
              <span className="material-icons">public</span>
              Browser
            </button>
          </div>

          {/* Windows Tab Content */}
          {activeTab === 'windows' && (
            <div className="vlc-tab-content">
              <div className="modal-section">
                <h3 className="modal-section-title">
                  <span className="material-icons">terminal</span>
                  PowerShell Script (Recommended)
                </h3>
                <p className="modal-info-text">
                  Download and run the PowerShell script to automatically register the vlc:// protocol handler in Windows Registry.
                </p>
                <div className="vlc-script-download">
                  <button
                    className="modal-btn modal-btn-primary"
                    onClick={() => handleDownloadScript('/scripts/vlc-protocol-windows.ps1', 'vlc-protocol-windows.ps1')}
                  >
                    <span className="material-icons">download</span>
                    Download PowerShell Script
                  </button>
                </div>
                <div className="vlc-instructions">
                  <strong>Instructions:</strong>
                  <ol>
                    <li>Download the script above</li>
                    <li>Open PowerShell (press <kbd>Win</kbd>+<kbd>X</kbd>, select "Windows PowerShell")</li>
                    <li>Navigate to Downloads: <code>cd ~\Downloads</code></li>
                    <li>Run this command:
                      {renderCopyableCommand('powershell -ExecutionPolicy Bypass -File .\\vlc-protocol-windows.ps1')}
                    </li>
                    <li>Click "Yes" on the administrator prompt</li>
                  </ol>
                </div>
              </div>

              <div className="modal-section">
                <h3 className="modal-section-title">
                  <span className="material-icons">edit</span>
                  Manual Registry Setup
                </h3>
                <p className="modal-info-text">
                  Alternatively, you can manually add the registry entries:
                </p>
                <div className="vlc-code-block">
                  <code>
{`Windows Registry Editor Version 5.00

[HKEY_CLASSES_ROOT\\vlc]
@="URL:VLC Protocol"
"URL Protocol"=""

[HKEY_CLASSES_ROOT\\vlc\\DefaultIcon]
@="\\"C:\\\\Program Files\\\\VideoLAN\\\\VLC\\\\vlc.exe\\",0"

[HKEY_CLASSES_ROOT\\vlc\\shell\\open\\command]
@="\\"C:\\\\Program Files\\\\VideoLAN\\\\VLC\\\\vlc.exe\\" \\"%1\\""`}
                  </code>
                </div>
                <p className="vlc-note">
                  Note: Adjust the VLC path if installed in a different location.
                </p>
              </div>
            </div>
          )}

          {/* Linux Tab Content */}
          {activeTab === 'linux' && (
            <div className="vlc-tab-content">
              <div className="modal-section">
                <h3 className="modal-section-title">
                  <span className="material-icons">terminal</span>
                  Shell Script (Recommended)
                </h3>
                <p className="modal-info-text">
                  Download and run the shell script to create a .desktop file and register the vlc:// protocol handler.
                </p>
                <div className="vlc-script-download">
                  <button
                    className="modal-btn modal-btn-primary"
                    onClick={() => handleDownloadScript('/scripts/vlc-protocol-linux.sh', 'vlc-protocol-linux.sh')}
                  >
                    <span className="material-icons">download</span>
                    Download Shell Script
                  </button>
                </div>
                <div className="vlc-instructions">
                  <strong>Instructions:</strong>
                  <ol>
                    <li>Download the script above</li>
                    <li>Open a terminal in the download directory</li>
                    <li>Make it executable: <code>chmod +x vlc-protocol-linux.sh</code></li>
                    <li>Run: <code>./vlc-protocol-linux.sh</code></li>
                  </ol>
                </div>
              </div>

              <div className="modal-section">
                <h3 className="modal-section-title">
                  <span className="material-icons">edit</span>
                  Manual Setup
                </h3>
                <p className="modal-info-text">
                  Create a .desktop file at <code>~/.local/share/applications/vlc-protocol.desktop</code>:
                </p>
                <div className="vlc-code-block">
                  <code>
{`[Desktop Entry]
Name=VLC Protocol Handler
Comment=Handle vlc:// URLs
Exec=vlc %u
Terminal=false
Type=Application
MimeType=x-scheme-handler/vlc;
NoDisplay=true`}
                  </code>
                </div>
                <p className="modal-info-text" style={{ marginTop: '0.75rem' }}>
                  Then register it with:
                </p>
                <div className="vlc-code-block">
                  <code>xdg-mime default vlc-protocol.desktop x-scheme-handler/vlc</code>
                </div>
              </div>
            </div>
          )}

          {/* macOS Tab Content */}
          {activeTab === 'macos' && (
            <div className="vlc-tab-content">
              <div className="modal-section">
                <h3 className="modal-section-title">
                  <span className="material-icons">terminal</span>
                  Shell Script (Recommended)
                </h3>
                <p className="modal-info-text">
                  Download and run the shell script to create a VLC protocol handler app.
                </p>
                <div className="vlc-script-download">
                  <button
                    className="modal-btn modal-btn-primary"
                    onClick={() => handleDownloadScript('/scripts/vlc-protocol-macos.sh', 'vlc-protocol-macos.sh')}
                  >
                    <span className="material-icons">download</span>
                    Download Shell Script
                  </button>
                </div>
                <div className="vlc-instructions">
                  <strong>Instructions:</strong>
                  <ol>
                    <li>Download the script above</li>
                    <li>Open Terminal (Applications &gt; Utilities &gt; Terminal)</li>
                    <li>Navigate to Downloads: <code>cd ~/Downloads</code></li>
                    <li>Make it executable: <code>chmod +x vlc-protocol-macos.sh</code></li>
                    <li>Run: <code>./vlc-protocol-macos.sh</code></li>
                    <li>If permission denied, run with sudo: <code>sudo ./vlc-protocol-macos.sh</code></li>
                  </ol>
                </div>
              </div>

              <div className="modal-section">
                <h3 className="modal-section-title">
                  <span className="material-icons">download</span>
                  Install VLC First
                </h3>
                <p className="modal-info-text">
                  VLC must be installed in /Applications before running the script:
                </p>
                <div className="vlc-external-link">
                  <a href="https://www.videolan.org/vlc/download-macosx.html" target="_blank" rel="noopener noreferrer">
                    <span className="material-icons">open_in_new</span>
                    https://www.videolan.org/vlc/
                  </a>
                </div>
              </div>
            </div>
          )}

          {/* Browser Tab Content */}
          {activeTab === 'browser' && (
            <div className="vlc-tab-content">
              <div className="modal-section">
                <h3 className="modal-section-title">Browser Extensions</h3>
                <p className="modal-info-text">
                  Some browsers require extensions to handle custom protocols. After setting up the OS-level handler, you may also want to install these extensions:
                </p>

                <div className="modal-info-card">
                  <strong>Chrome / Edge:</strong>
                  <p>
                    Install the "Open in VLC media player" extension from the Chrome Web Store.
                    This helps pass vlc:// URLs to the system handler.
                  </p>
                </div>

                <div className="modal-info-card">
                  <strong>Firefox:</strong>
                  <p>
                    Firefox usually prompts to set up protocol handlers automatically.
                    You can also install the "Open in VLC" add-on from Firefox Add-ons.
                  </p>
                </div>

                <div className="modal-info-card">
                  <strong>Safari:</strong>
                  <p>
                    Safari uses the system protocol handlers directly. No extension needed.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Alternative Section */}
          <div className="modal-section vlc-alternative-section">
            <h3 className="modal-section-title">
              <span className="material-icons">file_download</span>
              Alternative: Download M3U File
            </h3>
            <p className="modal-info-text">
              If the protocol handler doesn't work, you can download an M3U playlist file.
              Most systems will automatically open M3U files with VLC if it's installed.
            </p>
          </div>
        </div>

        <div className="modal-footer">
          <button
            type="button"
            className="modal-btn modal-btn-secondary"
            onClick={onClose}
          >
            Close
          </button>
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
    </ModalOverlay>
  );
});

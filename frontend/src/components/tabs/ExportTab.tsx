import { useState } from 'react';
import { PlaylistProfileList } from '../export/PlaylistProfileList';
import { CloudTargetList } from '../export/CloudTargetList';
import { PublishConfigList } from '../export/PublishConfigList';
import { PublishHistoryPanel } from '../export/PublishHistoryPanel';
import './ExportTab.css';

type ExportSection = 'profiles' | 'cloud' | 'publishing' | 'history';

const SECTIONS: { id: ExportSection; label: string; icon: string }[] = [
  { id: 'profiles', label: 'Export Profiles', icon: 'playlist_play' },
  { id: 'cloud', label: 'Cloud Targets', icon: 'cloud_upload' },
  { id: 'publishing', label: 'Publishing', icon: 'publish' },
  { id: 'history', label: 'History', icon: 'history' },
];

export function ExportTab() {
  const [activeSection, setActiveSection] = useState<ExportSection>('profiles');

  return (
    <div className="export-tab">
      <div className="export-tab-nav">
        {SECTIONS.map(s => (
          <button
            key={s.id}
            className={`export-tab-nav-item ${activeSection === s.id ? 'active' : ''}`}
            onClick={() => setActiveSection(s.id)}
          >
            <span className="material-icons">{s.icon}</span>
            <span>{s.label}</span>
          </button>
        ))}
      </div>
      <div className="export-tab-content">
        {activeSection === 'profiles' && <PlaylistProfileList />}
        {activeSection === 'cloud' && <CloudTargetList />}
        {activeSection === 'publishing' && <PublishConfigList />}
        {activeSection === 'history' && <PublishHistoryPanel />}
      </div>
    </div>
  );
}

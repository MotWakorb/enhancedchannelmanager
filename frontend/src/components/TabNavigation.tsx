import './TabNavigation.css';

export type TabId = 'm3u-manager' | 'epg-manager' | 'channel-manager' | 'guide' | 'logo-manager' | 'settings';

interface Tab {
  id: TabId;
  label: string;
  icon: string;
}

interface TabNavigationProps {
  activeTab: TabId;
  onTabChange: (tab: TabId) => void;
  disabled?: boolean;
  editModeActive?: boolean;
}

const TABS: Tab[] = [
  { id: 'm3u-manager', label: 'M3U Manager', icon: 'playlist_play' },
  { id: 'epg-manager', label: 'EPG Manager', icon: 'schedule' },
  { id: 'channel-manager', label: 'Channel Manager', icon: 'tv' },
  { id: 'guide', label: 'Guide', icon: 'grid_on' },
  { id: 'logo-manager', label: 'Logo Manager', icon: 'image' },
  { id: 'settings', label: 'Settings', icon: 'settings' },
];

export function TabNavigation({ activeTab, onTabChange, disabled, editModeActive }: TabNavigationProps) {
  return (
    <nav className={`tab-navigation ${editModeActive ? 'edit-mode-active' : ''}`}>
      {TABS.map((tab) => (
        <button
          key={tab.id}
          className={`tab-button ${activeTab === tab.id ? 'active' : ''}`}
          onClick={() => onTabChange(tab.id)}
          disabled={disabled}
        >
          <span className="material-icons">{tab.icon}</span>
          <span className="tab-label">{tab.label}</span>
        </button>
      ))}
    </nav>
  );
}

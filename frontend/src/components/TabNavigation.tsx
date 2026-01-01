import './TabNavigation.css';

export type TabId = 'channel-manager' | 'epg-manager' | 'logo-manager' | 'settings';

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
  { id: 'channel-manager', label: 'Channel Manager', icon: 'tv' },
  { id: 'epg-manager', label: 'EPG Manager', icon: 'schedule' },
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

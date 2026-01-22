import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import type { Channel, Logo, EPGProgram, EPGData, EPGSource, StreamProfile, ChannelProfile, ChannelGroup } from '../../types';
import * as api from '../../services/api';
import { EditChannelModal, type ChannelMetadataChanges } from '../EditChannelModal';
import { PrintGuideModal } from '../PrintGuideModal';
import './GuideTab.css';

// Constants for grid layout
const SLOT_WIDTH_PX = 200; // Width of each 30-minute slot
const SLOT_MINUTES = 30;
const HOURS_TO_SHOW = 6; // Total hours to display in grid
const ROW_HEIGHT = 60; // Height of each channel row in pixels
const OVERSCAN_COUNT = 5; // Number of extra rows to render above/below viewport

// Helper to get local date string in YYYY-MM-DD format
const getLocalDateString = (date: Date): string => {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
};

// Helper to get program start time (handles both start_time and start field names)
const getProgramStart = (program: EPGProgram): Date => {
  return new Date(program.start_time || program.start || '');
};

// Helper to get program end time (handles both end_time and stop field names)
const getProgramEnd = (program: EPGProgram): Date => {
  return new Date(program.end_time || program.stop || '');
};

interface GuideTabProps {
  // Optional: pass existing data from parent to avoid re-fetching
  channels?: Channel[];
  logos?: Logo[];
  // EPG data for edit modal
  epgData?: EPGData[];
  epgSources?: EPGSource[];
  streamProfiles?: StreamProfile[];
  epgDataLoading?: boolean;
  // Callbacks for edit modal
  onChannelUpdate?: (channel: Channel, changes: ChannelMetadataChanges) => Promise<void>;
  onLogoCreate?: (url: string) => Promise<Logo>;
  onLogoUpload?: (file: File) => Promise<Logo>;
  onLogosChange?: () => Promise<void>;
}

export function GuideTab({
  channels: propChannels,
  logos: propLogos,
  epgData = [],
  epgSources = [],
  streamProfiles = [],
  epgDataLoading = false,
  onChannelUpdate,
  onLogoCreate,
  onLogoUpload,
  onLogosChange,
}: GuideTabProps) {
  // Data state
  const [channels, setChannels] = useState<Channel[]>(propChannels ?? []);
  const [logos, setLogos] = useState<Logo[]>(propLogos ?? []);
  const [programs, setPrograms] = useState<EPGProgram[]>([]);
  const [channelProfiles, setChannelProfiles] = useState<ChannelProfile[]>([]);
  const [channelGroups, setChannelGroups] = useState<ChannelGroup[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState<number | null>(null);

  // Edit channel modal state
  const [channelToEdit, setChannelToEdit] = useState<Channel | null>(null);
  const [showEditModal, setShowEditModal] = useState(false);

  // Print modal state
  const [showPrintModal, setShowPrintModal] = useState(false);

  // Loading state
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Time selection state
  const [selectedDate, setSelectedDate] = useState(() => getLocalDateString(new Date()));
  const [startHour, setStartHour] = useState(() => {
    const now = new Date();
    return now.getHours();
  });

  // Current time state for now-playing highlights (updates every minute)
  const [currentTime, setCurrentTime] = useState(() => new Date());

  // Group filter state
  const [selectedGroup, setSelectedGroup] = useState<string>('');
  const [groupFilterMode, setGroupFilterMode] = useState<'filter' | 'jump'>('filter');

  // Virtualization state
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(600);

  // Refs for synchronized scrolling
  const gridContentRef = useRef<HTMLDivElement>(null);
  const timelineHeaderRef = useRef<HTMLDivElement>(null);

  // Build logo map for quick lookup
  const logoMap = useMemo(() => {
    const map = new Map<number, Logo>();
    logos.forEach(logo => map.set(logo.id, logo));
    return map;
  }, [logos]);

  // Build EPG data map for looking up tvg_id by epg_data_id
  // This allows us to match programs even when channel.tvg_id differs from the EPG's tvg_id
  const epgDataById = useMemo(() => {
    const map = new Map<number, EPGData>();
    epgData.forEach(epg => map.set(epg.id, epg));
    return map;
  }, [epgData]);

  // Build programs map by tvg_id for quick lookup
  const programsByTvgId = useMemo(() => {
    const map = new Map<string, EPGProgram[]>();
    programs.forEach(program => {
      if (program.tvg_id) {
        const existing = map.get(program.tvg_id) || [];
        existing.push(program);
        map.set(program.tvg_id, existing);
      }
    });
    // Sort programs by start time within each tvg_id
    map.forEach((progs) => {
      progs.sort((a, b) => getProgramStart(a).getTime() - getProgramStart(b).getTime());
    });
    return map;
  }, [programs]);


  // Get selected profile for filtering
  const selectedProfile = useMemo(() => {
    if (selectedProfileId === null) return null;
    return channelProfiles.find(p => p.id === selectedProfileId) ?? null;
  }, [selectedProfileId, channelProfiles]);

  // Get sorted list of groups that have channels, sorted by first channel number in each group
  const sortedGroups = useMemo(() => {
    // Build a map of group ID to lowest channel number in that group
    const groupFirstChannel = new Map<number, number>();
    channels.forEach(ch => {
      if (ch.channel_number !== null && ch.channel_group_id !== null) {
        const existing = groupFirstChannel.get(ch.channel_group_id);
        if (existing === undefined || ch.channel_number < existing) {
          groupFirstChannel.set(ch.channel_group_id, ch.channel_number);
        }
      }
    });
    // Get the group objects and sort by first channel number
    return channelGroups
      .filter(g => groupFirstChannel.has(g.id))
      .sort((a, b) => {
        const aFirst = groupFirstChannel.get(a.id) ?? Infinity;
        const bFirst = groupFirstChannel.get(b.id) ?? Infinity;
        return aFirst - bFirst;
      });
  }, [channels, channelGroups]);

  // Sort channels by channel number, optionally filtered by profile and group
  const sortedChannels = useMemo(() => {
    let filtered = [...channels].filter(ch => ch.channel_number !== null);

    // If a profile is selected, only show channels in that profile
    if (selectedProfile) {
      const profileChannelIds = new Set(selectedProfile.channels);
      filtered = filtered.filter(ch => profileChannelIds.has(ch.id));
    }

    // If a group is selected and we're in filter mode, filter by group
    if (selectedGroup && groupFilterMode === 'filter') {
      const groupId = parseInt(selectedGroup, 10);
      filtered = filtered.filter(ch => ch.channel_group_id === groupId);
    }

    return filtered.sort((a, b) => (a.channel_number ?? 0) - (b.channel_number ?? 0));
  }, [channels, selectedProfile, selectedGroup, groupFilterMode]);


  // Calculate time range for the grid
  const timeRange = useMemo(() => {
    const date = new Date(selectedDate + 'T00:00:00');
    const start = new Date(date);
    start.setHours(startHour, 0, 0, 0);
    const end = new Date(start);
    end.setHours(end.getHours() + HOURS_TO_SHOW);
    return { start, end };
  }, [selectedDate, startHour]);

  // Generate time slots for the header
  const timeSlots = useMemo(() => {
    const slots: Date[] = [];
    const current = new Date(timeRange.start);
    while (current < timeRange.end) {
      slots.push(new Date(current));
      current.setMinutes(current.getMinutes() + SLOT_MINUTES);
    }
    return slots;
  }, [timeRange]);

  // Virtualization: calculate which rows to render
  const virtualizedRows = useMemo(() => {
    const totalRows = sortedChannels.length;
    const startIndex = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN_COUNT);
    const visibleCount = Math.ceil(viewportHeight / ROW_HEIGHT) + 2 * OVERSCAN_COUNT;
    const endIndex = Math.min(totalRows, startIndex + visibleCount);

    return {
      startIndex,
      endIndex,
      totalHeight: totalRows * ROW_HEIGHT,
      offsetY: startIndex * ROW_HEIGHT,
      visibleChannels: sortedChannels.slice(startIndex, endIndex),
    };
  }, [sortedChannels, scrollTop, viewportHeight]);

  // Load data on mount
  useEffect(() => {
    const loadData = async () => {
      setLoading(true);
      setError(null);

      try {
        // Fetch all needed data in parallel
        const [channelsData, logosData, programsData, profilesData, groupsData] = await Promise.all([
          propChannels ? Promise.resolve({ results: propChannels }) : api.getChannels({ pageSize: 5000 }),
          propLogos ? Promise.resolve({ results: propLogos }) : api.getLogos({ pageSize: 10000 }),
          api.getEPGGrid(),
          api.getChannelProfiles(),
          api.getChannelGroups(),
        ]);

        if (!propChannels) setChannels((channelsData as { results: Channel[] }).results);
        if (!propLogos) setLogos((logosData as { results: Logo[] }).results);
        setPrograms(programsData);
        setChannelProfiles(profilesData);
        setChannelGroups(groupsData);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load guide data');
      } finally {
        setLoading(false);
      }
    };

    loadData();
  }, [propChannels, propLogos]);

  // Refresh programs only
  const handleRefresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const programsData = await api.getEPGGrid();
      setPrograms(programsData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to refresh program data');
    } finally {
      setLoading(false);
    }
  }, []);

  // Synchronized scrolling between header and content, plus virtualization scroll tracking
  const handleContentScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    if (timelineHeaderRef.current) {
      timelineHeaderRef.current.scrollLeft = e.currentTarget.scrollLeft;
    }
    // Update scroll position for virtualization
    setScrollTop(e.currentTarget.scrollTop);
  }, []);

  // Handle group selection change
  const handleGroupChange = useCallback((group: string) => {
    setSelectedGroup(group);

    // If in jump mode and a group is selected, scroll to first channel in that group
    if (groupFilterMode === 'jump' && group && gridContentRef.current) {
      const groupId = parseInt(group, 10);
      const sortedWithGroup = [...channels]
        .filter(ch => ch.channel_number !== null)
        .sort((a, b) => (a.channel_number ?? 0) - (b.channel_number ?? 0));

      // Apply profile filter if active
      let filtered = sortedWithGroup;
      if (selectedProfile) {
        const profileChannelIds = new Set(selectedProfile.channels);
        filtered = filtered.filter(ch => profileChannelIds.has(ch.id));
      }

      const firstChannelIndex = filtered.findIndex(ch => ch.channel_group_id === groupId);
      if (firstChannelIndex >= 0) {
        gridContentRef.current.scrollTop = firstChannelIndex * ROW_HEIGHT;
      }
    }
  }, [groupFilterMode, channels, selectedProfile]);

  // Update current time every 5 minutes for now-playing highlights (pauses when tab hidden)
  useEffect(() => {
    const interval = setInterval(() => {
      // Only update if tab is visible to avoid CPU waste
      if (document.visibilityState === 'visible') {
        setCurrentTime(new Date());
      }
    }, 300000); // Update every 5 minutes (was 1 minute)
    return () => clearInterval(interval);
  }, []);

  // Auto-scroll to current time on load
  useEffect(() => {
    if (!loading && gridContentRef.current) {
      const isToday = selectedDate === getLocalDateString(currentTime);

      if (isToday) {
        // Calculate scroll position to center current time
        const minutesSinceStart = (currentTime.getHours() - startHour) * 60 + currentTime.getMinutes();
        const scrollPosition = (minutesSinceStart / SLOT_MINUTES) * SLOT_WIDTH_PX;
        const centerOffset = gridContentRef.current.clientWidth / 2;
        gridContentRef.current.scrollLeft = Math.max(0, scrollPosition - centerOffset);
      }
    }
  }, [loading, selectedDate, startHour, currentTime]);

  // Update viewport height for virtualization when container resizes
  useEffect(() => {
    const updateViewportHeight = () => {
      if (gridContentRef.current) {
        setViewportHeight(gridContentRef.current.clientHeight);
      }
    };

    updateViewportHeight();

    const resizeObserver = new ResizeObserver(updateViewportHeight);
    if (gridContentRef.current) {
      resizeObserver.observe(gridContentRef.current);
    }

    return () => resizeObserver.disconnect();
  }, [loading]);

  // Get programs for a channel within the visible time range
  // Uses epg_data_id -> tvg_id indirection to match programs, similar to Dispatcharr's approach.
  // This allows the guide to display correctly even if channel.tvg_id differs from the EPG's tvg_id.
  // Also supports dummy EPG sources which use channel_uuid for matching.
  const getChannelPrograms = useCallback((channel: Channel): EPGProgram[] => {
    // First, try to get the tvg_id via the epg_data_id (preferred method)
    // This matches how Dispatcharr displays its guide
    let lookupTvgId: string | null = null;

    if (channel.epg_data_id !== null) {
      const epgDataEntry = epgDataById.get(channel.epg_data_id);
      if (epgDataEntry) {
        lookupTvgId = epgDataEntry.tvg_id;
      }
    }

    // Fall back to channel.tvg_id if no epg_data_id mapping found
    if (!lookupTvgId) {
      lookupTvgId = channel.tvg_id;
    }

    // Try to find programs by tvg_id first
    let channelPrograms: EPGProgram[] = [];
    if (lookupTvgId) {
      channelPrograms = programsByTvgId.get(lookupTvgId) || [];
    }

    // If no programs found by tvg_id, try matching by channel UUID
    // Dummy EPG sources set program.tvg_id = channel.uuid, so we look up UUID in programsByTvgId
    if (channelPrograms.length === 0 && channel.uuid) {
      channelPrograms = programsByTvgId.get(channel.uuid) || [];
    }

    if (channelPrograms.length === 0) return [];

    // Filter to programs that overlap with our time range
    return channelPrograms.filter(program => {
      const progStart = getProgramStart(program);
      const progEnd = getProgramEnd(program);
      return progStart < timeRange.end && progEnd > timeRange.start;
    });
  }, [programsByTvgId, epgDataById, timeRange]);

  // Calculate position and width for a program block
  const getProgramStyle = useCallback((program: EPGProgram): React.CSSProperties => {
    const progStart = getProgramStart(program);
    const progEnd = getProgramEnd(program);

    // Clamp to visible time range
    const visibleStart = Math.max(progStart.getTime(), timeRange.start.getTime());
    const visibleEnd = Math.min(progEnd.getTime(), timeRange.end.getTime());

    // Calculate offset from timeline start in minutes
    const offsetMinutes = (visibleStart - timeRange.start.getTime()) / (1000 * 60);
    const durationMinutes = (visibleEnd - visibleStart) / (1000 * 60);

    const left = (offsetMinutes / SLOT_MINUTES) * SLOT_WIDTH_PX;
    const width = Math.max(50, (durationMinutes / SLOT_MINUTES) * SLOT_WIDTH_PX - 4); // -4 for margin

    return { left: `${left}px`, width: `${width}px` };
  }, [timeRange]);

  // Check if a program is currently airing
  const isNowPlaying = useCallback((program: EPGProgram): boolean => {
    const progStart = getProgramStart(program);
    const progEnd = getProgramEnd(program);
    return currentTime >= progStart && currentTime < progEnd;
  }, [currentTime]);

  // Format time for display
  const formatTime = (date: Date): string => {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  // Get available date options (today + previous 2 days + next 5 days)
  const dateOptions = useMemo(() => {
    const options: { value: string; label: string }[] = [];
    const today = new Date();

    for (let i = -2; i <= 5; i++) {
      const date = new Date(today);
      date.setDate(date.getDate() + i);
      const value = getLocalDateString(date);
      const label = i === 0 ? 'Today' : i === -1 ? 'Yesterday' : i === 1 ? 'Tomorrow' : date.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
      options.push({ value, label });
    }

    return options;
  }, []);

  // Get hour options (12-hour format)
  const hourOptions = useMemo(() => {
    return Array.from({ length: 24 }, (_, i) => {
      const hour12 = i === 0 ? 12 : i > 12 ? i - 12 : i;
      const ampm = i < 12 ? 'AM' : 'PM';
      return {
        value: i,
        label: `${hour12}:00 ${ampm}`,
      };
    });
  }, []);

  // Check if edit modal can be shown
  const canEdit = onChannelUpdate && onLogoCreate && onLogoUpload;

  // Render channel row
  const renderChannelRow = (channel: Channel) => {
    const channelPrograms = getChannelPrograms(channel);
    const hasEpg = channelPrograms.length > 0;
    const logo = channel.logo_id ? logoMap.get(channel.logo_id) : null;

    const handleChannelClick = () => {
      if (canEdit) {
        setChannelToEdit(channel);
        setShowEditModal(true);
      }
    };

    return (
      <div key={channel.id} className="guide-row">
        <div
          className={`channel-info ${canEdit ? 'clickable' : ''}`}
          onClick={handleChannelClick}
          title={canEdit ? `Click to edit ${channel.name}` : undefined}
        >
          <span className="channel-number">{channel.channel_number}</span>
          {logo && (
            <img
              src={logo.cache_url || logo.url}
              alt=""
              className="channel-logo"
              onError={(e) => { e.currentTarget.style.display = 'none'; }}
            />
          )}
          <span className="channel-name" title={channel.name}>{channel.name}</span>
        </div>
        <div className="program-timeline">
          {hasEpg ? (
            channelPrograms.map(program => (
              <div
                key={program.id}
                className={`program-block ${isNowPlaying(program) ? 'now-playing' : ''}`}
                style={getProgramStyle(program)}
                title={`${program.title}${program.sub_title ? ` - ${program.sub_title}` : ''}\n${formatTime(getProgramStart(program))} - ${formatTime(getProgramEnd(program))}`}
              >
                <div className="program-title">{program.title}</div>
                {program.sub_title && (
                  <div className="program-subtitle">{program.sub_title}</div>
                )}
              </div>
            ))
          ) : (
            <div className="no-epg-block">
              <span className="no-epg-text">No program data</span>
            </div>
          )}
        </div>
      </div>
    );
  };


  // Calculate total timeline width
  const timelineWidth = timeSlots.length * SLOT_WIDTH_PX;

  // Calculate current time indicator position
  const nowIndicatorPosition = useMemo(() => {
    const isToday = selectedDate === getLocalDateString(currentTime);

    if (!isToday) return null;

    // Check if current time is within the visible range
    if (currentTime < timeRange.start || currentTime >= timeRange.end) return null;

    // Calculate position in pixels from the start of the timeline
    const minutesSinceStart = (currentTime.getTime() - timeRange.start.getTime()) / (1000 * 60);
    const position = (minutesSinceStart / SLOT_MINUTES) * SLOT_WIDTH_PX;

    return position;
  }, [selectedDate, timeRange, currentTime]);

  if (loading && channels.length === 0) {
    return (
      <div className="guide-tab">
        <div className="guide-loading">
          <span className="material-icons spinning">sync</span>
          <p>Loading guide data...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="guide-tab">
      {/* Controls */}
      <div className="guide-controls">
        <div className="control-group">
          <label>Date:</label>
          <select
            value={selectedDate}
            onChange={(e) => setSelectedDate(e.target.value)}
          >
            {dateOptions.map(opt => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>

        <div className="control-group">
          <label>Start:</label>
          <select
            value={startHour}
            onChange={(e) => setStartHour(Number(e.target.value))}
          >
            {hourOptions.map(opt => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>

        {channelProfiles.length > 0 && (
          <div className="control-group">
            <label>Profile:</label>
            <select
              value={selectedProfileId ?? ''}
              onChange={(e) => setSelectedProfileId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">All Channels</option>
              {channelProfiles.map(profile => (
                <option key={profile.id} value={profile.id}>{profile.name}</option>
              ))}
            </select>
          </div>
        )}

        {sortedGroups.length > 0 && (
          <div className="control-group group-filter">
            <label>Group:</label>
            <select
              value={selectedGroup}
              onChange={(e) => handleGroupChange(e.target.value)}
            >
              <option value="">{groupFilterMode === 'filter' ? 'All Groups' : 'Jump to group...'}</option>
              {sortedGroups.map(group => (
                <option key={group.id} value={group.id.toString()}>{group.name}</option>
              ))}
            </select>
            <div className="group-mode-toggle">
              <button
                className={`mode-btn ${groupFilterMode === 'filter' ? 'active' : ''}`}
                onClick={() => {
                  setGroupFilterMode('filter');
                  if (selectedGroup) setSelectedGroup(''); // Reset when switching modes
                }}
                title="Filter: Show only channels in selected group"
              >
                <span className="material-icons">filter_list</span>
              </button>
              <button
                className={`mode-btn ${groupFilterMode === 'jump' ? 'active' : ''}`}
                onClick={() => {
                  setGroupFilterMode('jump');
                  if (selectedGroup) setSelectedGroup(''); // Reset when switching modes
                }}
                title="Jump: Scroll to first channel in selected group"
              >
                <span className="material-icons">arrow_downward</span>
              </button>
            </div>
          </div>
        )}

        <button
          className="refresh-btn"
          onClick={handleRefresh}
          disabled={loading}
          title="Refresh program data"
        >
          <span className={`material-icons ${loading ? 'spinning' : ''}`}>refresh</span>
          Refresh
        </button>

        <button
          className="print-btn"
          onClick={() => setShowPrintModal(true)}
          title="Print channel guide"
        >
          <span className="material-icons">print</span>
          Print Guide
        </button>

        {error && <span className="error-message">{error}</span>}
      </div>

      {/* Guide Grid */}
      <div className="guide-container">
        {/* Timeline Header */}
        <div className="timeline-header-wrapper">
          <div className="timeline-header-spacer" />
          <div className="timeline-header" ref={timelineHeaderRef}>
            <div className="timeline-slots" style={{ width: `${timelineWidth}px` }}>
              {timeSlots.map((slot, idx) => (
                <div key={idx} className="time-slot-header">
                  {formatTime(slot)}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Grid Content */}
        <div
          className="guide-content"
          ref={gridContentRef}
          onScroll={handleContentScroll}
        >
          <div
            className="guide-channels-wrapper"
            style={{ height: `${virtualizedRows.totalHeight}px` }}
          >
            <div
              className="guide-channels"
              style={{ transform: `translateY(${virtualizedRows.offsetY}px)` }}
            >
              {virtualizedRows.visibleChannels.map(renderChannelRow)}
            </div>
            {nowIndicatorPosition !== null && (
              <div
                className="now-indicator"
                style={{ left: `${200 + nowIndicatorPosition}px` }}
              />
            )}
          </div>
        </div>
      </div>

      {/* Channel count footer */}
      <div className="guide-footer">
        <span className="channel-count">
          {sortedChannels.length} channels | {programs.length} programs loaded
        </span>
      </div>

      {/* Edit Channel Modal */}
      {showEditModal && channelToEdit && onChannelUpdate && onLogoCreate && onLogoUpload && (
        <EditChannelModal
          channel={channelToEdit}
          logos={logos}
          epgData={epgData.map(e => ({
            id: e.id,
            tvg_id: e.tvg_id,
            name: e.name,
            icon_url: e.icon_url,
            epg_source: e.epg_source,
          }))}
          epgSources={epgSources.map(s => ({ id: s.id, name: s.name }))}
          streamProfiles={streamProfiles.map(p => ({ id: p.id, name: p.name, is_active: p.is_active }))}
          epgDataLoading={epgDataLoading}
          onClose={() => {
            setShowEditModal(false);
            setChannelToEdit(null);
          }}
          onSave={async (changes) => {
            await onChannelUpdate(channelToEdit, changes);
            // Update local channel state with the changes
            setChannels(prev => prev.map(ch =>
              ch.id === channelToEdit.id
                ? { ...ch, ...changes }
                : ch
            ));
            setShowEditModal(false);
            setChannelToEdit(null);
          }}
          onLogoCreate={async (url) => {
            const logo = await onLogoCreate(url);
            if (onLogosChange) {
              await onLogosChange();
            }
            return logo;
          }}
          onLogoUpload={async (file) => {
            const logo = await onLogoUpload(file);
            if (onLogosChange) {
              await onLogosChange();
            }
            return logo;
          }}
        />
      )}

      {/* Print Guide Modal */}
      <PrintGuideModal
        isOpen={showPrintModal}
        onClose={() => setShowPrintModal(false)}
        channelGroups={channelGroups}
        channels={channels}
        title="TV Channel Guide"
      />
    </div>
  );
}

export default GuideTab;

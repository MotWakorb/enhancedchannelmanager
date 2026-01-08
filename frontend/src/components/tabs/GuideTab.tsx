import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import type { Channel, Logo, EPGProgram, EPGData, EPGSource, StreamProfile } from '../../types';
import * as api from '../../services/api';
import { EditChannelModal, type ChannelMetadataChanges } from '../EditChannelModal';
import './GuideTab.css';

// Constants for grid layout
const SLOT_WIDTH_PX = 200; // Width of each 30-minute slot
const SLOT_MINUTES = 30;
const HOURS_TO_SHOW = 6; // Total hours to display in grid

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

  // Edit channel modal state
  const [channelToEdit, setChannelToEdit] = useState<Channel | null>(null);
  const [showEditModal, setShowEditModal] = useState(false);

  // Loading state
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Time selection state
  const [selectedDate, setSelectedDate] = useState(() => {
    const today = new Date();
    return today.toISOString().split('T')[0]; // YYYY-MM-DD
  });
  const [startHour, setStartHour] = useState(() => {
    const now = new Date();
    return now.getHours();
  });

  // Refs for synchronized scrolling
  const gridContentRef = useRef<HTMLDivElement>(null);
  const timelineHeaderRef = useRef<HTMLDivElement>(null);

  // Build logo map for quick lookup
  const logoMap = useMemo(() => {
    const map = new Map<number, Logo>();
    logos.forEach(logo => map.set(logo.id, logo));
    return map;
  }, [logos]);

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
      progs.sort((a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime());
    });
    return map;
  }, [programs]);

  // Sort channels by channel number
  const sortedChannels = useMemo(() => {
    return [...channels]
      .filter(ch => ch.channel_number !== null)
      .sort((a, b) => (a.channel_number ?? 0) - (b.channel_number ?? 0));
  }, [channels]);


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

  // Load data on mount
  useEffect(() => {
    const loadData = async () => {
      setLoading(true);
      setError(null);

      try {
        // Fetch all needed data in parallel
        const [channelsData, logosData, programsData] = await Promise.all([
          propChannels ? Promise.resolve({ results: propChannels }) : api.getChannels({ pageSize: 5000 }),
          propLogos ? Promise.resolve({ results: propLogos }) : api.getLogos({ pageSize: 10000 }),
          api.getEPGGrid(),
        ]);

        if (!propChannels) setChannels((channelsData as { results: Channel[] }).results);
        if (!propLogos) setLogos((logosData as { results: Logo[] }).results);
        setPrograms(programsData);
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

  // Synchronized scrolling between header and content
  const handleContentScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    if (timelineHeaderRef.current) {
      timelineHeaderRef.current.scrollLeft = e.currentTarget.scrollLeft;
    }
  }, []);

  // Auto-scroll to current time on load
  useEffect(() => {
    if (!loading && gridContentRef.current) {
      const now = new Date();
      const isToday = selectedDate === now.toISOString().split('T')[0];

      if (isToday) {
        // Calculate scroll position to center current time
        const minutesSinceStart = (now.getHours() - startHour) * 60 + now.getMinutes();
        const scrollPosition = (minutesSinceStart / SLOT_MINUTES) * SLOT_WIDTH_PX;
        const centerOffset = gridContentRef.current.clientWidth / 2;
        gridContentRef.current.scrollLeft = Math.max(0, scrollPosition - centerOffset);
      }
    }
  }, [loading, selectedDate, startHour]);

  // Get programs for a channel within the visible time range
  const getChannelPrograms = useCallback((channel: Channel): EPGProgram[] => {
    if (!channel.tvg_id) return [];

    const channelPrograms = programsByTvgId.get(channel.tvg_id) || [];

    // Filter to programs that overlap with our time range
    return channelPrograms.filter(program => {
      const progStart = new Date(program.start_time);
      const progEnd = new Date(program.end_time);
      return progStart < timeRange.end && progEnd > timeRange.start;
    });
  }, [programsByTvgId, timeRange]);

  // Calculate position and width for a program block
  const getProgramStyle = useCallback((program: EPGProgram): React.CSSProperties => {
    const progStart = new Date(program.start_time);
    const progEnd = new Date(program.end_time);

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
    const now = new Date();
    const progStart = new Date(program.start_time);
    const progEnd = new Date(program.end_time);
    return now >= progStart && now < progEnd;
  }, []);

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
      const value = date.toISOString().split('T')[0];
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
                title={`${program.title}${program.sub_title ? ` - ${program.sub_title}` : ''}\n${formatTime(new Date(program.start_time))} - ${formatTime(new Date(program.end_time))}`}
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
    const now = new Date();
    const isToday = selectedDate === now.toISOString().split('T')[0];

    if (!isToday) return null;

    // Check if current time is within the visible range
    if (now < timeRange.start || now >= timeRange.end) return null;

    // Calculate position in pixels from the start of the timeline
    const minutesSinceStart = (now.getTime() - timeRange.start.getTime()) / (1000 * 60);
    const position = (minutesSinceStart / SLOT_MINUTES) * SLOT_WIDTH_PX;

    return position;
  }, [selectedDate, timeRange]);

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

        <button
          className="refresh-btn"
          onClick={handleRefresh}
          disabled={loading}
          title="Refresh program data"
        >
          <span className={`material-icons ${loading ? 'spinning' : ''}`}>refresh</span>
          Refresh
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
          <div className="guide-channels-wrapper">
            <div className="guide-channels">
              {sortedChannels.map(renderChannelRow)}
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
    </div>
  );
}

export default GuideTab;

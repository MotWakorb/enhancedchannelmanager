import { useState, useMemo } from 'react';
import type { StreamOptionsState } from './FFMPEGBuilderTab';

interface StreamOptionsPanelProps {
  value: StreamOptionsState;
  onChange: (opts: StreamOptionsState) => void;
}

export function StreamOptionsPanel({ value, onChange }: StreamOptionsPanelProps) {
  const [expanded, setExpanded] = useState(false);

  const enabledCount = useMemo(() => {
    let count = 0;
    if (value.networkResilience) count++;
    if (value.streamAnalysis) count++;
    if (value.errorHandling) count++;
    if (value.bufferSize && value.bufferSize !== '0') count++;
    if (value.streamMapping) count++;
    return count;
  }, [value]);

  const update = (partial: Partial<StreamOptionsState>) => {
    onChange({ ...value, ...partial });
  };

  return (
    <div data-testid="stream-options-panel" className="wizard-step stream-options-panel">
      <button
        type="button"
        className="stream-options-toggle"
        data-testid="stream-options-toggle"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        <div className="stream-options-toggle-left">
          <span className="material-icons stream-options-icon">settings_ethernet</span>
          <h3>Stream Options</h3>
          <span className="stream-options-badge" data-testid="stream-options-badge">
            {enabledCount} of 5 enabled
          </span>
        </div>
        <span className={`material-icons stream-options-chevron${expanded ? ' expanded' : ''}`}>
          expand_more
        </span>
      </button>

      {expanded && (
        <div className="stream-options-body" data-testid="stream-options-body">
          {/* Network Resilience */}
          <div className="stream-option-row">
            <label className="stream-option-label">
              <input
                type="checkbox"
                data-testid="stream-opt-network-resilience"
                checked={value.networkResilience}
                onChange={(e) => update({ networkResilience: e.target.checked })}
              />
              <span className="stream-option-text">
                <strong>Network Resilience</strong>
                <span className="stream-option-hint">
                  If the stream drops or the server hiccups, FFmpeg will automatically
                  try to reconnect instead of just dying. Essential for live IPTV where
                  brief interruptions are normal.
                </span>
              </span>
            </label>
            {value.networkResilience && (
              <div className="stream-option-detail">
                <label>
                  Max reconnect delay
                  <input
                    type="number"
                    data-testid="stream-opt-reconnect-delay"
                    className="stream-option-input"
                    value={value.reconnectDelayMax}
                    min="1"
                    max="60"
                    onChange={(e) => update({ reconnectDelayMax: e.target.value })}
                  />
                  <span className="input-recommend">
                    Seconds between retries. FFmpeg starts at 1s and backs off up to this
                    limit. 5-10s is ideal for most streams. Higher values (30-60s) are
                    better for unreliable servers.
                  </span>
                </label>
              </div>
            )}
          </div>

          {/* Stream Analysis */}
          <div className="stream-option-row">
            <label className="stream-option-label">
              <input
                type="checkbox"
                data-testid="stream-opt-stream-analysis"
                checked={value.streamAnalysis}
                onChange={(e) => update({ streamAnalysis: e.target.checked })}
              />
              <span className="stream-option-text">
                <strong>Stream Analysis</strong>
                <span className="stream-option-hint">
                  FFmpeg needs to &ldquo;look ahead&rdquo; into the stream to figure out what codecs,
                  resolutions, and audio tracks are inside. With IPTV streams, the default
                  look-ahead is often too small and FFmpeg may miss audio tracks or pick
                  the wrong codec. This gives it more time and data to get it right.
                </span>
              </span>
            </label>
            {value.streamAnalysis && (
              <div className="stream-option-detail">
                <label>
                  Analyze duration
                  <input
                    type="number"
                    data-testid="stream-opt-analyzeduration"
                    className="stream-option-input"
                    value={value.analyzeduration}
                    min="1000000"
                    step="1000000"
                    onChange={(e) => update({ analyzeduration: e.target.value })}
                  />
                  <span className="input-recommend">
                    Microseconds. 5,000,000 = 5 seconds of analysis. Increase to 10,000,000
                    if audio is missing or tracks aren't detected. Lower to 2,000,000 for
                    faster startup with reliable sources.
                  </span>
                </label>
                <label>
                  Probe size
                  <input
                    type="number"
                    data-testid="stream-opt-probesize"
                    className="stream-option-input"
                    value={value.probesize}
                    min="1000000"
                    step="1000000"
                    onChange={(e) => update({ probesize: e.target.value })}
                  />
                  <span className="input-recommend">
                    Bytes of data to examine. 5,000,000 = ~5 MB. Increase to 10,000,000
                    if streams have unusual formatting. Usually keep this matched with
                    analyze duration.
                  </span>
                </label>
              </div>
            )}
          </div>

          {/* Error Handling */}
          <div className="stream-option-row">
            <label className="stream-option-label">
              <input
                type="checkbox"
                data-testid="stream-opt-error-handling"
                checked={value.errorHandling}
                onChange={(e) => update({ errorHandling: e.target.checked })}
              />
              <span className="stream-option-text">
                <strong>Error Handling</strong>
                <span className="stream-option-hint">
                  IPTV streams often have minor glitches &mdash; missing timestamps, corrupt
                  packets from brief signal loss. Without this, FFmpeg may freeze, stutter,
                  or stop entirely. This tells it to fix missing timestamps on the fly and
                  skip bad packets instead of choking on them.
                </span>
              </span>
            </label>
          </div>

          {/* Buffer Size */}
          <div className="stream-option-row">
            <label className="stream-option-label">
              <span className="stream-option-text">
                <strong>Buffer Size</strong>
                <span className="stream-option-hint">
                  How many packets FFmpeg queues up from the network before processing.
                  Think of it as a shock absorber &mdash; if the network briefly stalls, the
                  buffer keeps playback smooth. Too small and you get stuttering; too large
                  and you waste memory and add latency.
                </span>
              </span>
            </label>
            <div className="stream-option-detail">
              <label>
                Packets
                <input
                  type="number"
                  data-testid="stream-opt-buffer-size"
                  className="stream-option-input"
                  value={value.bufferSize}
                  min="64"
                  max="8192"
                  step="64"
                  onChange={(e) => update({ bufferSize: e.target.value })}
                />
                <span className="input-recommend">
                  512 is a good default for most IPTV streams. Use 1024-2048 for
                  unreliable or high-bitrate sources. Values above 4096 use significant
                  memory and add noticeable delay.
                </span>
              </label>
            </div>
          </div>

          {/* Stream Mapping */}
          <div className="stream-option-row">
            <label className="stream-option-label">
              <input
                type="checkbox"
                data-testid="stream-opt-stream-mapping"
                checked={value.streamMapping}
                onChange={(e) => update({ streamMapping: e.target.checked })}
              />
              <span className="stream-option-text">
                <strong>Stream Mapping</strong>
                <span className="stream-option-hint">
                  IPTV sources often contain multiple video angles, audio languages,
                  subtitles, and data tracks. Without explicit mapping, FFmpeg guesses
                  which ones to use &mdash; and sometimes picks wrong (like a secondary audio
                  track or a low-res video). This locks it to the first video and first
                  audio track, which is the primary content in virtually all IPTV streams.
                </span>
              </span>
            </label>
          </div>
        </div>
      )}
    </div>
  );
}

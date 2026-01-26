import { useState, useEffect } from 'react';
import type { TaskSchedule, TaskScheduleType, TaskScheduleCreate, TaskScheduleUpdate, TaskParameterSchema } from '../services/api';
import { CustomSelect } from './CustomSelect';
import './ModalBase.css';
import './ScheduleEditor.css';

// Option with optional badge for display
interface ParameterOption {
  value: string | number;
  label: string;
  badge?: string;  // Optional badge text (e.g., "auto")
}

interface ScheduleEditorProps {
  schedule?: TaskSchedule;  // For editing existing schedule
  onSave: (data: TaskScheduleCreate | TaskScheduleUpdate) => Promise<void>;
  onCancel: () => void;
  saving?: boolean;
  taskId?: string;  // For fetching parameter options
  parameterSchema?: TaskParameterSchema[];  // Schema defining what parameters this task accepts
  parameterOptions?: Record<string, ParameterOption[]>;  // Pre-fetched options for array parameters
  defaultParameters?: Record<string, unknown>;  // Default parameter values for new schedules
}

// Common interval presets in seconds
const INTERVAL_PRESETS = [
  { label: '5 min', value: 300 },
  { label: '15 min', value: 900 },
  { label: '30 min', value: 1800 },
  { label: '1 hr', value: 3600 },
  { label: '2 hr', value: 7200 },
  { label: '6 hr', value: 21600 },
  { label: '12 hr', value: 43200 },
  { label: '24 hr', value: 86400 },
];

// Day names
const DAYS_OF_WEEK = [
  { value: 0, label: 'Sun', fullLabel: 'Sunday' },
  { value: 1, label: 'Mon', fullLabel: 'Monday' },
  { value: 2, label: 'Tue', fullLabel: 'Tuesday' },
  { value: 3, label: 'Wed', fullLabel: 'Wednesday' },
  { value: 4, label: 'Thu', fullLabel: 'Thursday' },
  { value: 5, label: 'Fri', fullLabel: 'Friday' },
  { value: 6, label: 'Sat', fullLabel: 'Saturday' },
];

// Common timezone options
const TIMEZONE_OPTIONS = [
  { value: 'UTC', label: 'UTC' },
  { value: 'America/New_York', label: 'Eastern (US)' },
  { value: 'America/Chicago', label: 'Central (US)' },
  { value: 'America/Denver', label: 'Mountain (US)' },
  { value: 'America/Los_Angeles', label: 'Pacific (US)' },
  { value: 'Europe/London', label: 'London' },
  { value: 'Europe/Paris', label: 'Paris' },
  { value: 'Asia/Tokyo', label: 'Tokyo' },
  { value: 'Australia/Sydney', label: 'Sydney' },
];

export function ScheduleEditor({ schedule, onSave, onCancel, saving, taskId, parameterSchema, parameterOptions, defaultParameters }: ScheduleEditorProps) {
  // Form state
  const [name, setName] = useState(schedule?.name || '');
  const [enabled, setEnabled] = useState(schedule?.enabled ?? true);
  const [scheduleType, setScheduleType] = useState<TaskScheduleType>(schedule?.schedule_type || 'daily');
  const [intervalSeconds, setIntervalSeconds] = useState(schedule?.interval_seconds || 3600);
  const [scheduleTime, setScheduleTime] = useState(schedule?.schedule_time || '03:00');
  const [timezone, setTimezone] = useState(schedule?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [daysOfWeek, setDaysOfWeek] = useState<number[]>(schedule?.days_of_week || [1, 2, 3, 4, 5]); // Default: weekdays
  const [dayOfMonth, setDayOfMonth] = useState(schedule?.day_of_month || 1);
  const [useCustomInterval, setUseCustomInterval] = useState(false);

  // Task-specific parameters state
  const [parameters, setParameters] = useState<Record<string, unknown>>(() => {
    // Initialize from schedule parameters (editing)
    if (schedule?.parameters) return schedule.parameters;
    // Use provided defaults for new schedules
    if (defaultParameters && Object.keys(defaultParameters).length > 0) return defaultParameters;
    // Fall back to schema defaults
    if (!parameterSchema) return {};
    return parameterSchema.reduce((acc, param) => {
      if (param.default !== undefined) acc[param.name] = param.default;
      return acc;
    }, {} as Record<string, unknown>);
  });

  // Check if browser timezone is in our list
  const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const tzInList = TIMEZONE_OPTIONS.some(tz => tz.value === browserTz);

  // Check if current interval matches a preset
  useEffect(() => {
    const isPreset = INTERVAL_PRESETS.some(p => p.value === intervalSeconds);
    setUseCustomInterval(!isPreset);
  }, []);

  // Helper to update a single parameter
  const updateParameter = (name: string, value: unknown) => {
    setParameters(prev => ({ ...prev, [name]: value }));
  };

  // Helper to toggle a value in an array parameter
  const toggleArrayParameter = (name: string, value: string | number) => {
    setParameters(prev => {
      const arr = (prev[name] as (string | number)[]) || [];
      const newArr = arr.includes(value)
        ? arr.filter(v => v !== value)
        : [...arr, value];
      return { ...prev, [name]: newArr };
    });
  };

  const handleSave = async () => {
    const data: TaskScheduleCreate | TaskScheduleUpdate = {
      name: name || null,
      enabled,
      schedule_type: scheduleType,
    };

    // Add type-specific fields
    if (scheduleType === 'interval') {
      data.interval_seconds = intervalSeconds;
    } else {
      data.schedule_time = scheduleTime;
      data.timezone = timezone;

      if (scheduleType === 'weekly' || scheduleType === 'biweekly') {
        data.days_of_week = daysOfWeek;
      }

      if (scheduleType === 'monthly') {
        data.day_of_month = dayOfMonth;
      }
    }

    // Include task-specific parameters if we have a schema
    if (parameterSchema && parameterSchema.length > 0) {
      data.parameters = parameters;
    }

    await onSave(data);
  };

  const handleIntervalPreset = (value: number) => {
    setIntervalSeconds(value);
    setUseCustomInterval(false);
  };

  const toggleDay = (day: number) => {
    if (daysOfWeek.includes(day)) {
      setDaysOfWeek(daysOfWeek.filter(d => d !== day));
    } else {
      setDaysOfWeek([...daysOfWeek, day].sort());
    }
  };

  const selectWeekdays = () => setDaysOfWeek([1, 2, 3, 4, 5]);
  const selectWeekends = () => setDaysOfWeek([0, 6]);
  const selectAllDays = () => setDaysOfWeek([0, 1, 2, 3, 4, 5, 6]);

  const customIntervalHours = Math.floor(intervalSeconds / 3600);
  const customIntervalMinutes = Math.floor((intervalSeconds % 3600) / 60);

  return (
    <>
      {/* Modal Body - scrollable content */}
      <div className="modal-body schedule-editor">
        {/* Schedule Name */}
      <div className="form-group">
        <label>Schedule Name (optional)</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g., 'Daily morning run'"
          className="form-input"
        />
      </div>

      {/* Enable/Disable */}
      <div className="form-group checkbox-group">
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          <span>Enable this schedule</span>
        </label>
      </div>

      {/* Schedule Type Selector */}
      <div className="form-group">
        <label>Schedule Type</label>
        <div className="type-selector">
          {[
            { type: 'interval' as TaskScheduleType, label: 'Interval', icon: 'schedule' },
            { type: 'daily' as TaskScheduleType, label: 'Daily', icon: 'today' },
            { type: 'weekly' as TaskScheduleType, label: 'Weekly', icon: 'date_range' },
            { type: 'biweekly' as TaskScheduleType, label: 'Bi-weekly', icon: 'view_week' },
            { type: 'monthly' as TaskScheduleType, label: 'Monthly', icon: 'calendar_month' },
          ].map(({ type, label, icon }) => (
            <button
              key={type}
              type="button"
              className={`type-button ${scheduleType === type ? 'active' : ''}`}
              onClick={() => setScheduleType(type)}
            >
              <span className="material-icons">{icon}</span>
              <span>{label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Type-specific Configuration */}
      <div className="config-section">
        {/* INTERVAL */}
        {scheduleType === 'interval' && (
          <>
            <label>Run Interval</label>
            <div className="interval-presets">
              {INTERVAL_PRESETS.map((preset) => (
                <button
                  key={preset.value}
                  type="button"
                  className={`preset-button ${!useCustomInterval && intervalSeconds === preset.value ? 'active' : ''}`}
                  onClick={() => handleIntervalPreset(preset.value)}
                >
                  {preset.label}
                </button>
              ))}
            </div>
            <div className="custom-interval">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={useCustomInterval}
                  onChange={(e) => setUseCustomInterval(e.target.checked)}
                />
                <span>Custom:</span>
              </label>
              <input
                type="number"
                min={0}
                max={23}
                value={customIntervalHours}
                onChange={(e) => {
                  setUseCustomInterval(true);
                  setIntervalSeconds(parseInt(e.target.value || '0') * 3600 + customIntervalMinutes * 60);
                }}
                disabled={!useCustomInterval}
                className="number-input"
              />
              <span>hours</span>
              <input
                type="number"
                min={0}
                max={59}
                value={customIntervalMinutes}
                onChange={(e) => {
                  setUseCustomInterval(true);
                  setIntervalSeconds(customIntervalHours * 3600 + parseInt(e.target.value || '0') * 60);
                }}
                disabled={!useCustomInterval}
                className="number-input"
              />
              <span>minutes</span>
            </div>
          </>
        )}

        {/* DAILY */}
        {scheduleType === 'daily' && (
          <>
            <label>Time of Day</label>
            <div className="time-picker-row">
              <input
                type="time"
                value={scheduleTime}
                onChange={(e) => setScheduleTime(e.target.value)}
                className="time-input"
              />
              <CustomSelect
                value={timezone}
                onChange={(val) => setTimezone(val)}
                className="timezone-select"
                options={[
                  ...TIMEZONE_OPTIONS.map((tz) => ({ value: tz.value, label: tz.label })),
                  ...(!tzInList ? [{ value: browserTz, label: `${browserTz} (Local)` }] : []),
                ]}
              />
            </div>
          </>
        )}

        {/* WEEKLY */}
        {scheduleType === 'weekly' && (
          <>
            <label>Days of Week</label>
            <div className="day-quick-select">
              <button type="button" onClick={selectWeekdays} className="quick-button">Weekdays</button>
              <button type="button" onClick={selectWeekends} className="quick-button">Weekends</button>
              <button type="button" onClick={selectAllDays} className="quick-button">Every Day</button>
            </div>
            <div className="days-grid">
              {DAYS_OF_WEEK.map((day) => (
                <button
                  key={day.value}
                  type="button"
                  className={`day-button ${daysOfWeek.includes(day.value) ? 'active' : ''}`}
                  onClick={() => toggleDay(day.value)}
                  title={day.fullLabel}
                >
                  {day.label}
                </button>
              ))}
            </div>
            <label>Time</label>
            <div className="time-picker-row">
              <input
                type="time"
                value={scheduleTime}
                onChange={(e) => setScheduleTime(e.target.value)}
                className="time-input"
              />
              <CustomSelect
                value={timezone}
                onChange={(val) => setTimezone(val)}
                className="timezone-select"
                options={[
                  ...TIMEZONE_OPTIONS.map((tz) => ({ value: tz.value, label: tz.label })),
                  ...(!tzInList ? [{ value: browserTz, label: `${browserTz} (Local)` }] : []),
                ]}
              />
            </div>
          </>
        )}

        {/* BIWEEKLY */}
        {scheduleType === 'biweekly' && (
          <>
            <label>Days of Week (every 2 weeks)</label>
            <div className="day-quick-select">
              <button type="button" onClick={selectWeekdays} className="quick-button">Weekdays</button>
              <button type="button" onClick={selectWeekends} className="quick-button">Weekends</button>
              <button type="button" onClick={selectAllDays} className="quick-button">Every Day</button>
            </div>
            <div className="days-grid">
              {DAYS_OF_WEEK.map((day) => (
                <button
                  key={day.value}
                  type="button"
                  className={`day-button ${daysOfWeek.includes(day.value) ? 'active' : ''}`}
                  onClick={() => toggleDay(day.value)}
                  title={day.fullLabel}
                >
                  {day.label}
                </button>
              ))}
            </div>
            <label>Time</label>
            <div className="time-picker-row">
              <input
                type="time"
                value={scheduleTime}
                onChange={(e) => setScheduleTime(e.target.value)}
                className="time-input"
              />
              <CustomSelect
                value={timezone}
                onChange={(val) => setTimezone(val)}
                className="timezone-select"
                options={[
                  ...TIMEZONE_OPTIONS.map((tz) => ({ value: tz.value, label: tz.label })),
                  ...(!tzInList ? [{ value: browserTz, label: `${browserTz} (Local)` }] : []),
                ]}
              />
            </div>
          </>
        )}

        {/* MONTHLY */}
        {scheduleType === 'monthly' && (
          <>
            <label>Day of Month</label>
            <div className="monthly-day-row">
              <CustomSelect
                value={String(dayOfMonth)}
                onChange={(val) => setDayOfMonth(parseInt(val))}
                className="day-select"
                options={[
                  ...Array.from({ length: 31 }, (_, i) => ({
                    value: String(i + 1),
                    label: String(i + 1),
                  })),
                  { value: '-1', label: 'Last day' },
                ]}
              />
              <span className="day-suffix">of each month</span>
            </div>
            <label>Time</label>
            <div className="time-picker-row">
              <input
                type="time"
                value={scheduleTime}
                onChange={(e) => setScheduleTime(e.target.value)}
                className="time-input"
              />
              <CustomSelect
                value={timezone}
                onChange={(val) => setTimezone(val)}
                className="timezone-select"
                options={[
                  ...TIMEZONE_OPTIONS.map((tz) => ({ value: tz.value, label: tz.label })),
                  ...(!tzInList ? [{ value: browserTz, label: `${browserTz} (Local)` }] : []),
                ]}
              />
            </div>
          </>
        )}
      </div>

      {/* Task-specific Parameters */}
      {parameterSchema && parameterSchema.length > 0 && (
        <div className="parameters-section">
          <h4 className="section-title">Task Parameters</h4>
          {parameterSchema.map((param) => (
            <div key={param.name} className="form-group">
              {/* Show note before batch_size for stream_probe */}
              {taskId === 'stream_probe' && param.name === 'batch_size' && (
                <p className="parameters-note">
                  Batch Size, Timeout, and Max Concurrent are optional overrides for the global settings in Settings → Maintenance.
                </p>
              )}
              <label>{param.label}</label>
              <p className="param-description">{param.description}</p>

              {/* Number input */}
              {param.type === 'number' && (
                <input
                  type="number"
                  value={(parameters[param.name] as number) ?? param.default ?? 0}
                  onChange={(e) => updateParameter(param.name, parseInt(e.target.value) || 0)}
                  min={param.min}
                  max={param.max}
                  className="form-input number-input"
                />
              )}

              {/* String input */}
              {param.type === 'string' && (
                <input
                  type="text"
                  value={(parameters[param.name] as string) ?? param.default ?? ''}
                  onChange={(e) => updateParameter(param.name, e.target.value)}
                  className="form-input"
                />
              )}

              {/* Boolean checkbox */}
              {param.type === 'boolean' && (
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={(parameters[param.name] as boolean) ?? param.default ?? false}
                    onChange={(e) => updateParameter(param.name, e.target.checked)}
                  />
                  <span>Enabled</span>
                </label>
              )}

              {/* String/Number array with options */}
              {(param.type === 'string_array' || param.type === 'number_array') && parameterOptions?.[param.source || param.name] && (
                <div className="array-options">
                  <div className="array-hint">
                    {((parameters[param.name] as (string | number)[]) || []).length === 0
                      ? 'None selected (applies to all)'
                      : `${((parameters[param.name] as (string | number)[]) || []).length} selected`}
                  </div>
                  <div className="option-list">
                    {parameterOptions[param.source || param.name].map((opt) => {
                      const selected = ((parameters[param.name] as (string | number)[]) || []).includes(opt.value);
                      return (
                        <label key={String(opt.value)} className="option-checkbox">
                          <input
                            type="checkbox"
                            checked={selected}
                            onChange={() => toggleArrayParameter(param.name, opt.value)}
                          />
                          <span>{opt.label}</span>
                          {opt.badge && <span className="option-badge">{opt.badge}</span>}
                        </label>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* String/Number array without options - show text hint */}
              {(param.type === 'string_array' || param.type === 'number_array') && !parameterOptions?.[param.source || param.name] && (
                <div className="array-hint">
                  Empty = applies to all. Options not yet loaded.
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      </div>

      {/* Modal Footer - action buttons */}
      <div className="modal-footer">
        <button type="button" onClick={onCancel} className="modal-btn modal-btn-secondary">
          Cancel
        </button>
        <button
          type="button"
          onClick={handleSave}
          disabled={saving || (scheduleType !== 'interval' && !scheduleTime)}
          className="modal-btn modal-btn-primary"
        >
          {saving ? (
            <>
              <span className="material-icons spinning">sync</span>
              Saving...
            </>
          ) : (
            <>
              <span className="material-icons">save</span>
              {schedule ? 'Update Schedule' : 'Add Schedule'}
            </>
          )}
        </button>
      </div>
    </>
  );
}

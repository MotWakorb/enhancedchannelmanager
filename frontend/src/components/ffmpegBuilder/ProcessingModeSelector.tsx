import type { ProcessingMode } from '../../types/ffmpegBuilder';

interface ProcessingModeOption {
  mode: ProcessingMode;
  label: string;
  description: string;
  icon: string;
}

const PROCESSING_MODES: ProcessingModeOption[] = [
  {
    mode: 'copy',
    label: 'Copy (No Re-encoding)',
    description: 'Pass through original streams unchanged — fastest, no quality loss',
    icon: 'content_copy',
  },
  {
    mode: 'software',
    label: 'Software (CPU)',
    description: 'H.264 encoding using your CPU — works everywhere, slower',
    icon: 'memory',
  },
  {
    mode: 'nvidia',
    label: 'NVIDIA',
    description: 'Hardware encoding with NVENC — fast, requires NVIDIA GPU',
    icon: 'developer_board',
  },
  {
    mode: 'amd',
    label: 'AMD Radeon',
    description: 'Hardware encoding with VAAPI — fast, requires AMD GPU',
    icon: 'developer_board',
  },
  {
    mode: 'qsv',
    label: 'Intel QSV',
    description: 'Hardware encoding with Quick Sync — fast, requires Intel iGPU',
    icon: 'developer_board',
  },
  {
    mode: 'vaapi',
    label: 'Intel VAAPI',
    description: 'Hardware encoding with VAAPI — Linux Intel GPU path',
    icon: 'developer_board',
  },
];

interface ProcessingModeSelectorProps {
  value: ProcessingMode;
  onChange: (mode: ProcessingMode) => void;
}

export function ProcessingModeSelector({ value, onChange }: ProcessingModeSelectorProps) {
  return (
    <div data-testid="processing-mode-selector" className="processing-mode-grid">
      {PROCESSING_MODES.map((opt) => (
        <button
          key={opt.mode}
          type="button"
          data-testid={`processing-mode-${opt.mode}`}
          className={`processing-mode-card${value === opt.mode ? ' active' : ''}`}
          aria-pressed={value === opt.mode}
          onClick={() => onChange(opt.mode)}
        >
          <span className="material-icons processing-mode-icon">{opt.icon}</span>
          <span className="processing-mode-label">{opt.label}</span>
          <span className="processing-mode-desc">{opt.description}</span>
        </button>
      ))}
    </div>
  );
}

/**
 * Heatmap visual-regression fixture (bd-lln2x).
 *
 * Mounts the Heatmap chart primitive in isolation with a representative
 * dataset modelled on the GH-59 Stats v2 Providers panel — the surface
 * where bd-wdpve clipped column labels in production. Loaded by Playwright
 * in `e2e/heatmap-visual.spec.ts` to capture a deterministic PNG and
 * compare against the committed baseline.
 *
 * Why this exists:
 *
 *   - jsdom (used by `Heatmap.test.tsx`) cannot detect the bd-yteek →
 *     bd-wdpve class of bug — the regression is a visual one (text
 *     rotated the wrong direction overlaps cell content). The DOM
 *     rotation lock catches the *attribute*; this catches the *render*.
 *
 *   - Mounting through the real app would drag in auth, error
 *     reporting, API calls, and noisy chrome that all shift pixels
 *     between runs. The fixture skips all of it — only the Heatmap
 *     and the theme variables it depends on are loaded.
 *
 * Dataset shape:
 *
 *   - 6 rows including "Unknown" (matches the PO-validated provider
 *     bucket from bd-lhxfu — Unknown is a real provider in real data,
 *     not a special case).
 *   - 24 columns with realistic channel-name lengths, including the
 *     ~145px-wide "917 | Milwaukee Brewers" string from the original
 *     bd-wdpve regression report.
 *   - Cell values span the full data range so the sequential color
 *     ramp exercises both endpoints.
 *
 * Determinism:
 *
 *   - Values are hardcoded — no Math.random(), no new Date(), no API
 *     calls. The same input must always produce the same pixel output.
 *   - cellSize is pinned at 32px so the SVG dimensions are stable
 *     across any environment that loads the page.
 */
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { Heatmap } from '../components/charts/Heatmap';
import '../index.css';

// Channel-name labels modelled on the GH-59 Providers panel. The first
// few names span a range of lengths to exercise both short ("ESPN") and
// long ("917 | Milwaukee Brewers") rotated-label rendering. The
// remaining slots cycle through realistic provider/sport/network names.
const COLUMN_LABELS: readonly string[] = [
  'ESPN HD',
  '917 | Milwaukee Brewers',
  'Discovery Channel',
  'Telemundo Internacional',
  'CNN',
  'Fox Sports 1',
  'TNT',
  'NBC Sports',
  'Bravo East',
  'Cartoon Network',
  'A&E',
  'AMC',
  'Hallmark',
  'Lifetime',
  'Nat Geo Wild',
  'Disney Channel',
  'NickToons',
  'BBC America',
  'IFC',
  'Sundance TV',
  'WeatherNation',
  'C-SPAN',
  'QVC',
  'HSN',
];

const ROW_LABELS: readonly string[] = [
  'Infinity',
  'Lumen',
  'HD Homerun',
  'Strong',
  'Pluto',
  'Unknown',
];

// Hand-tuned values: each row has a distinct activity pattern so the
// cells render with visibly different colors. Top-left and bottom-right
// span 0..max so the sequential ramp exercises both endpoints.
const HEATMAP_DATA: readonly (readonly number[])[] = [
  // Infinity — high baseline, peaks mid-range
  [12, 28, 35, 42, 50, 48, 38, 30, 22, 15, 10, 8, 12, 18, 25, 32, 28, 20, 15, 10, 8, 5, 3, 2],
  // Lumen — bursty, two peaks
  [5, 10, 22, 30, 18, 12, 8, 15, 25, 38, 42, 35, 22, 14, 8, 5, 12, 18, 22, 18, 12, 8, 5, 3],
  // HD Homerun — flat low
  [8, 9, 11, 12, 14, 15, 14, 13, 12, 11, 10, 9, 8, 9, 10, 11, 12, 13, 12, 11, 10, 9, 8, 7],
  // Strong — mostly idle, one big spike
  [0, 0, 0, 1, 2, 1, 0, 0, 0, 1, 2, 5, 12, 25, 18, 8, 3, 1, 0, 0, 0, 0, 0, 0],
  // Pluto — gentle wave
  [3, 5, 8, 12, 15, 18, 22, 25, 28, 30, 32, 30, 28, 25, 22, 18, 15, 12, 10, 8, 6, 5, 4, 3],
  // Unknown — low + intermittent (resolver-gap signal from bd-lhxfu)
  [0, 1, 0, 0, 2, 0, 1, 0, 0, 3, 0, 0, 1, 0, 0, 4, 0, 0, 1, 0, 0, 2, 0, 0],
];

// Visual fixture entry point. Fast refresh / HMR is irrelevant here —
// the file is a one-shot Vite entry mounted by Playwright, not part of
// the live app. Suppressing the only-export-components rule keeps the
// fixture self-contained (component + mount in one file).
// eslint-disable-next-line react-refresh/only-export-components
function HeatmapFixture() {
  return (
    <div
      style={{
        // Fixed viewport-sized container so the screenshot region is
        // stable even if the browser window varies. Background matches
        // the dark theme so the heatmap renders in its real context.
        width: '1280px',
        padding: '24px',
        backgroundColor: 'var(--bg-primary)',
        color: 'var(--text-primary)',
      }}
    >
      <Heatmap
        data={HEATMAP_DATA}
        rowLabels={ROW_LABELS}
        columnLabels={COLUMN_LABELS}
        cellSize={32}
        ariaLabel="Channels by Provider — visual regression fixture"
      />
    </div>
  );
}

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('visual-fixture: #root element not found in HTML');
}

createRoot(rootElement).render(
  <StrictMode>
    <HeatmapFixture />
  </StrictMode>,
);

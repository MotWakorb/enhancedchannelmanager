/**
 * Tests for FFMPEGBuilderTab — IPTV-focused 3-step wizard + Advanced mode.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { renderWithProviders } from '../../test/utils/renderWithProviders';
import userEvent from '@testing-library/user-event';
import {
  server,
  resetMockDataStore,
} from '../../test/mocks/server';
import { FFMPEGBuilderTab } from './FFMPEGBuilderTab';

// Setup MSW server
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  resetMockDataStore();
});
afterAll(() => server.close());

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderBuilderTab() {
  return renderWithProviders(<FFMPEGBuilderTab />);
}

function getSectionOrder(container: HTMLElement, testIds: string[]): string[] {
  const nodes: { testId: string; index: number }[] = [];
  const allElements = container.querySelectorAll('[data-testid]');
  allElements.forEach((el, idx) => {
    const tid = el.getAttribute('data-testid');
    if (tid && testIds.includes(tid)) {
      nodes.push({ testId: tid, index: idx });
    }
  });
  nodes.sort((a, b) => a.index - b.index);
  return nodes.map((n) => n.testId);
}

// ===========================================================================
// Tests
// ===========================================================================

describe('FFMPEGBuilderTab', () => {
  // -------------------------------------------------------------------------
  // Simple Mode (default)
  // -------------------------------------------------------------------------
  describe('simple mode (default)', () => {
    it('renders builder container', () => {
      renderBuilderTab();
      expect(screen.getByTestId('ffmpeg-builder')).toBeInTheDocument();
    });

    it('shows tab title', () => {
      renderBuilderTab();
      expect(
        screen.getByRole('heading', { name: /ffmpeg|builder/i })
      ).toBeInTheDocument();
    });

    it('renders 3 wizard steps', () => {
      renderBuilderTab();
      expect(screen.getByTestId('wizard-step-source')).toBeInTheDocument();
      expect(screen.getByTestId('wizard-step-processing')).toBeInTheDocument();
      expect(screen.getByTestId('wizard-step-output')).toBeInTheDocument();
    });

    it('renders wizard steps in correct order', () => {
      const { container } = renderBuilderTab();
      const order = getSectionOrder(container, [
        'wizard-step-source',
        'wizard-step-processing',
        'wizard-step-output',
        'stream-options-panel',
        'command-preview',
      ]);
      expect(order[0]).toBe('wizard-step-source');
      expect(order[1]).toBe('wizard-step-processing');
      expect(order[2]).toBe('wizard-step-output');
      expect(order[3]).toBe('stream-options-panel');
      expect(order[4]).toBe('command-preview');
    });

    it('renders IPTV preset bar', () => {
      renderBuilderTab();
      expect(screen.getByTestId('iptv-preset-bar')).toBeInTheDocument();
    });

    it('renders processing mode selector', () => {
      renderBuilderTab();
      expect(screen.getByTestId('processing-mode-selector')).toBeInTheDocument();
    });

    it('renders command preview at bottom', () => {
      renderBuilderTab();
      expect(screen.getByTestId('command-preview')).toBeInTheDocument();
    });

    it('renders mode toggle button', () => {
      renderBuilderTab();
      expect(screen.getByTestId('mode-toggle')).toBeInTheDocument();
      expect(screen.getByTestId('mode-toggle')).toHaveTextContent(/advanced/i);
    });

    it('does NOT show advanced sections in simple mode', () => {
      renderBuilderTab();
      expect(screen.queryByTestId('input-section')).not.toBeInTheDocument();
      expect(screen.queryByTestId('output-section')).not.toBeInTheDocument();
      expect(screen.queryByTestId('video-codec-section')).not.toBeInTheDocument();
      expect(screen.queryByTestId('audio-codec-section')).not.toBeInTheDocument();
      expect(screen.queryByTestId('video-filters-section')).not.toBeInTheDocument();
      expect(screen.queryByTestId('audio-filters-section')).not.toBeInTheDocument();
      // preset bar is visible in simple mode (not hidden)
    });
  });

  // -------------------------------------------------------------------------
  // Source URL input
  // -------------------------------------------------------------------------
  describe('source URL input', () => {
    it('has a source URL input field', () => {
      renderBuilderTab();
      expect(screen.getByTestId('source-url-input')).toBeInTheDocument();
    });

    it('default URL is {streamUrl}', () => {
      renderBuilderTab();
      expect(screen.getByTestId('source-url-input')).toHaveValue('{streamUrl}');
    });

    it('typing a URL updates command preview', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      const input = screen.getByTestId('source-url-input');
      await user.clear(input);
      await user.type(input, 'http://example.com/live.ts');

      await waitFor(() => {
        const commandText = screen.getByTestId('command-text');
        expect(commandText.textContent).toContain('http://example.com/live.ts');
      });
    });
  });

  // -------------------------------------------------------------------------
  // Processing mode selection
  // -------------------------------------------------------------------------
  describe('processing mode selection', () => {
    it('copy mode is selected by default', () => {
      renderBuilderTab();
      const copyCard = screen.getByTestId('processing-mode-copy');
      expect(copyCard).toHaveAttribute('aria-pressed', 'true');
    });

    it('clicking Software updates codec to libx264', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      await user.click(screen.getByTestId('processing-mode-software'));

      await waitFor(() => {
        const commandText = screen.getByTestId('command-text');
        expect(commandText.textContent).toContain('libx264');
      });
    });

    it('clicking NVIDIA updates codec to h264_nvenc', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      await user.click(screen.getByTestId('processing-mode-nvidia'));

      await waitFor(() => {
        const commandText = screen.getByTestId('command-text');
        expect(commandText.textContent).toContain('h264_nvenc');
      });
    });

    it('clicking QSV updates codec to h264_qsv', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      await user.click(screen.getByTestId('processing-mode-qsv'));

      await waitFor(() => {
        const commandText = screen.getByTestId('command-text');
        expect(commandText.textContent).toContain('h264_qsv');
      });
    });

    it('clicking Copy sets codec to copy', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      // Switch away then back
      await user.click(screen.getByTestId('processing-mode-software'));
      await user.click(screen.getByTestId('processing-mode-copy'));

      await waitFor(() => {
        const commandText = screen.getByTestId('command-text');
        expect(commandText.textContent).toContain('-c:v copy');
      });
    });
  });

  // -------------------------------------------------------------------------
  // IPTV preset application
  // -------------------------------------------------------------------------
  describe('IPTV presets', () => {
    it('renders built-in preset buttons', () => {
      renderBuilderTab();
      expect(screen.getByTestId('iptv-preset-passthrough')).toBeInTheDocument();
      expect(screen.getByTestId('iptv-preset-iptv-standard')).toBeInTheDocument();
      expect(screen.getByTestId('iptv-preset-iptv-nvidia')).toBeInTheDocument();
      expect(screen.getByTestId('iptv-preset-iptv-qsv')).toBeInTheDocument();
      expect(screen.getByTestId('iptv-preset-hls-output')).toBeInTheDocument();
    });

    it('clicking a preset applies its config', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      await user.click(screen.getByTestId('iptv-preset-iptv-standard'));

      await waitFor(() => {
        const commandText = screen.getByTestId('command-text');
        expect(commandText.textContent).toContain('libx264');
        expect(commandText.textContent).toContain('aac');
      });
    });

    it('clicking a preset marks it active', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      await user.click(screen.getByTestId('iptv-preset-hls-output'));

      expect(screen.getByTestId('iptv-preset-hls-output')).toHaveAttribute('aria-pressed', 'true');
    });

    it('preset preserves user-entered source URL', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      // Type a URL first
      const input = screen.getByTestId('source-url-input');
      await user.clear(input);
      await user.type(input, 'http://my-stream.com/live.ts');

      // Apply a preset
      await user.click(screen.getByTestId('iptv-preset-iptv-nvidia'));

      await waitFor(() => {
        const commandText = screen.getByTestId('command-text');
        expect(commandText.textContent).toContain('http://my-stream.com/live.ts');
        expect(commandText.textContent).toContain('h264_nvenc');
      });
    });
  });

  // -------------------------------------------------------------------------
  // Output format selection (simple mode)
  // -------------------------------------------------------------------------
  describe('simple output format', () => {
    it('renders streaming output format options', () => {
      renderBuilderTab();
      expect(screen.getByTestId('simple-output-ts')).toBeInTheDocument();
      expect(screen.getByTestId('simple-output-hls')).toBeInTheDocument();
    });

    it('TS is selected by default', () => {
      renderBuilderTab();
      expect(screen.getByTestId('simple-output-ts')).toHaveAttribute('aria-pressed', 'true');
    });

    it('clicking HLS changes output format', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      await user.click(screen.getByTestId('simple-output-hls'));

      expect(screen.getByTestId('simple-output-hls')).toHaveAttribute('aria-pressed', 'true');
      expect(screen.getByTestId('simple-output-ts')).toHaveAttribute('aria-pressed', 'false');
    });
  });

  // -------------------------------------------------------------------------
  // Advanced mode
  // -------------------------------------------------------------------------
  describe('advanced mode', () => {
    it('toggling to advanced shows all 8 sections', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      await user.click(screen.getByTestId('mode-toggle'));

      expect(screen.getByTestId('input-section')).toBeInTheDocument();
      expect(screen.getByTestId('output-section')).toBeInTheDocument();
      expect(screen.getByTestId('video-codec-section')).toBeInTheDocument();
      expect(screen.getByTestId('audio-codec-section')).toBeInTheDocument();
      expect(screen.getByTestId('video-filters-section')).toBeInTheDocument();
      expect(screen.getByTestId('audio-filters-section')).toBeInTheDocument();
      expect(screen.getByTestId('command-preview')).toBeInTheDocument();
    });

    it('advanced mode shows preset bar', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      await user.click(screen.getByTestId('mode-toggle'));

      expect(screen.getByTestId('iptv-preset-bar')).toBeInTheDocument();
    });

    it('advanced mode hides wizard steps', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      await user.click(screen.getByTestId('mode-toggle'));

      expect(screen.queryByTestId('wizard-step-source')).not.toBeInTheDocument();
      expect(screen.queryByTestId('wizard-step-processing')).not.toBeInTheDocument();
      expect(screen.queryByTestId('wizard-step-output')).not.toBeInTheDocument();
      // IPTV preset bar is now shown in both modes
      expect(screen.queryByTestId('iptv-preset-bar')).toBeInTheDocument();
    });

    it('sections in correct layout order', async () => {
      const user = userEvent.setup();
      const { container } = renderBuilderTab();

      await user.click(screen.getByTestId('mode-toggle'));

      const order = getSectionOrder(container, [
        'input-section',
        'output-section',
        'video-codec-section',
        'audio-codec-section',
        'video-filters-section',
        'audio-filters-section',
        'command-preview',
      ]);

      expect(order.indexOf('input-section')).toBeLessThan(order.indexOf('output-section'));
      expect(order.indexOf('video-codec-section')).toBeLessThan(order.indexOf('video-filters-section'));
      expect(order.indexOf('audio-codec-section')).toBeLessThan(order.indexOf('audio-filters-section'));
      expect(order[order.length - 1]).toBe('command-preview');
    });

    it('section headings are correct', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      await user.click(screen.getByTestId('mode-toggle'));

      expect(within(screen.getByTestId('input-section')).getByRole('heading', { name: /input/i })).toBeInTheDocument();
      expect(within(screen.getByTestId('output-section')).getByRole('heading', { name: /output/i })).toBeInTheDocument();
      expect(within(screen.getByTestId('video-codec-section')).getByRole('heading', { name: /video.*codec/i })).toBeInTheDocument();
      expect(within(screen.getByTestId('audio-codec-section')).getByRole('heading', { name: /audio.*codec/i })).toBeInTheDocument();
    });

    it('mode toggle button says Simple in advanced mode', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      await user.click(screen.getByTestId('mode-toggle'));

      expect(screen.getByTestId('mode-toggle')).toHaveTextContent(/simple/i);
    });
  });

  // -------------------------------------------------------------------------
  // Mode toggle preserves state
  // -------------------------------------------------------------------------
  describe('mode toggle state preservation', () => {
    it('switching modes preserves source URL', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      // Type a URL in simple mode
      const input = screen.getByTestId('source-url-input');
      await user.clear(input);
      await user.type(input, 'http://test.com/stream.ts');

      // Switch to advanced
      await user.click(screen.getByTestId('mode-toggle'));

      // Command should still contain the URL
      await waitFor(() => {
        const commandText = screen.getByTestId('command-text');
        expect(commandText.textContent).toContain('http://test.com/stream.ts');
      });

      // Switch back to simple
      await user.click(screen.getByTestId('mode-toggle'));

      // URL should still be in the input
      expect(screen.getByTestId('source-url-input')).toHaveValue('http://test.com/stream.ts');
    });

    it('switching modes preserves codec settings', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      // Set to NVIDIA in simple mode
      await user.click(screen.getByTestId('processing-mode-nvidia'));

      // Switch to advanced
      await user.click(screen.getByTestId('mode-toggle'));

      // Command should still contain nvenc
      await waitFor(() => {
        const commandText = screen.getByTestId('command-text');
        expect(commandText.textContent).toContain('h264_nvenc');
      });
    });
  });

  // -------------------------------------------------------------------------
  // Responsive layout
  // -------------------------------------------------------------------------
  describe('responsive layout', () => {
    it('renders at desktop width', () => {
      renderBuilderTab();
      expect(screen.getByTestId('ffmpeg-builder')).toBeInTheDocument();
    });

    it('renders at tablet width', () => {
      Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 768 });
      window.dispatchEvent(new Event('resize'));
      renderBuilderTab();
      expect(screen.getByTestId('ffmpeg-builder')).toBeInTheDocument();
      Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1024 });
    });

    it('sections stack at narrow width', () => {
      Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 480 });
      window.dispatchEvent(new Event('resize'));
      renderBuilderTab();
      expect(screen.getByTestId('wizard-step-source')).toBeInTheDocument();
      expect(screen.getByTestId('wizard-step-processing')).toBeInTheDocument();
      expect(screen.getByTestId('wizard-step-output')).toBeInTheDocument();
      expect(screen.getByTestId('command-preview')).toBeInTheDocument();
      Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1024 });
    });
  });

  // -------------------------------------------------------------------------
  // Error handling
  // -------------------------------------------------------------------------
  describe('error handling', () => {
    it('shows validation warnings in preview', async () => {
      renderBuilderTab();
      await waitFor(() => {
        const commandPreview = screen.getByTestId('command-preview');
        expect(commandPreview).toBeInTheDocument();
      });
      const warningElements = screen.queryAllByTestId('command-warning');
      expect(Array.isArray(warningElements)).toBe(true);
    });
  });

  // -------------------------------------------------------------------------
  // IPTV smart defaults
  // -------------------------------------------------------------------------
  describe('IPTV smart defaults', () => {
    it('default command includes reconnect flags', () => {
      renderBuilderTab();
      const commandText = screen.getByTestId('command-text');
      expect(commandText.textContent).toContain('-reconnect 1');
      expect(commandText.textContent).toContain('-reconnect_streamed 1');
      expect(commandText.textContent).toContain('-reconnect_delay_max 10');
    });

    it('default command includes fflags and err_detect', () => {
      renderBuilderTab();
      const commandText = screen.getByTestId('command-text');
      expect(commandText.textContent).toContain('-fflags +genpts+discardcorrupt');
      expect(commandText.textContent).toContain('-err_detect ignore_err');
    });

    it('default command includes stream mappings', () => {
      renderBuilderTab();
      const commandText = screen.getByTestId('command-text');
      expect(commandText.textContent).toContain('-map 0:v:0');
      expect(commandText.textContent).toContain('-map 0:a:0');
    });

    it('default command includes output format', () => {
      renderBuilderTab();
      const commandText = screen.getByTestId('command-text');
      expect(commandText.textContent).toContain('-f ts');
    });

    it('default command includes analyzeduration and probesize', () => {
      renderBuilderTab();
      const commandText = screen.getByTestId('command-text');
      expect(commandText.textContent).toContain('-analyzeduration 5000000');
      expect(commandText.textContent).toContain('-probesize 5000000');
    });

    it('default command includes thread_queue_size', () => {
      renderBuilderTab();
      const commandText = screen.getByTestId('command-text');
      expect(commandText.textContent).toContain('-thread_queue_size 512');
    });
  });

  // -------------------------------------------------------------------------
  // Stream Options Panel
  // -------------------------------------------------------------------------
  describe('stream options panel', () => {
    it('renders stream options panel in simple mode', () => {
      renderBuilderTab();
      expect(screen.getByTestId('stream-options-panel')).toBeInTheDocument();
    });

    it('panel is collapsed by default', () => {
      renderBuilderTab();
      expect(screen.queryByTestId('stream-options-body')).not.toBeInTheDocument();
    });

    it('shows badge with enabled count', () => {
      renderBuilderTab();
      const badge = screen.getByTestId('stream-options-badge');
      expect(badge.textContent).toContain('5 of 5 enabled');
    });

    it('expands when toggle is clicked', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      await user.click(screen.getByTestId('stream-options-toggle'));

      expect(screen.getByTestId('stream-options-body')).toBeInTheDocument();
      expect(screen.getByTestId('stream-opt-network-resilience')).toBeInTheDocument();
      expect(screen.getByTestId('stream-opt-stream-analysis')).toBeInTheDocument();
      expect(screen.getByTestId('stream-opt-error-handling')).toBeInTheDocument();
      expect(screen.getByTestId('stream-opt-buffer-size')).toBeInTheDocument();
      expect(screen.getByTestId('stream-opt-stream-mapping')).toBeInTheDocument();
    });

    it('toggling off network resilience removes reconnect flags from command', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      // Expand panel
      await user.click(screen.getByTestId('stream-options-toggle'));

      // Uncheck network resilience
      await user.click(screen.getByTestId('stream-opt-network-resilience'));

      await waitFor(() => {
        const commandText = screen.getByTestId('command-text');
        expect(commandText.textContent).not.toContain('-reconnect 1');
        expect(commandText.textContent).not.toContain('-reconnect_streamed 1');
      });
    });

    it('toggling off error handling removes fflags from command', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      await user.click(screen.getByTestId('stream-options-toggle'));
      await user.click(screen.getByTestId('stream-opt-error-handling'));

      await waitFor(() => {
        const commandText = screen.getByTestId('command-text');
        expect(commandText.textContent).not.toContain('-fflags');
        expect(commandText.textContent).not.toContain('-err_detect');
      });
    });

    it('toggling off stream mapping removes -map flags from command', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      await user.click(screen.getByTestId('stream-options-toggle'));
      await user.click(screen.getByTestId('stream-opt-stream-mapping'));

      await waitFor(() => {
        const commandText = screen.getByTestId('command-text');
        expect(commandText.textContent).not.toContain('-map 0:v:0');
        expect(commandText.textContent).not.toContain('-map 0:a:0');
      });
    });

    it('stream options panel is hidden in advanced mode', async () => {
      const user = userEvent.setup();
      renderBuilderTab();

      await user.click(screen.getByTestId('mode-toggle'));

      expect(screen.queryByTestId('stream-options-panel')).not.toBeInTheDocument();
    });
  });
});

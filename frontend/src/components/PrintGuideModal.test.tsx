/**
 * Unit tests for PrintGuideModal — channel-number range filter.
 *
 * bd-9q9z0: Operator-selectable channel-number range filter for the Print
 * Channel Guide modal. Defaults to the full range so existing all-channels
 * behaviour is preserved; operators can narrow to a sub-range (e.g. 100–199)
 * for booklet sections. Range is applied before the existing group filter.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { PrintGuideModal } from './PrintGuideModal';
import type { Channel, ChannelGroup } from '../types';

// --- Test fixtures ---

const GROUP_A: ChannelGroup = { id: 1, name: 'Sports', channel_count: 3 };
const GROUP_B: ChannelGroup = { id: 2, name: 'News', channel_count: 3 };

function makeChannel(id: number, num: number | null, groupId: number | null): Channel {
  return {
    id,
    channel_number: num,
    name: `Channel ${num ?? 'no-num'}`,
    channel_group_id: groupId,
    tvg_id: null,
    tvc_guide_stationid: null,
    epg_data_id: null,
    streams: [],
    stream_profile_id: null,
    uuid: `uuid-${id}`,
    logo_id: null,
    auto_created: false,
    auto_created_by: null,
    auto_created_by_name: null,
  };
}

// Channels 100–110 in Sports, 200–210 in News
const CHANNELS: Channel[] = [
  makeChannel(1, 100, 1),
  makeChannel(2, 105, 1),
  makeChannel(3, 110, 1),
  makeChannel(4, 200, 2),
  makeChannel(5, 205, 2),
  makeChannel(6, 210, 2),
];

const GROUPS: ChannelGroup[] = [GROUP_A, GROUP_B];

function renderModal(channels = CHANNELS, groups = GROUPS) {
  const onClose = vi.fn();
  render(
    <PrintGuideModal
      isOpen={true}
      onClose={onClose}
      channelGroups={groups}
      channels={channels}
      title="Test Guide"
    />
  );
  return { onClose };
}

function getFromInput() {
  return screen.getByLabelText(/from channel/i) as HTMLInputElement;
}

function getToInput() {
  return screen.getByLabelText(/to channel/i) as HTMLInputElement;
}

// --- Test suite ---

describe('PrintGuideModal — channel range filter (bd-9q9z0)', () => {
  describe('default range covers all channels (regression guard)', () => {
    it('From input defaults to the lowest channel number in the guide', () => {
      renderModal();
      expect(getFromInput().value).toBe('100');
    });

    it('To input defaults to the highest channel number in the guide', () => {
      renderModal();
      expect(getToInput().value).toBe('210');
    });

    it('all groups remain visible at default range', () => {
      renderModal();
      expect(screen.getByText('Sports')).toBeInTheDocument();
      expect(screen.getByText('News')).toBeInTheDocument();
    });

    it('Print button is enabled at default range', () => {
      renderModal();
      expect(
        screen.getByRole('button', { name: /print selected/i })
      ).not.toBeDisabled();
    });
  });

  describe('narrowed range filters channels in the group list', () => {
    it('setting To=150 hides the News group (200–210) from the group list', () => {
      renderModal();
      const toInput = getToInput();
      fireEvent.change(toInput, { target: { value: '150' } });
      // News group has no channels in 100–150, should be hidden from group list
      expect(screen.queryByText('News')).not.toBeInTheDocument();
      expect(screen.getByText('Sports')).toBeInTheDocument();
    });

    it('setting From=200 hides the Sports group (100–110) from the group list', () => {
      renderModal();
      const fromInput = getFromInput();
      fireEvent.change(fromInput, { target: { value: '200' } });
      expect(screen.queryByText('Sports')).not.toBeInTheDocument();
      expect(screen.getByText('News')).toBeInTheDocument();
    });

    it('setting From=105 To=205 shows both groups (each has at least one channel in range)', () => {
      renderModal();
      fireEvent.change(getFromInput(), { target: { value: '105' } });
      fireEvent.change(getToInput(), { target: { value: '205' } });
      expect(screen.getByText('Sports')).toBeInTheDocument();
      expect(screen.getByText('News')).toBeInTheDocument();
    });
  });

  describe('invalid range — From > To', () => {
    it('shows a validation error when From > To', () => {
      renderModal();
      fireEvent.change(getFromInput(), { target: { value: '210' } });
      fireEvent.change(getToInput(), { target: { value: '100' } });
      expect(screen.getByRole('alert')).toBeInTheDocument();
      expect(screen.getByRole('alert').textContent).toMatch(/from.*must.*to|range.*invalid/i);
    });

    it('disables the Print button when From > To', () => {
      renderModal();
      fireEvent.change(getFromInput(), { target: { value: '210' } });
      fireEvent.change(getToInput(), { target: { value: '100' } });
      expect(screen.getByRole('button', { name: /print selected/i })).toBeDisabled();
    });
  });

  describe('out-of-bounds clamping with operator hint', () => {
    it('clamps From below the minimum and shows an Adjusted hint', () => {
      renderModal();
      fireEvent.change(getFromInput(), { target: { value: '1' } });
      fireEvent.blur(getFromInput());
      // Should clamp to 100 (the minimum)
      expect(getFromInput().value).toBe('100');
      expect(screen.getByText(/adjusted to/i)).toBeInTheDocument();
    });

    it('clamps To above the maximum and shows an Adjusted hint', () => {
      renderModal();
      fireEvent.change(getToInput(), { target: { value: '9999' } });
      fireEvent.blur(getToInput());
      // Should clamp to 210 (the maximum)
      expect(getToInput().value).toBe('210');
      expect(screen.getByText(/adjusted to/i)).toBeInTheDocument();
    });
  });

  describe('no channels in range — graceful empty state', () => {
    it('shows an empty state message when no groups have channels in the range', () => {
      renderModal();
      fireEvent.change(getFromInput(), { target: { value: '300' } });
      fireEvent.change(getToInput(), { target: { value: '400' } });
      expect(screen.getByText(/no channels in range/i)).toBeInTheDocument();
    });

    it('disables the Print button when no channels are in range', () => {
      renderModal();
      fireEvent.change(getFromInput(), { target: { value: '300' } });
      fireEvent.change(getToInput(), { target: { value: '400' } });
      expect(screen.getByRole('button', { name: /print selected/i })).toBeDisabled();
    });
  });
});

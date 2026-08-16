/**
 * bead enhancedchannelmanager-ok8tj — the drag-drop half.
 *
 * A doc tester could not tell "no channel was close enough" from "the dedup
 * feature did not run". Commit `941d9087` fixed that for the `Create in…`
 * trigger and left drag-drop, trigger path #1 in the same article, unchanged.
 * These cases assert that every branch of a drop now produces a distinct,
 * operator-readable outcome — and that the one branch which must stay quiet
 * does.
 *
 * This is the layer that can prove it: nothing in the suite can drive a real
 * drag-drop onto a ChannelsPane group header, so the sentences are composed
 * here and asserted directly rather than through a rendered drop.
 */
import { describe, it, expect } from 'vitest';
import { describeDedupDropReport } from './dedupDropMessages';

describe('describeDedupDropReport (bead enhancedchannelmanager-ok8tj)', () => {
  it('says nothing when a candidate was found — the modal is the message', () => {
    expect(
      describeDedupDropReport(
        { outcome: 'candidate', streamName: 'US: CNN', streamCount: 1 },
        '"News Channels"',
      ),
    ).toBeNull();
  });

  it('says nothing matched, naming the stream and the group, when the lookup came back empty', () => {
    const message = describeDedupDropReport(
      { outcome: 'no_candidate', streamName: 'US: CNN', streamCount: 1 },
      '"News Channels"',
    );

    expect(message?.type).toBe('info');
    expect(message?.title).toBe('No duplicate found');
    expect(message?.message).toContain('"News Channels"');
    expect(message?.message).toContain('"US: CNN"');
    expect(message?.message).toMatch(/threshold/i);
  });

  it('says the check was unavailable — not that nothing matched — when the lookup failed', () => {
    const message = describeDedupDropReport(
      { outcome: 'lookup_failed', streamName: 'US: CNN', streamCount: 1 },
      '"News Channels"',
    );

    expect(message?.type).toBe('warning');
    expect(message?.title).toBe('Duplicate check unavailable');
    expect(message?.message).toContain('"News Channels"');
  });

  it('says the check did not run for a multi-stream drop, and how many streams', () => {
    const message = describeDedupDropReport(
      { outcome: 'skipped_multi_stream', streamName: null, streamCount: 4 },
      '"News Channels"',
    );

    expect(message?.type).toBe('info');
    expect(message?.title).toBe('Duplicate check skipped');
    expect(message?.message).toContain('4');
    expect(message?.message).toMatch(/single-stream/i);
  });

  it('says the check did not run when the dropped stream could not be read', () => {
    const message = describeDedupDropReport(
      { outcome: 'skipped_unknown_stream', streamName: null, streamCount: 1 },
      '"News Channels"',
    );

    expect(message?.type).toBe('info');
    expect(message?.title).toBe('Duplicate check skipped');
    expect(message?.message).toMatch(/could not read/i);
  });

  /**
   * The failure this bead is about is two DIFFERENT things looking the same,
   * so distinctness is the property, not any one sentence. If a later edit
   * makes two outcomes read alike, the operator is back where the doc tester
   * was.
   */
  it('gives every reported outcome a distinguishable message', () => {
    const reported = (['no_candidate', 'lookup_failed', 'skipped_multi_stream', 'skipped_unknown_stream'] as const)
      .map((outcome) => describeDedupDropReport(
        { outcome, streamName: 'US: CNN', streamCount: 2 },
        '"News Channels"',
      ))
      .map((message) => `${message?.title}|${message?.message}`);

    expect(new Set(reported).size).toBe(reported.length);
  });

  it('carries the caller-supplied group label verbatim, including the ungrouped bucket', () => {
    const message = describeDedupDropReport(
      { outcome: 'no_candidate', streamName: 'US: CNN', streamCount: 1 },
      'the ungrouped list',
    );

    expect(message?.message).toContain('the ungrouped list');
  });
});

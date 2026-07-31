const CAPABILITY_TIER_ORDER = ['4K', 'FHD', 'HD', 'SD'] as const;

type CapabilityStats = {
  probe_status?: string | null;
  resolution?: string | null;
};

/** Distinct successfully-probed resolution tiers, always highest-first. */
export function channelCapabilityTiers(
  streamIds: number[],
  statsMap: Map<number, CapabilityStats>,
): string[] {
  const tiers = new Set<string>();
  for (const streamId of streamIds) {
    const stats = statsMap.get(streamId);
    if (stats?.probe_status !== 'success' || !stats.resolution) continue;
    const match = stats.resolution.match(/^(\d+)x(\d+)$/);
    if (!match) continue;
    const height = Number.parseInt(match[2], 10);
    if (height >= 2160) tiers.add('4K');
    else if (height >= 1080) tiers.add('FHD');
    else if (height >= 720) tiers.add('HD');
    else tiers.add('SD');
  }
  return CAPABILITY_TIER_ORDER.filter((tier) => tiers.has(tier));
}

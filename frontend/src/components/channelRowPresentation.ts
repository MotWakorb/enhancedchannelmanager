import type { Channel, Logo } from '../types';

export function resolveChannelArtwork(
  channel: Channel,
  logoMap: ReadonlyMap<number, Logo>,
  isEditMode: boolean,
): string | null {
  if (isEditMode && channel._stagedLogoUrl) return channel._stagedLogoUrl;
  if (channel.logo_id == null) return null;
  const logo = logoMap.get(channel.logo_id);
  return logo?.cache_url || logo?.url || null;
}

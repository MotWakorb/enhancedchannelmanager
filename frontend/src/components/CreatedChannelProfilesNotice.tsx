import type { ChannelProfile } from '../types';
import type { CreatedChannelProfileIntent } from '../services/createdChannelProfiles';
import './CreatedChannelProfilesNotice.css';

interface CreatedChannelProfilesNoticeProps {
  pending: CreatedChannelProfileIntent[];
  busy: boolean;
  unknown: boolean;
  storageUnavailable: boolean;
  profiles: ChannelProfile[];
  onRetry: () => void;
  onDismiss: () => void;
  retryUnavailable?: boolean;
}

export function CreatedChannelProfilesNotice({
  pending, busy, unknown, storageUnavailable, profiles, onRetry, onDismiss, retryUnavailable = false,
}: CreatedChannelProfilesNoticeProps) {
  if (pending.length === 0) return null;
  return <section className="created-channel-profiles-notice" role={busy ? 'status' : 'alert'}>
    <div>
      <strong>{busy ? 'Checking profile assignments' : 'Profile assignments unresolved'} — {pending.length} created channel{pending.length === 1 ? '' : 's'}</strong>
      <p>The channels are already created. Retrying checks and updates profile visibility only; it does not create channels again.</p>
      {unknown && <p>Profile visibility could not be verified. The requested assignments are retained.</p>}
      {storageUnavailable && <p>Retry details could not be saved in this tab. Keep this page open until the assignments are resolved.</p>}
      {retryUnavailable && <p>Finish the current Edit Mode session before retrying profile assignments.</p>}
      <details>
        <summary>Review requested profile assignments</summary>
        <ul>{pending.map(intent => <li key={intent.channelId}>
          Channel #{intent.channelId}: {intent.profileIds.length === 0 ? 'No profiles' :
            intent.profileIds.map(id => profiles.find(profile => profile.id === id)?.name ?? `Profile #${id}`).join(', ')}
        </li>)}</ul>
      </details>
    </div>
    <button className="btn-secondary" disabled={busy || retryUnavailable} onClick={onRetry}>Retry profile assignments</button>
    <button className="btn-secondary" disabled={busy} onClick={() => {
      if (window.confirm('Forget these requested profile assignments? This does not change profile visibility or delete channels.')) onDismiss();
    }}>Discard saved profile intent</button>
  </section>;
}

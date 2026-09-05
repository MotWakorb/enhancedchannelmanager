import { useEffect, useId, useState } from 'react';
import { CustomSelect } from './CustomSelect';
import { deleteChannelNameMapping, getChannelNameMappings, saveChannelNameMapping, type ChannelNameMapping } from '../services/channelNameMappings';
import './MappedChannels.css';

interface MappedChannelsProps {
  selectedNames?: string[];
}

export function MappedChannels({ selectedNames }: MappedChannelsProps) {
  const id = useId();
  const [mappings, setMappings] = useState<ChannelNameMapping[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [editing, setEditing] = useState(Boolean(selectedNames));
  const [editingId, setEditingId] = useState<number>();
  const [mode, setMode] = useState('new');
  const [existingId, setExistingId] = useState('');
  const [preferred, setPreferred] = useState(selectedNames?.[0] ?? '');
  const [aliases, setAliases] = useState(selectedNames?.join('\n') ?? '');

  useEffect(() => {
    let active = true;
    getChannelNameMappings().then(({ mappings: loaded }) => {
      if (active) setMappings(loaded);
    }).catch((err: unknown) => {
      if (active) setError(err instanceof Error ? err.message : 'Failed to load mappings');
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const edit = (mapping?: ChannelNameMapping) => {
    setEditingId(mapping?.id);
    setMode('new');
    setPreferred(mapping?.preferred_name ?? selectedNames?.[0] ?? '');
    setAliases(mapping?.aliases.join('\n') ?? selectedNames?.join('\n') ?? '');
    setError('');
    setMessage('');
    setEditing(true);
  };

  const save = async () => {
    setBusy(true);
    setError('');
    setMessage('');
    const existing = mode === 'existing' ? mappings.find(m => String(m.id) === existingId) : undefined;
    try {
      const saved = await saveChannelNameMapping({
        preferred_name: existing?.preferred_name ?? preferred,
        aliases: [...(existing?.aliases ?? []), ...aliases.split('\n').filter(name => name !== '')],
      }, existing?.id ?? editingId);
      setMappings(current => [...current.filter(m => m.id !== saved.id), saved]);
      setEditing(false);
      setMessage('Mapping saved. Used by subsequent Create / Pipeline runs; no channels changed now.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save mapping');
    } finally { setBusy(false); }
  };

  const remove = async (mapping: ChannelNameMapping) => {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await deleteChannelNameMapping(mapping.id);
      setMappings(current => current.filter(m => m.id !== mapping.id));
      setMessage('Mapping removed. Existing channels and attachments are unchanged.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to remove mapping');
    } finally { setBusy(false); }
  };

  return <section className="mapped-channels" aria-label="Mapped channels">
    <p>Whole-name, case-insensitive literal aliases across providers. Saving applies to subsequent Create / Pipeline runs and configured automation, not existing channels.</p>
    {error && <p role="alert" className="error-message">{error}</p>}
    {message && <p role="status">{message}</p>}
    {loading ? <p role="status">Loading mappings...</p> : <>
      {!editing && <button className="btn-primary" onClick={() => edit()} disabled={busy}>Add mapping</button>}
      {editing && <form onSubmit={event => { event.preventDefault(); void save(); }}>
        <fieldset disabled={busy}>
          <legend>{editingId ? 'Edit mapping' : 'Add mapping'}</legend>
          {!editingId && <div className="mapped-channels-mode">
            <label><input type="radio" name={`${id}-mode`} checked={mode === 'existing'} onChange={() => setMode('existing')} />Existing</label>
            <label><input type="radio" name={`${id}-mode`} checked={mode === 'new'} onChange={() => setMode('new')} />Add new</label>
          </div>}
          {mode === 'existing' ? <CustomSelect ariaLabel="Existing mapping" value={existingId} onChange={setExistingId}
            options={mappings.map(m => ({ value: String(m.id), label: m.preferred_name }))} /> : <>
            <label htmlFor={`${id}-preferred`}>Preferred name</label>
            <input className="form-input" id={`${id}-preferred`} required maxLength={255} value={preferred} onChange={e => setPreferred(e.target.value)} />
          </>}
          <label htmlFor={`${id}-aliases`}>Alternative names (one per line)</label>
          <textarea className="form-input" id={`${id}-aliases`} rows={6} value={aliases} onChange={e => setAliases(e.target.value)} />
          <p>The preferred name also matches itself. Existing adds these aliases without removing its current aliases.</p>
          <div className="mapped-channels-actions">
            <button className="btn-primary" type="submit" disabled={mode === 'existing' && !existingId}>Save mapping</button>
            <button className="btn-secondary" type="button" onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </fieldset>
      </form>}
      {!selectedNames && <div className="mapped-channels-list">
        {mappings.length === 0 && <p>No mappings defined.</p>}
        {mappings.map(mapping => <article key={mapping.id}>
          <h3>{mapping.preferred_name}</h3>
          <p>{mapping.aliases.join(', ')}</p>
          <div className="mapped-channels-actions">
            <button className="btn-secondary" aria-label={`Edit ${mapping.preferred_name}`} disabled={busy || editing} onClick={() => edit(mapping)}>Edit</button>
            <button className="btn-secondary" aria-label={`Remove ${mapping.preferred_name}`} disabled={busy || editing} onClick={() => void remove(mapping)}>Remove</button>
          </div>
        </article>)}
      </div>}
    </>}
  </section>;
}

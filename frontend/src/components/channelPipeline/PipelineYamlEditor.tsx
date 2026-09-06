import { useEffect, useId, useRef, useState } from 'react';
import { getPipelineRulesYaml, savePipelineRulesYaml, type PipelineRulesYamlResponse } from '../../services/channelPipelineApi';
import { HttpError } from '../../services/httpClient';
import { ModalOverlay } from '../ModalOverlay';
import { useOwnedDialog } from '../../hooks/useOwnedDialog';
import './PipelineYamlEditor.css';

interface DeletionSummary {
  deletions: { id: number; name: string }[];
  warnings: string[];
}

export function PipelineYamlEditor({ onSaved }: { onSaved: () => void }) {
  const id = useId();
  const [snapshot, setSnapshot] = useState<PipelineRulesYamlResponse | null>(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [find, setFind] = useState('');
  const [replacement, setReplacement] = useState('');
  const [deletions, setDeletions] = useState<DeletionSummary | null>(null);
  const [reloadConfirm, setReloadConfirm] = useState(false);
  const { titleId: deletionTitleId, containerRef: deletionContainerRef } = useOwnedDialog(deletions !== null);
  const { titleId: reloadTitleId, containerRef: reloadContainerRef } = useOwnedDialog(reloadConfirm);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const dirty = snapshot !== null && draft !== snapshot.yaml_content;

  useEffect(() => {
    let cancelled = false;
    getPipelineRulesYaml().then(result => {
      if (!cancelled) { setSnapshot(result); setDraft(result.yaml_content); }
    }).catch((err: unknown) => {
      if (!cancelled) setError(err instanceof Error ? err.message : 'Unable to load YAML');
    }).finally(() => { if (!cancelled) setBusy(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!dirty) return;
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [dirty]);

  async function reload() {
    setReloadConfirm(false);
    setBusy(true);
    setError('');
    try {
      const result = await getPipelineRulesYaml();
      setSnapshot(result);
      setDraft(result.yaml_content);
      setStatus('Loaded current rules.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to reload; draft retained');
    } finally { setBusy(false); }
  }

  async function save(confirm = false) {
    if (!snapshot) return;
    setBusy(true);
    setError('');
    setStatus('');
    setDeletions(null);
    try {
      const result = await savePipelineRulesYaml({
        yaml_content: draft, revision: snapshot.revision, confirm_deletions: confirm,
      });
      setSnapshot(result);
      setDraft(result.yaml_content);
      setStatus('Rules saved. No channels were changed.');
      onSaved();
    } catch (err) {
      const detail = err instanceof HttpError && typeof err.detail === 'object' && err.detail !== null
        ? err.detail as Record<string, unknown> : null;
      if (detail?.code === 'deletion_confirmation_required' && Array.isArray(detail.deletions) && Array.isArray(detail.warnings)) {
        setDeletions(detail as unknown as DeletionSummary);
      } else {
        setError(detail ? JSON.stringify(detail, null, 2) : err instanceof Error ? err.message : 'Unable to save; draft retained');
      }
    } finally { setBusy(false); }
  }

  function findNext() {
    if (!find || !textarea.current) return;
    const start = textarea.current.selectionEnd;
    const next = draft.indexOf(find, start);
    const index = next >= 0 ? next : draft.indexOf(find);
    if (index < 0) { setStatus('No literal matches.'); return; }
    textarea.current.focus();
    textarea.current.setSelectionRange(index, index + find.length);
    setStatus('Literal match selected.');
  }

  return (
    <section className="pipeline-yaml-editor" aria-label="YAML editor">
      <h3>Rules YAML</h3>
      <p>Complete collection: standard and Event Sync, enabled and disabled. List filters do not apply.
        Keep an id to edit or rename; omit it to create a new rule. Omitted rules require deletion confirmation.
        Runtime, ownership and history are not editable. Switching rule views keeps this draft.</p>
      <div className="pipeline-yaml-controls">
        <label htmlFor={`${id}-find`}>Find (literal)
          <input id={`${id}-find`} value={find} onChange={event => setFind(event.target.value)} />
        </label>
        <label htmlFor={`${id}-replace`}>Replace with
          <input id={`${id}-replace`} value={replacement} onChange={event => setReplacement(event.target.value)} />
        </label>
        <button className="btn-secondary" onClick={findNext} disabled={!find || busy}>Find next</button>
        <button className="btn-secondary" onClick={() => {
          setDraft(draft.split(find).join(replacement)); setError(''); setStatus('Literal replacement applied to draft.');
        }} disabled={!find || busy || !snapshot}>Replace all</button>
      </div>
      <div className="modal-form-group">
        <label htmlFor={`${id}-yaml`}>Rules YAML</label>
        <textarea id={`${id}-yaml`} ref={textarea} value={draft} rows={24} spellCheck={false} wrap="off"
          disabled={busy || !snapshot} onChange={event => { setDraft(event.target.value); setError(''); setStatus(''); }} />
      </div>
      {error && <pre className="pipeline-yaml-error" role="alert">{error}</pre>}
      <div className="pipeline-yaml-controls">
        <button className="btn-secondary" disabled={busy} onClick={() => dirty ? setReloadConfirm(true) : void reload()}>Reload YAML</button>
        <button className="btn-primary" disabled={busy || !snapshot || !dirty} onClick={() => void save()}>Save YAML</button>
        <span role="status">{busy ? 'Working...' : status || (dirty ? 'Unsaved changes' : 'No unsaved changes')}</span>
      </div>
      {deletions && <ModalOverlay onClose={() => setDeletions(null)} role="dialog" aria-modal="true" aria-labelledby={deletionTitleId}>
        <div className="modal-container modal-md" ref={deletionContainerRef}>
          <div className="modal-header"><h2 id={deletionTitleId}>Confirm rule deletions</h2></div>
          <div className="modal-body">
            <p>Saving this draft deletes these omitted rules:</p>
            <ul>{deletions.deletions.map(rule => <li key={rule.id}>{rule.name} (id {rule.id})</li>)}</ul>
            {deletions.warnings.map(warning => <p key={warning}>{warning}</p>)}
          </div>
          <div className="modal-footer">
            <button className="btn-secondary" onClick={() => setDeletions(null)}>Cancel</button>
            <button className="btn-danger" onClick={() => void save(true)}>Confirm deletions and save</button>
          </div>
        </div>
      </ModalOverlay>}
      {reloadConfirm && <ModalOverlay onClose={() => setReloadConfirm(false)} role="dialog" aria-modal="true" aria-labelledby={reloadTitleId}>
        <div className="modal-container modal-sm" ref={reloadContainerRef}>
          <div className="modal-header"><h2 id={reloadTitleId}>Unsaved Changes</h2></div>
          <div className="modal-body"><p>Reloading replaces this draft with current saved rules. Keep editing to preserve it.</p></div>
          <div className="modal-footer">
            <button className="btn-secondary" onClick={() => setReloadConfirm(false)}>Keep editing</button>
            <button className="btn-danger" onClick={() => void reload()}>Discard draft and reload</button>
          </div>
        </div>
      </ModalOverlay>}
    </section>
  );
}

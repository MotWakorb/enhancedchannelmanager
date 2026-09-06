import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PipelineYamlEditor } from './PipelineYamlEditor';
import * as api from '../../services/channelPipelineApi';
import { HttpError } from '../../services/httpClient';

vi.mock('../../services/channelPipelineApi');
afterEach(() => vi.resetAllMocks());
const snapshot = { yaml_content: 'version: 1\nrules:\n- id: 1\n  name: Original\n', revision: 'original' };

describe('PipelineYamlEditor', () => {
  it('loads the full snapshot and performs literal replacement through Save', async () => {
    vi.mocked(api.getPipelineRulesYaml).mockResolvedValue(snapshot);
    vi.mocked(api.savePipelineRulesYaml).mockImplementation(async body => ({ ...body, revision: 'saved' }));
    const onSaved = vi.fn();
    const user = userEvent.setup();
    render(<PipelineYamlEditor onSaved={onSaved} />);
    const textarea = await screen.findByRole('textbox', { name: 'Rules YAML' });
    await waitFor(() => expect(textarea).toHaveValue(snapshot.yaml_content));
    fireEvent.change(textarea, { target: { value: 'name: .* Original .*' } });
    await user.type(screen.getByRole('textbox', { name: 'Find (literal)' }), '.*');
    await user.type(screen.getByRole('textbox', { name: 'Replace with' }), '$&');
    await user.click(screen.getByRole('button', { name: 'Replace all' }));
    expect(textarea).toHaveValue('name: $& Original $&');
    await user.click(screen.getByRole('button', { name: 'Save YAML' }));
    await waitFor(() => expect(api.savePipelineRulesYaml).toHaveBeenCalledWith({
      yaml_content: 'name: $& Original $&', revision: 'original', confirm_deletions: false,
    }));
    expect(onSaved).toHaveBeenCalledOnce();
  });

  it('preserves draft on deletion cancel, validation errors and reload cancel', async () => {
    vi.mocked(api.getPipelineRulesYaml).mockResolvedValue(snapshot);
    vi.mocked(api.savePipelineRulesYaml).mockRejectedValue(new HttpError('Confirm', 409, {
      code: 'deletion_confirmation_required', deletions: [{ id: 1, name: 'Original' }],
      warnings: ['Selected schedules may become stale'],
    }));
    const user = userEvent.setup();
    render(<PipelineYamlEditor onSaved={vi.fn()} />);
    const textarea = await screen.findByRole('textbox', { name: 'Rules YAML' });
    await waitFor(() => expect(textarea).toHaveValue(snapshot.yaml_content));
    fireEvent.change(textarea, { target: { value: 'version: 1\nrules: []' } });
    await user.click(screen.getByRole('button', { name: 'Save YAML' }));
    expect(await screen.findByRole('dialog', { name: 'Confirm rule deletions' })).toHaveTextContent('Original');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus());
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(textarea).toHaveValue('version: 1\nrules: []');
    expect(api.savePipelineRulesYaml).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: 'Reload YAML' }));
    await user.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(api.getPipelineRulesYaml).toHaveBeenCalledTimes(1);
    vi.mocked(api.savePipelineRulesYaml).mockRejectedValue(new HttpError('Invalid YAML', 422, { line: 3, column: 4, message: 'Duplicate key' }));
    await user.click(screen.getByRole('button', { name: 'Save YAML' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Duplicate key');
    expect(screen.getByRole('alert')).toHaveTextContent('3');
    expect(textarea).toHaveValue('version: 1\nrules: []');
  });
});

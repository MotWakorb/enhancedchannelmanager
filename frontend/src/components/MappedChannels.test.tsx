import { render, screen, waitFor } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/mocks/server';
import { MappedChannels } from './MappedChannels';

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('Mapped channels', () => {
  it('prefills selected literal names and adds them to an existing mapping', async () => {
    const user = userEvent.setup();
    let saved: unknown;
    server.use(
      http.get('/api/normalization/mappings', () => HttpResponse.json({ mappings: [
        { id: 8, preferred_name: 'Stars TV', aliases: ['Stars TV', 'Stars.TV'] },
      ] })),
      http.put('/api/normalization/mappings/8', async ({ request }) => {
        saved = await request.json();
        return HttpResponse.json({ id: 8, ...(saved as object) });
      }),
    );
    render(<MappedChannels selectedNames={['Stars-TV', 'Stars.TV']} />);
    expect(await screen.findByLabelText('Alternative names (one per line)')).toHaveValue('Stars-TV\nStars.TV');
    await user.click(screen.getByRole('radio', { name: 'Existing' }));
    await user.click(screen.getByRole('button', { name: 'Existing mapping' }));
    await user.click(screen.getByRole('option', { name: 'Stars TV' }));
    await user.click(screen.getByRole('button', { name: 'Save mapping' }));
    await waitFor(() => expect(saved).toEqual({ preferred_name: 'Stars TV', aliases: ['Stars TV', 'Stars.TV', 'Stars-TV', 'Stars.TV'] }));
    expect(await screen.findByRole('status')).toHaveTextContent('Mapping saved');
  });

  it('reviews, edits, adds, removes and keeps API errors visible', async () => {
    const user = userEvent.setup();
    server.use(
      http.get('/api/normalization/mappings', () => HttpResponse.json({ mappings: [
        { id: 1, preferred_name: 'TVN', aliases: ['TVN', 'TVN-HD'] },
      ] })),
      http.put('/api/normalization/mappings/1', () => HttpResponse.json({ detail: 'Alias already owned' }, { status: 409 })),
      http.delete('/api/normalization/mappings/1', () => new HttpResponse(null, { status: 204 })),
      http.post('/api/normalization/mappings', () => HttpResponse.json({ id: 2, preferred_name: 'Polonia', aliases: ['Polonia'] }, { status: 201 })),
    );
    render(<MappedChannels />);
    await user.click(await screen.findByRole('button', { name: 'Edit TVN' }));
    expect(screen.getByLabelText('Preferred name')).toHaveValue('TVN');
    await user.click(screen.getByRole('button', { name: 'Save mapping' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Alias already owned');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await user.click(screen.getByRole('button', { name: 'Remove TVN' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Edit TVN' })).not.toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Add mapping' }));
    await user.type(screen.getByLabelText('Preferred name'), 'Polonia');
    await user.click(screen.getByRole('button', { name: 'Save mapping' }));
    expect(await screen.findByRole('button', { name: 'Edit Polonia' })).toBeInTheDocument();
  });
});

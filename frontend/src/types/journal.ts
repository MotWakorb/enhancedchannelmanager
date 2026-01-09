/**
 * Journal types for tracking changes to channels, EPG sources, and M3U accounts.
 */

export type JournalCategory = 'channel' | 'epg' | 'm3u';

export type JournalActionType =
  | 'create'
  | 'update'
  | 'delete'
  | 'stream_add'
  | 'stream_remove'
  | 'stream_reorder'
  | 'reorder'
  | 'refresh';

export interface JournalEntry {
  id: number;
  timestamp: string;
  category: JournalCategory;
  action_type: JournalActionType;
  entity_id: number | null;
  entity_name: string;
  description: string;
  before_value: Record<string, unknown> | null;
  after_value: Record<string, unknown> | null;
  user_initiated: boolean;
  batch_id: string | null;
}

export interface JournalQueryParams {
  page?: number;
  page_size?: number;
  category?: JournalCategory;
  action_type?: JournalActionType;
  date_from?: string;
  date_to?: string;
  search?: string;
  user_initiated?: boolean;
}

export interface JournalResponse {
  count: number;
  page: number;
  page_size: number;
  total_pages: number;
  results: JournalEntry[];
}

export interface JournalStats {
  total_entries: number;
  by_category: Record<string, number>;
  by_action_type: Record<string, number>;
  date_range: {
    oldest: string | null;
    newest: string | null;
  };
}

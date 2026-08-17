/**
 * Journal types for tracking changes to channels, EPG sources, and M3U accounts.
 */

export type JournalCategory = 'channel' | 'epg' | 'm3u' | 'watch' | 'task' | 'auto_creation' | 'event_sync';

export type MutationSource = 'ui' | 'mcp_ai' | 'scheduler' | 'auto_creation';

export type JournalActionType =
  | 'create'
  | 'update'
  | 'delete'
  | 'stream_add'
  | 'stream_remove'
  | 'stream_reorder'
  | 'reorder'
  /**
   * A pending merge the operator accepted that ECM could NOT apply to
   * Dispatcharr — the stream name matched zero or several streams, or the
   * search could not establish an answer. Its own action type so the affected
   * merges are findable by filter rather than by reading every row's prose
   * (bead enhancedchannelmanager-i5ic0).
   */
  | 'merge_unapplied'
  /**
   * A bulk-merge group whose streams moved onto the target but whose source
   * channels are NOT all gone — the deletions that errored and the ones the
   * request never reached both leave the channel there. Its own action type,
   * for the same reason as `merge_unapplied`: the groups needing attention are
   * findable by filter rather than by reading every row's prose.
   * `after_value.undeleted_ids` names the channels still upstream
   * (bead enhancedchannelmanager-ftidn).
   */
  | 'bulk_merge_incomplete'
  | 'refresh'
  | 'start'
  | 'stop'
  | 'complete'
  | 'cancel'
  | 'fail'
  | 'error';

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
  mutation_source: MutationSource | null;
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
  mutation_source?: MutationSource;
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

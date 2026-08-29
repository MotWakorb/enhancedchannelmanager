export interface ProfileConflictSource {
  source_group_id: number;
  source_group_name: string;
  m3u_account_id: number | null;
  m3u_account_name: string;
}

export interface ProfileConflictChoice {
  choice_key: string;
  profile_ids: number[];
  profile_names: string[];
  sources: ProfileConflictSource[];
}

export interface ProfileConflictEvidence {
  fingerprint_version: number;
  target: { effective_group_id: number; name: string };
  choices: ProfileConflictChoice[];
}

export interface ProfileConflictReview {
  id: number;
  fingerprint: string;
  effective_group_id: number;
  status: 'pending' | 'accepted' | 'superseded';
  accepted_choice_key: string | null;
  accepted_profile_ids: number[] | null;
  evidence: ProfileConflictEvidence;
  created_at: number;
  last_seen_at: number;
  resolved_at: number | null;
  applied_at: number | null;
  retry_error: string | null;
}

export interface ProfileConflictReviewsResponse {
  reviews: ProfileConflictReview[];
  total: number;
}

export interface AcceptProfileConflictOutcome {
  status: 'accepted';
  applied: boolean;
  updated_account_ids: number[];
  failed_account_ids: number[];
  retry_error: string | null;
}

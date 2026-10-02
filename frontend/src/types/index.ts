export type EvidenceBoundary =
  | 'OBSERVED'
  | 'CORRELATED'
  | 'INFERRED'
  | 'UNKNOWN'
  | 'BLOCKED'
  | 'SYNTHETIC';

export type Confidence = 'HIGH' | 'MEDIUM' | 'LOW';

export type AttentionLevel = 'none' | 'possible' | 'required' | 'blocked' | 'synthetic';

export type Severity = 'high' | 'medium' | 'low' | 'none';

/* ------------------------------------------------------------------ *
 * Phase 4C: stage intelligence
 *
 * Two independent recency axes. They are deliberately separate fields:
 *   stage_*    how long the item has occupied its current pipeline stage
 *   evidence_* how long since ANY observation was recorded for the item
 *
 * An item can be overdue in its stage while its evidence is still fresh
 * (polling keeps re-reporting the same state), and evidence can be ancient
 * while the item is trivially complete. Collapsing these into one flag
 * destroys the distinction the backend goes to some length to preserve.
 * ------------------------------------------------------------------ */

/** How the current stage compares to its expected duration. */
export type StageFreshness = 'RECENT' | 'STALE' | 'UNKNOWN';

/** How recently this item was observed at all. */
export type EvidenceFreshness = 'CURRENT' | 'AGED' | 'STALE';

export interface StageIntelligence {
  stage_start_time?: string | null;
  stage_duration_seconds?: number | null;
  stage_freshness?: StageFreshness;
  missing_transition?: string | null;
  transition_evidence?: string[];
  evidence_age_seconds?: number | null;
  evidence_freshness?: EvidenceFreshness;
}

export interface MediaItem extends StageIntelligence {
  id: string;
  media_type: 'movie' | 'episode';
  title: string;
  season?: number;
  episode?: number;
  tvdb_id?: string;
  tmdb_id?: string;
  imdb_id?: string;
  current_state: string;
  progress?: number;
  current_service?: string;
  last_event_at?: string;
  next_expected_state?: string;
  event_count: number;
  first_seen_at?: string;
  confidence?: Confidence;
  evidence_boundary?: EvidenceBoundary;
  confidence_basis?: string;
  download_attempts?: DownloadAttempt[];
  ingestion_sources?: ('webhook' | 'polling')[];
  state?: string; // For PipelineRow compatibility
  guardarr_events?: GuardarrEvent[];
}

export interface PipelineRow extends StageIntelligence {
  id: string;
  title: string;
  state: string;
  progress?: number;
  current_service?: string;
  last_event_at?: string;
  season?: number;
  episode?: number;
  media_type: 'movie' | 'episode';
  tvdb_id?: string;
  tmdb_id?: string;
  imdb_id?: string;
  current_state: string;
  confidence?: Confidence;
  evidence_boundary?: EvidenceBoundary;
  confidence_basis?: string;
  download_attempts?: DownloadAttempt[];
  ingestion_sources?: ('webhook' | 'polling')[];
  first_seen_at?: string;
  event_count: number;
}

export interface DownloadAttempt {
  hash: string;
  first_event: string;
  last_event: string;
  state: string;
  progress: number;
  category: string;
  tags: string;
  is_cross_seed: boolean;
  event_count: number;
  ingestion_sources?: ('webhook' | 'polling')[];
}

export interface OrphanTorrent {
  hash: string;
  title: string;
  category: string;
  tags: string;
  state: string;
  progress: number;
  save_path: string;
  reason: string;
}

export interface Indexer {
  id: number;
  name: string;
  enabled: boolean;
  status: string;
  last_response?: string;
  recent_errors?: string;
}

export interface SearchEvent {
  query: string;
  indexer: string;
  results: number;
  timestamp: string;
}

export interface GuardarrEvent {
  id: string;
  timestamp: string;
  event_type: string;
  state: string;
  reservation_id?: string;
  content_id?: string;
  arr_item_id?: string;
  torrent_metadata_hash?: string;
  associated_path?: string;
  target_device?: string;
  max_bytes?: number;
  expected_bytes?: number;
  observed_materialized_bytes?: number;
  remaining_unfulfilled_bytes?: number;
  import_mode?: string;
  priority?: number;
  owner?: string;
  torrent_tag?: string;
  idempotency_key?: string;
  evidence_boundary?: EvidenceBoundary;
}

export interface Event {
  id: number;
  timestamp: string;
  source_service: string;
  event_type: string;
  media_type: string;
  title: string;
  season?: number;
  episode?: number;
  correlation_key: string;
  status: string;
  error_message?: string;
  ingestion?: 'webhook' | 'polling';
  evidence_boundary?: EvidenceBoundary;
  confidence_basis?: string;
  normalized_metadata?: Record<string, any> | string;
}

export interface ServiceStatus {
  service: string;
  status: string;
  version?: string;
  error?: string;
  details?: Record<string, any>;
  evidence_boundary?: EvidenceBoundary;
}

export interface TimelineEvent {
  timestamp: string;
  source: string;
  event_type: string;
  status: string;
  error_message?: string;
  normalized_metadata?: Record<string, any>;
  ingestion?: 'webhook' | 'polling';
  evidence_boundary?: EvidenceBoundary;
  confidence_basis?: string;
}

export interface ItemDetail extends MediaItem {
  timeline: TimelineEvent[];
  last_event?: TimelineEvent;
  next_expected_state?: string;
}

export interface Explanation {
  correlation_key: string;
  current_state: string;
  reason: string;
  evidence: string[];
  timeline_summary: string;
  evidence_boundary?: EvidenceBoundary;
  confidence_basis?: string;
}

export interface SummaryCounts {
  active: number;
  attention: number;
  completed_today: number;
  failed: number;
}

export interface SseEvent {
  event_id?: string;
  event_type: string;
  source_service: string;
  media_id: string;
  timestamp?: string;
  title: string;
  media_type: string;
  provenance: string;
  evidence_boundary?: EvidenceBoundary;
  confidence_basis?: string;
  metadata?: Record<string, any>;
}

/* ------------------------------------------------------------------ *
 * Phase 4B: operational attention
 * Mirrors backend Orchestrator output (backend/backend/services/orchestrator.py)
 * ------------------------------------------------------------------ */

export interface AttentionItemRef extends StageIntelligence {
  id: string;
  title: string;
  media_type: string;
  current_state: string;
  last_event_at?: string | null;
  event_count: number;
}

export interface AttentionItem extends StageIntelligence {
  attention_level: AttentionLevel;
  /** Stable machine-readable diagnosis code, e.g. GRAB_NO_DOWNLOAD. */
  attention_class: string;
  reason: string;
  evidence: string[];
  evidence_boundary: EvidenceBoundary;
  confidence_basis?: string | null;
  requires_attention: boolean;
  severity?: Severity;
  state?: string;
  item: AttentionItemRef;
}

export interface AttentionStats {
  total_attention: number;
  required: number;
  possible: number;
  blocked: number;
  high_severity: number;
  medium_severity: number;
  items_count: number;
}

export interface ActivityStats {
  period_days: number;
  by_source: Record<string, number>;
  by_type: Record<string, number>;
  failed: number;
  stuck: number;
  today: number;
  total: number;
}

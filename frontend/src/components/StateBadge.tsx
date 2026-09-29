type Tone = 'progress' | 'ok' | 'warn' | 'danger' | 'idle' | 'info' | 'blocked';

const TONE: Record<string, Tone> = {
  wanted: 'idle',
  search_started: 'info',
  search_completed: 'info',
  search_failed: 'danger',
  release_grabbed: 'progress',
  download_started: 'progress',
  download_progress: 'progress',
  download_completed: 'ok',
  download_failed: 'danger',
  import_started: 'progress',
  import_completed: 'ok',
  import_failed: 'danger',
  available: 'ok',
  stuck: 'warn',
  unknown: 'blocked',
  indexer_search: 'info',
  indexer_search_completed: 'ok',
  indexer_search_failed: 'danger',
  release_rejected: 'warn',
  storage_estimate: 'info',
  storage_admit: 'ok',
  storage_release: 'info',
  storage_reconcile: 'info',
  security_blocked: 'danger',
  security_quarantined: 'danger',
  security_alert: 'warn',
  torrent_associated: 'progress',
  torrent_unreserved: 'warn',
};

interface Props {
  state?: string;
  label?: string;
}

/** Pipeline state chip. Text carries the meaning; colour is secondary. */
export const StateBadge: React.FC<Props> = ({ state, label }) => {
  if (!state) return null;
  return (
    <span className="state" data-tone={TONE[state] ?? 'idle'}>
      {(label ?? state).replace(/_/g, ' ')}
    </span>
  );
};

export default StateBadge;

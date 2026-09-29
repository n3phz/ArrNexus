import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../services/api';
import { MediaItem } from '../types';
import EmptyState from '../components/EmptyState';
import EvidenceBadge from '../components/EvidenceBadge';
import StateBadge from '../components/StateBadge';

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'in_flight', label: 'In flight' },
  { key: 'attention', label: 'Needs attention' },
  { key: 'available', label: 'Available' },
];

function ago(ts?: string): string {
  if (!ts) return '—';
  const diff = Date.now() - new Date(ts).getTime();
  if (Number.isNaN(diff)) return '—';
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

const ATTENTION_STATES = ['stuck', 'download_failed', 'import_failed', 'unknown'];
const SETTLED_STATES = ['available', 'import_completed', 'download_completed'];

interface Props {
  refreshKey: number;
}

/** Media view — operational list, deliberately not a poster wall. */
export const Media: React.FC<Props> = ({ refreshKey }) => {
  const [items, setItems] = useState<MediaItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getItems({ limit: 500 })
      .then((data) => {
        if (cancelled) return;
        setItems(data);
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load media');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items
      .filter((i) => {
        if (filter === 'in_flight' && SETTLED_STATES.includes(i.current_state)) return false;
        if (filter === 'available' && i.current_state !== 'available') return false;
        if (filter === 'attention' && !ATTENTION_STATES.includes(i.current_state)) return false;
        if (q && !i.title.toLowerCase().includes(q) && !i.id.toLowerCase().includes(q)) return false;
        return true;
      })
      .sort((a, b) => {
        const rank = (s: string) => (ATTENTION_STATES.includes(s) ? 0 : SETTLED_STATES.includes(s) ? 2 : 1);
        const r = rank(a.current_state) - rank(b.current_state);
        if (r !== 0) return r;
        return new Date(b.last_event_at ?? 0).getTime() - new Date(a.last_event_at ?? 0).getTime();
      });
  }, [items, filter, query]);

  if (error) {
    return (
      <EmptyState title="Cannot load media" tone="error">
        {error}
      </EmptyState>
    );
  }

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1 className="page-title">Media</h1>
          <p className="page-sub">
            Correlated media items with their current pipeline state and the evidence behind it.
          </p>
        </div>
      </div>

      <div className="filters">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className="chip"
            aria-pressed={filter === f.key}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
        <span className="spacer" />
        <input
          className="input"
          placeholder="Filter by title or correlation key…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Filter media"
        />
      </div>

      <section className="panel">
        <div className="panel-head">
          <span className="panel-title">
            {rows.length} item{rows.length === 1 ? '' : 's'}
          </span>
        </div>
        <div className="panel-body tight">
          {loading ? (
            <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div className="skeleton" />
              <div className="skeleton" />
              <div className="skeleton" />
            </div>
          ) : rows.length === 0 ? (
            <EmptyState title="No media to show">
              {items.length === 0
                ? 'No media has been correlated yet. The control plane builds items as services report activity.'
                : 'No items match the current filter.'}
            </EmptyState>
          ) : (
            <div className="tbl-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>Type</th>
                    <th>State</th>
                    <th>Progress</th>
                    <th>Service</th>
                    <th>Last activity</th>
                    <th>Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((i) => (
                    <tr key={i.id}>
                      <td>
                        <Link to={`/item/${encodeURIComponent(i.id)}`}>{i.title}</Link>
                        <div className="tiny faint mono">{i.id}</div>
                      </td>
                      <td className="tiny mono faint">{i.media_type}</td>
                      <td>
                        <StateBadge state={i.current_state} />
                      </td>
                      <td style={{ width: 130 }}>
                        {typeof i.progress === 'number' ? (
                          <div className="row">
                            <div className="bar" style={{ flex: 1 }}>
                              <i style={{ width: `${Math.min(100, i.progress)}%` }} />
                            </div>
                            <span className="tiny mono faint">{Math.round(i.progress)}%</span>
                          </div>
                        ) : (
                          <span className="faint">—</span>
                        )}
                      </td>
                      <td className="tiny mono faint">{i.current_service ?? '—'}</td>
                      <td className="tiny mono faint">{ago(i.last_event_at)}</td>
                      <td>
                        <EvidenceBadge boundary={i.evidence_boundary} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>
    </div>
  );
};

export default Media;

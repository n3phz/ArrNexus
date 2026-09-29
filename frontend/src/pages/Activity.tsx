import { useEffect, useState } from 'react';
import { api } from '../services/api';
import { ActivityStats, Event } from '../types';
import EmptyState from '../components/EmptyState';
import EvidenceBadge from '../components/EvidenceBadge';
import StateBadge from '../components/StateBadge';

const SOURCES = [
  { key: '', label: 'All services' },
  { key: 'sonarr', label: 'Sonarr' },
  { key: 'radarr', label: 'Radarr' },
  { key: 'qbittorrent', label: 'qBittorrent' },
  { key: 'prowlarr', label: 'Prowlarr' },
  { key: 'guardarr', label: 'Guardarr' },
];

function when(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

interface Props {
  refreshKey: number;
}

/** Activity — observation history across services, each row evidence-labelled. */
export const Activity: React.FC<Props> = ({ refreshKey }) => {
  const [events, setEvents] = useState<Event[]>([]);
  const [stats, setStats] = useState<ActivityStats | null>(null);
  const [source, setSource] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      api.getActivity({ limit: 100, offset: page * 100, days: 7, source_service: source, search }),
      api.getActivityStats(7),
    ])
      .then(([ev, st]) => {
        if (cancelled) return;
        setEvents(ev);
        setStats(st);
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load activity');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [page, source, search, refreshKey]);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1 className="page-title">Activity</h1>
          <p className="page-sub">
            Raw observations over the last 7 days. Events are recorded as observed; relationships
            between them are established elsewhere.
          </p>
        </div>
      </div>

      {stats && (
        <div className="stats">
          <div className="stat">
            <div className="stat-value">{stats.total}</div>
            <div className="stat-label">events / 7d</div>
          </div>
          <div className="stat">
            <div className="stat-value">{stats.today}</div>
            <div className="stat-label">today</div>
          </div>
          <div className="stat">
            <div className="stat-value" data-tone="danger">
              {stats.failed}
            </div>
            <div className="stat-label">failed</div>
          </div>
          <div className="stat">
            <div className="stat-value" data-tone="warn">
              {stats.stuck}
            </div>
            <div className="stat-label">stuck</div>
          </div>
        </div>
      )}

      <div className="filters">
        <select
          className="select"
          value={source}
          onChange={(e) => {
            setSource(e.target.value);
            setPage(0);
          }}
          aria-label="Filter by service"
        >
          {SOURCES.map((s) => (
            <option key={s.key} value={s.key}>
              {s.label}
            </option>
          ))}
        </select>
        <input
          className="input"
          placeholder="Search titles…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(0);
          }}
          aria-label="Search activity"
        />
      </div>

      <section className="panel">
        <div className="panel-body tight">
          {error ? (
            <EmptyState title="Cannot load activity" tone="error">
              {error}
            </EmptyState>
          ) : loading ? (
            <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div className="skeleton" />
              <div className="skeleton" />
              <div className="skeleton" />
            </div>
          ) : events.length === 0 ? (
            <EmptyState title="No activity recorded">
              No events match this filter. Events appear once services report state through polling
              or webhooks.
            </EmptyState>
          ) : (
            <div className="tbl-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Service</th>
                    <th>Title</th>
                    <th>Event</th>
                    <th>State</th>
                    <th>Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((e) => (
                    <tr key={e.id}>
                      <td className="tiny mono faint" style={{ whiteSpace: 'nowrap' }}>
                        {when(e.timestamp)}
                      </td>
                      <td className="tiny mono">{e.source_service}</td>
                      <td>{e.title || <span className="faint">—</span>}</td>
                      <td className="tiny mono">{e.event_type.replace(/_/g, ' ')}</td>
                      <td>
                        <StateBadge state={e.status} label={e.status} />
                      </td>
                      <td>
                        <EvidenceBadge boundary={e.evidence_boundary} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      <div className="row">
        <button className="btn" disabled={page === 0 || loading} onClick={() => setPage((p) => p - 1)}>
          Previous
        </button>
        <span className="tiny faint mono">page {page + 1}</span>
        <button
          className="btn"
          disabled={loading || events.length < 100}
          onClick={() => setPage((p) => p + 1)}
        >
          Next
        </button>
      </div>
    </div>
  );
};

export default Activity;

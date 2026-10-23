import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../services/api';
import { AttentionItem, Incident, IncidentStats } from '../types';
import EmptyState from '../components/EmptyState';
import IncidentCard, { IncidentAction } from '../components/IncidentCard';

type Filter = 'all' | 'OPEN' | 'ACKNOWLEDGED' | 'RESOLVED' | 'ARCHIVED';

const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'all' },
  { id: 'OPEN', label: 'open' },
  { id: 'ACKNOWLEDGED', label: 'acknowledged' },
  { id: 'RESOLVED', label: 'resolved' },
  { id: 'ARCHIVED', label: 'archived' },
];

const STATS_KEY: Record<Filter, keyof IncidentStats | null> = {
  all: null,
  OPEN: 'open',
  ACKNOWLEDGED: 'acknowledged',
  RESOLVED: 'resolved',
  ARCHIVED: 'archived',
};

interface Props {
  /** Bumped by the shared SSE stream so the view follows live conditions. */
  refreshKey: number;
}

interface Notice {
  tone: 'ok' | 'error';
  text: string;
}

/**
 * Incidents — tracked operational problems, kept distinct from Attention.
 *
 * Attention is the *current* view (recomputed from observed state); incidents
 * are the *tracked* view (a problem with a lifecycle an operator owns). This
 * page only ever shows what the incident API reports; lifecycle actions are
 * the backend's three valid transitions and nothing else.
 */
export const Incidents: React.FC<Props> = ({ refreshKey }) => {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [stats, setStats] = useState<IncidentStats | null>(null);
  const [attention, setAttention] = useState<AttentionItem[]>([]);
  const [filter, setFilter] = useState<Filter>('all');
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busyAction, setBusyAction] = useState<{ id: string; action: IncidentAction } | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setNotice(null);
    Promise.all([
      api.getIncidents(),
      api.getIncidentStats(),
      api.getAttention({ limit: 200 }).catch(() => [] as AttentionItem[]),
    ])
      .then(([list, st, att]) => {
        if (cancelled) return;
        setIncidents(list);
        setStats(st);
        setAttention(att);
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load incidents');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  // Conditions currently open in the live attention view, keyed the same way
  // incident ids are derived: correlation_key + attention_class.
  const activeKeys = useMemo(
    () => new Set(attention.map((a) => `${a.item.id}:${a.attention_class}`)),
    [attention],
  );

  const visible = useMemo(
    () => (filter === 'all' ? incidents : incidents.filter((i) => i.status === filter)),
    [incidents, filter],
  );

  const refreshAfterAction = useCallback(async () => {
    const [list, st] = await Promise.all([api.getIncidents(), api.getIncidentStats()]);
    setIncidents(list);
    setStats(st);
  }, []);

  const runAction = useCallback(
    async (incident: Incident, action: IncidentAction) => {
      setBusyAction({ id: incident.id, action });
      setNotice(null);
      const calls: Record<IncidentAction, () => Promise<Incident>> = {
        acknowledge: () => api.acknowledgeIncident(incident.id),
        resolve: () => api.resolveIncident(incident.id),
        archive: () => api.archiveIncident(incident.id),
      };
      try {
        await calls[action]();
        setNotice({ tone: 'ok', text: `Incident ${incident.attention_class} for ${incident.title ?? incident.correlation_key} — ${action} successful.` });
        await refreshAfterAction();
      } catch (e) {
        setNotice({
          tone: 'error',
          text: `${action[0].toUpperCase() + action.slice(1)} failed: ${e instanceof Error ? e.message : 'unexpected error'}`,
        });
        // The state may have moved underneath us (e.g. a recurrence reset it
        // to OPEN); refetch so the page shows the truth, not a stale row.
        await refreshAfterAction().catch(() => undefined);
      } finally {
        setBusyAction(null);
      }
    },
    [refreshAfterAction],
  );

  const syncNow = useCallback(async () => {
    setSyncing(true);
    try {
      const result = await api.syncIncidents();
      await refreshAfterAction();
      setNotice({
        tone: 'ok',
        text: `Sync complete: ${result.created} new, ${result.updated} updated, ${result.reset} recurred.`,
      });
    } catch (e) {
      setNotice({ tone: 'error', text: `Sync failed: ${e instanceof Error ? e.message : 'unexpected error'}` });
    } finally {
      setSyncing(false);
    }
  }, [refreshAfterAction]);

  if (error && incidents.length === 0) {
    return (
      <EmptyState title="Cannot reach the incident service" tone="error">
        {error}. The lifecycle of tracked incidents is unavailable while the API cannot be reached.
      </EmptyState>
    );
  }

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1 className="page-title">Incidents</h1>
          <p className="page-sub">
            Operational problems tracked through a lifecycle. Attention shows what needs attention
            now; an incident is the same kind of problem once an operator starts owning it.
          </p>
        </div>
        <div className="row-wrap">
          <button className="chip" aria-pressed={false} onClick={syncNow} disabled={syncing} title="Re-evaluate current attention into the incident store">
            {syncing ? 'Syncing…' : 'Sync now'}
          </button>
        </div>
      </div>

      {stats && (
        <div className="stats" aria-label="Incident lifecycle counts">
          <div className="stat">
            <div className="stat-value" data-tone={stats.open > 0 ? 'danger' : 'ok'}>
              {stats.open}
            </div>
            <div className="stat-label">Open</div>
          </div>
          <div className="stat">
            <div className="stat-value" data-tone={stats.acknowledged > 0 ? 'warn' : undefined}>
              {stats.acknowledged}
            </div>
            <div className="stat-label">Acknowledged</div>
          </div>
          <div className="stat">
            <div className="stat-value" data-tone="ok">
              {stats.resolved}
            </div>
            <div className="stat-label">Resolved</div>
          </div>
          <div className="stat">
            <div className="stat-value">{stats.archived}</div>
            <div className="stat-label">Archived</div>
          </div>
          <div className="stat">
            <div className="stat-value" data-tone={stats.active > 0 ? 'warn' : 'ok'}>
              {stats.active}
            </div>
            <div className="stat-label">Needs an operator</div>
          </div>
        </div>
      )}

      {stats?.last_sync_at && (
        <p className="tiny faint" style={{ margin: 0 }}>
          Incident store last synced {new Date(stats.last_sync_at).toLocaleString()} — incidents follow
          the polling cycle automatically.
        </p>
      )}

      {notice && (
        <div className={`inc-note ${notice.tone === 'error' ? 'inc-note-error' : ''}`} role="status">
          {notice.text}
        </div>
      )}

      <div className="filters" role="tablist" aria-label="Filter incidents by lifecycle state">
        {FILTERS.map((f) => {
          const statKey = STATS_KEY[f.id];
          return (
            <button
              key={f.id}
              className="chip"
              aria-pressed={filter === f.id}
              onClick={() => setFilter(f.id)}
            >
              {f.label}
              {statKey && stats && (
                <span className="faint mono" style={{ marginLeft: 6 }}>
                  {stats[statKey]}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {loading ? (
        <div className="stack">
          <div className="skeleton" style={{ height: 96 }} />
          <div className="skeleton" style={{ height: 96 }} />
        </div>
      ) : visible.length === 0 ? (
        <div className="panel">
          <EmptyState
            title={filter === 'all' ? 'No tracked incidents' : `No ${filter.toLowerCase()} incidents`}
            tone="ok"
          >
            {filter === 'all'
              ? 'Incidents appear when an attention condition is promoted to the lifecycle. Use “Sync now” or wait for the next polling cycle.'
              : 'Nothing in this lifecycle state right now.'}
          </EmptyState>
        </div>
      ) : (
        <div className="stack">
          {visible.map((incident) => (
            <IncidentCard
              key={incident.id}
              incident={incident}
              onAction={runAction}
              activeInAttention={activeKeys.has(`${incident.correlation_key}:${incident.attention_class}`)}
              busyAction={busyAction?.id === incident.id ? busyAction.action : null}
            />
          ))}
        </div>
      )}

      {stats && stats.active > 0 && (
        <p className="tiny faint">
          The live conditions behind open work are in <Link to="/attention">Attention</Link>; the media
          timelines are in <Link to="/media">Media</Link>.
        </p>
      )}
    </div>
  );
};

export default Incidents;

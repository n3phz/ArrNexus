import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../services/api';
import { AttentionItem, Incident, TimelineEvent } from '../types';
import EmptyState from '../components/EmptyState';
import EvidenceBadge from '../components/EvidenceBadge';
import { allowedActions, IncidentAction } from '../components/IncidentCard';
import StageBadge, { elapsed } from '../components/StageBadge';
import StateBadge from '../components/StateBadge';
import Timeline from '../components/Timeline';
import WhyBlock from '../components/WhyBlock';

function when(ts?: string | null): string {
  if (!ts) return '—';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}

interface Props {
  /** Bumped by the shared SSE stream so the detail view follows live conditions. */
  refreshKey: number;
}

/**
 * Incident detail — the operator's working surface for one tracked problem.
 *
 * Sections, in order of what they answer:
 *   Why?        the attention classification that created the incident
 *   Lifecycle   where it stands in OPEN -> ACKNOWLEDGED -> RESOLVED -> ARCHIVED
 *   Stage       the pipeline stage and its two recency axes at observation time
 *   Timeline    the media item's evidence chain (Incident -> Media -> Evidence)
 *
 * Nothing here is invented: fields that the API does not provide are not
 * rendered, and no causal story is told beyond the backend's own `reason`.
 */
export const IncidentDetail: React.FC<Props> = ({ refreshKey }) => {
  const { id } = useParams<{ id: string }>();
  const [incident, setIncident] = useState<Incident | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[] | null>(null);
  const [attention, setAttention] = useState<AttentionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [busyAction, setBusyAction] = useState<IncidentAction | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setLoading(true);
    setError(null);

    // The incident first: its correlation_key — not its lifecycle id — is
    // the identifier the item/timeline endpoints understand.
    api
      .getIncident(id)
      .then(async (inc) => {
        if (cancelled) return;
        const [tl, att] = await Promise.all([
          api.getItemTimeline(inc.correlation_key).catch(() => [] as TimelineEvent[]),
          api.getAttention({ limit: 200 }).catch(() => [] as AttentionItem[]),
        ]);
        if (cancelled) return;
        setIncident(inc);
        setTimeline(tl);
        setAttention(att);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : 'Failed to load incident');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [id, refreshKey]);

  const activeInAttention = useMemo(
    () =>
      incident !== null &&
      attention.some(
        (a) => a.item.id === incident.correlation_key && a.attention_class === incident.attention_class,
      ),
    [incident, attention],
  );

  const refreshAfterAction = useCallback(async () => {
    if (!id) return;
    const [inc] = await Promise.all([api.getIncident(id)]);
    setIncident(inc);
  }, [id]);

  const runAction = useCallback(
    async (incident: Incident, action: IncidentAction) => {
      setBusyAction(action);
      setNotice(null);
      const calls: Record<IncidentAction, () => Promise<Incident>> = {
        acknowledge: () => api.acknowledgeIncident(incident.id),
        resolve: () => api.resolveIncident(incident.id),
        archive: () => api.archiveIncident(incident.id),
      };
      try {
        const updated = await calls[action]();
        setIncident(updated);
        setNotice({ tone: 'ok', text: `${action} successful — lifecycle is now ${updated.status}.` });
      } catch (e) {
        setNotice({
          tone: 'error',
          text: `${action} failed: ${e instanceof Error ? e.message : 'unexpected error'}`,
        });
        await refreshAfterAction().catch(() => undefined);
      } finally {
        setBusyAction(null);
      }
    },
    [refreshAfterAction],
  );

  if (loading) {
    return (
      <div className="stack">
        <div className="skeleton" style={{ width: '40%', height: 26 }} />
        <div className="skeleton" style={{ height: 90 }} />
        <div className="skeleton" style={{ height: 200 }} />
      </div>
    );
  }

  if (error || !incident) {
    return (
      <div className="stack">
        <Link className="back" to="/incidents">
          ← Back to incidents
        </Link>
        <EmptyState title="Incident unavailable" tone="error">
          {error ?? 'This incident id is not in the store. It may never have been tracked.'}
        </EmptyState>
      </div>
    );
  }

  const itemKey = incident.correlation_key;
  const actions = allowedActions(incident.status);
  const ts: Record<string, string | null | undefined> = {
    first_observed: incident.first_observed_at,
    last_observed: incident.last_observed_at,
    last_evidence: incident.last_evidence_at,
    acknowledged: incident.acknowledged_at,
    resolved: incident.resolved_at,
    archived: incident.archived_at,
    recurred: incident.last_recurred_at,
  };

  return (
    <div className="stack">
      <Link className="back" to="/incidents">
        ← Back to incidents
      </Link>

      <header className="detail-head">
        <div>
          <h1 className="detail-title">{incident.title ?? itemKey}</h1>
          <p className="page-sub mono">{incident.id}</p>
        </div>
        <div className="row-wrap">
          <StateBadge state={incident.state ?? undefined} />
          <EvidenceBadge boundary={incident.evidence_boundary} solid showLabel />
        </div>
      </header>

      {notice && (
        <div className={`inc-note ${notice.tone === 'error' ? 'inc-note-error' : ''}`} role="status">
          {notice.text}
        </div>
      )}

      <WhyBlock
        reason={incident.reason ?? 'No explanation was recorded with this incident.'}
        evidence={incident.evidence}
        evidenceBoundary={incident.evidence_boundary}
        confidenceBasis={incident.confidence_basis ?? null}
        heading="Why this is an incident"
      />

      <section className="panel">
        <div className="panel-head">
          <span className="panel-title">Lifecycle</span>
          <span className="state inc-status" data-tone="warn" title="Only one transition is valid at a time">
            {incident.status.toLowerCase()}
          </span>
          {(incident.recurrence_count ?? 0) > 0 && (
            <span className="tag" title="This incident reset to open after resolution">
              recurred ×{incident.recurrence_count}
            </span>
          )}
        </div>
        <div className="panel-body tight">
          <div className="kv">
            {(
              [
                ['Opened', ts.first_observed],
                ['Last observed', ts.last_observed],
                ['Acknowledged', ts.acknowledged],
                ['Resolved', ts.resolved],
                ['Archived', ts.archived],
              ] as [string, string | null | undefined][]
            ).map(([label, value]) => (
              <div className="kv-item" key={label}>
                <div className="kv-label">{label}</div>
                <div className="kv-value mono small">{when(value)}</div>
              </div>
            ))}
          </div>
          <div className="row-wrap" style={{ marginTop: 12, gap: 8 }}>
            {actions.length === 0 ? (
              <span className="tiny faint">
                Archived — no further lifecycle action. The record stays queryable as history.
              </span>
            ) : (
              actions.map((a) => (
                <button
                  key={a}
                  className="btn btn-primary"
                  disabled={busyAction !== null}
                  onClick={() => runAction(incident, a)}
                >
                  {busyAction === a ? 'Working…' : a[0].toUpperCase() + a.slice(1)}
                </button>
              ))
            )}
            <Link className="btn" to={`/item/${encodeURIComponent(itemKey)}`}>
              Media item
            </Link>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <span className="panel-title">Stage at last observation</span>
          <StageBadge stage={incident.stage_freshness} evidence={incident.evidence_freshness} />
        </div>
        <div className="panel-body tight">
          <div className="kv">
            <div className="kv-item">
              <div className="kv-label">Pipeline state</div>
              <div className="kv-value">
                <StateBadge state={incident.state ?? undefined} />
              </div>
            </div>
            <div className="kv-item">
              <div className="kv-label">Time in stage</div>
              <div className="kv-value mono small">
                {typeof incident.stage_duration_seconds === 'number'
                  ? elapsed(incident.stage_duration_seconds)
                  : 'unknown (stage age not observed)'}
              </div>
            </div>
            <div className="kv-item">
              <div className="kv-label">Last evidence</div>
              <div className="kv-value mono small">
                {typeof incident.evidence_age_seconds === 'number'
                  ? `${elapsed(incident.evidence_age_seconds)} ago`
                  : 'not recorded'}
              </div>
            </div>
            <div className="kv-item">
              <div className="kv-label">Missing transition</div>
              <div className="kv-value">
                {incident.missing_transition ? (
                  <StateBadge state={incident.missing_transition} />
                ) : (
                  <span className="faint">none observed</span>
                )}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <span className="panel-title">Media item timeline</span>
          {activeInAttention && (
            <Link className="tag" to="/attention" title="This condition is currently in the live attention list">
              still active in attention →
            </Link>
          )}
        </div>
        <div className="panel-body tight">
          {timeline === null ? (
            <EmptyState title="Timeline unavailable" tone="error">
              The timeline could not be loaded for this media item.
            </EmptyState>
          ) : timeline.length === 0 ? (
            <EmptyState title="No observations">
              No events are recorded for media item{' '}
              <span className="mono">{itemKey}</span>. The incident is tracked from the attention
              evaluation; the item may have been evicted or never observed.
            </EmptyState>
          ) : (
            <>
              <Timeline events={timeline} />
              <p className="tiny faint" style={{ margin: '10px 0 0' }}>
                Full context — <Link to={`/item/${encodeURIComponent(itemKey)}`}>open the media item</Link>.
              </p>
            </>
          )}
        </div>
      </section>
    </div>
  );
};

export default IncidentDetail;

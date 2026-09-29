import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../services/api';
import { AttentionItem, AttentionStats, ActivityStats, Event, MediaItem, ServiceStatus } from '../types';
import AttentionCard from '../components/AttentionCard';
import EmptyState from '../components/EmptyState';
import EvidenceBadge from '../components/EvidenceBadge';
import ServicesPanel from '../components/ServicesPanel';
import StateBadge from '../components/StateBadge';

interface Props {
  onData: (attention: AttentionItem[]) => void;
}

function ago(ts?: string | null): string {
  if (!ts) return '—';
  const diff = Date.now() - new Date(ts).getTime();
  if (Number.isNaN(diff)) return '—';
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}

/** Overview: system health, what needs attention, what is happening now. */
export const Overview: React.FC<Props> = ({ onData }) => {
  const [stats, setStats] = useState<AttentionStats | null>(null);
  const [activity, setActivity] = useState<AttentionItem[]>([]);
  const [items, setItems] = useState<MediaItem[]>([]);
  const [services, setServices] = useState<ServiceStatus[]>([]);
  const [recent, setRecent] = useState<Event[]>([]);
  const [actStats, setActStats] = useState<ActivityStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const [att, attStats, media, svc, feed, aStats] = await Promise.all([
          api.getAttention({ limit: 8 }),
          api.getAttentionStats(),
          api.getItems({ limit: 200 }),
          api.getServices(),
          api.getActivity({ limit: 8 }),
          api.getActivityStats(1),
        ]);
        if (cancelled) return;
        setActivity(att);
        setStats(attStats);
        setItems(media);
        setServices(svc);
        setRecent(feed);
        setActStats(aStats);
        setError(null);
        onData(att);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load overview');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const inFlight = items.filter(
    (i) => !['available', 'download_completed', 'import_completed'].includes(i.current_state),
  );
  const active = inFlight.length;

  if (loading) {
    return (
      <div className="stack">
        <div className="skeleton" style={{ width: '30%', height: 24 }} />
        <div className="skeleton" style={{ width: '100%', height: 72 }} />
        <div className="skeleton" style={{ width: '100%', height: 180 }} />
      </div>
    );
  }

  if (error) {
    return (
      <EmptyState title="Control plane unreachable" tone="error">
        {error}. The interface cannot reach the API. Observations are blocked rather than failed —
        check the backend service.
      </EmptyState>
    );
  }

  return (
    <div className="stack">
      <div className="stats">
        <div className="stat">
          <div className="stat-value" data-tone={stats && stats.required > 0 ? 'danger' : 'ok'}>
            {stats?.required ?? 0}
          </div>
          <div className="stat-label">require attention</div>
        </div>
        <div className="stat">
          <div className="stat-value" data-tone="warn">
            {stats?.possible ?? 0}
          </div>
          <div className="stat-label">possible</div>
        </div>
        <div className="stat">
          <div className="stat-value" data-tone="blocked">
            {stats?.blocked ?? 0}
          </div>
          <div className="stat-label">blocked</div>
        </div>
        <div className="stat">
          <div className="stat-value">{active}</div>
          <div className="stat-label">in flight</div>
        </div>
        <div className="stat">
          <div className="stat-value">{actStats?.today ?? 0}</div>
          <div className="stat-label">events today</div>
        </div>
      </div>

      <div className="grid-2">
        <section className="panel">
          <div className="panel-head">
            <span className="panel-title">Attention</span>
            <Link className="btn" to="/attention">
              Open attention view
            </Link>
          </div>
          <div className="panel-body stack">
            {activity.length === 0 ? (
              <EmptyState title="No attention items">
                No operational issues currently require investigation.
              </EmptyState>
            ) : (
              activity.slice(0, 3).map((a) => <AttentionCard key={a.item.id} attention={a} />)
            )}
          </div>
        </section>

        <div className="stack">
          <section className="panel">
            <div className="panel-head">
              <span className="panel-title">Services</span>
            </div>
            <div className="panel-body tight">
              <ServicesPanel services={services} />
            </div>
          </section>

          <section className="panel">
            <div className="panel-head">
              <span className="panel-title">Recent observations</span>
              <Link className="btn" to="/activity">
                All activity
              </Link>
            </div>
            <div className="panel-body tight">
              {recent.length === 0 ? (
                <EmptyState title="No recent activity">
                  No events have been observed recently. This usually means the polling service is
                  not running or services are unreachable.
                </EmptyState>
              ) : (
                <div className="feed">
                  {recent.map((e) => (
                    <div className="feed-row" key={e.id}>
                      <span className="feed-time">{ago(e.timestamp)}</span>
                      <span className="chain-src">{e.source_service}</span>
                      <span className="feed-title">
                        {e.title || e.correlation_key} · {e.event_type.replace(/_/g, ' ')}
                      </span>
                      <EvidenceBadge boundary={e.evidence_boundary} />
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>
        </div>
      </div>

      <section className="panel">
        <div className="panel-head">
          <span className="panel-title">In flight</span>
          <Link className="btn" to="/media">
            All media
          </Link>
        </div>
        <div className="panel-body tight">
          {active === 0 ? (
            <EmptyState title="Nothing in flight">
              No media is currently moving through the pipeline.
            </EmptyState>
          ) : (
            <div className="tbl-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>State</th>
                    <th>Progress</th>
                    <th>Service</th>
                    <th>Last activity</th>
                    <th>Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {inFlight.slice(0, 10).map((i) => (
                    <tr key={i.id}>
                      <td>
                        <Link to={`/item/${encodeURIComponent(i.id)}`}>{i.title}</Link>
                      </td>
                      <td>
                        <StateBadge state={i.current_state} />
                      </td>
                      <td style={{ width: 120 }}>
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

export default Overview;

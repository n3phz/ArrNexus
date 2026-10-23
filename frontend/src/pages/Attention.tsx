import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../services/api';
import { AttentionItem, Incident } from '../types';
import AttentionCard from '../components/AttentionCard';
import EmptyState from '../components/EmptyState';

interface Props {
  onData: (attention: AttentionItem[]) => void;
  refreshKey: number;
}

/**
 * Attention view — the primary operational surface.
 * Grouped by what the operator must do next, with evidence attached.
 */
export const Attention: React.FC<Props> = ({ onData, refreshKey }) => {
  const [items, setItems] = useState<AttentionItem[]>([]);
  const [includeBlocked, setIncludeBlocked] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [incidents, setIncidents] = useState<Incident[]>([]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    api
      .getAttention({ include_blocked: includeBlocked, limit: 200 })
      .then((data) => {
        if (cancelled) return;
        setItems(data);
        setError(null);
        onData(data);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load attention items');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    // Active-incident tracking (OPEN/ACKNOWLEDGED only) for the
    // "tracked as incident" chips. Best-effort: a failure here must not
    // degrade the attention view itself.
    api
      .getIncidents()
      .then((all) => {
        if (cancelled) return;
        setIncidents(all.filter((i) => i.status === 'OPEN' || i.status === 'ACKNOWLEDGED'));
      })
      .catch(() => {
        if (!cancelled) setIncidents([]);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeBlocked, refreshKey]);

  const required = items.filter((i) => i.attention_level === 'required');
  const possible = items.filter((i) => i.attention_level === 'possible');
  const blocked = items.filter((i) => i.attention_level === 'blocked');

  // An attention condition and its tracked incident share (item, class).
  const trackedFor = (a: AttentionItem) =>
    incidents.find(
      (i) => i.correlation_key === a.item.id && i.attention_class === a.attention_class,
    );

  const Group = ({
    title,
    subtitle,
    rows,
  }: {
    title: string;
    subtitle: string;
    rows: AttentionItem[];
  }) => {
    if (rows.length === 0) return null;
    return (
      <section className="stack">
        <div>
          <h2 className="panel-title" style={{ marginBottom: 4 }}>
            {title} · {rows.length}
          </h2>
          <p className="tiny faint" style={{ margin: 0 }}>
            {subtitle}
          </p>
        </div>
        {rows.map((a) => (
          <AttentionCard key={a.item.id} attention={a} trackedIncident={trackedFor(a)} />
        ))}
      </section>
    );
  };

  if (error) {
    return (
      <EmptyState title="Cannot reach the control plane" tone="error">
        {error}. Prioritisation is unavailable while the API cannot be reached.
      </EmptyState>
    );
  }

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1 className="page-title">Attention</h1>
          <p className="page-sub">
            Items surfaced from observed state. Every conclusion is labelled with the evidence it rests
            on — age alone never raises an alert.
            <br />
            Problems you start owning are <Link to="/incidents">incidents</Link> — attention is the
            condition now, an incident is the tracked lifecycle.
          </p>
        </div>
        <button
          className="chip"
          aria-pressed={includeBlocked}
          onClick={() => setIncludeBlocked((v) => !v)}
          title="Show items whose evidence source is unavailable"
        >
          {includeBlocked ? 'Showing blocked' : 'Hiding blocked'}
        </button>
      </div>

      {loading ? (
        <div className="stack">
          <div className="skeleton" style={{ height: 96 }} />
          <div className="skeleton" style={{ height: 96 }} />
        </div>
      ) : items.length === 0 ? (
        <div className="panel">
          <EmptyState title="No attention items" tone="ok">
            No operational issues currently require investigation. Items appear here when a service
            reports a failure, a stalled download, or when evidence needed for a diagnosis is
            unavailable.
          </EmptyState>
        </div>
      ) : (
        <>
          <Group
            title="Attention required"
            subtitle="A failure or stall was directly observed, or the state machine classified the item as stuck."
            rows={required}
          />
          <Group
            title="Attention possible"
            subtitle="Conditions consistent with a problem, but no explicit failure signal was observed."
            rows={possible}
          />
          <Group
            title="Diagnosis blocked"
            subtitle="A required evidence source is unavailable, so the condition cannot be concluded either way."
            rows={blocked}
          />
        </>
      )}
    </div>
  );
};

export default Attention;

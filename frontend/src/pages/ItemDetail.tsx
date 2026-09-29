import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../services/api';
import { AttentionItem, Explanation, ItemDetail as ItemDetailData } from '../types';
import EmptyState from '../components/EmptyState';
import EvidenceBadge from '../components/EvidenceBadge';
import StateBadge from '../components/StateBadge';
import Timeline from '../components/Timeline';
import WhyBlock from '../components/WhyBlock';

function when(ts?: string | null): string {
  if (!ts) return '—';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString();
}

/** Item detail — evidence chain first, explanation grounded in that evidence. */
export const ItemDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [item, setItem] = useState<ItemDetailData | null>(null);
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [attention, setAttention] = useState<AttentionItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setLoading(true);

    Promise.all([
      api.getItem(id),
      api.getItemExplanation(id).catch(() => null),
      api.getAttention({ limit: 200 }).catch(() => [] as AttentionItem[]),
    ])
      .then(([it, exp, att]) => {
        if (cancelled) return;
        setItem(it as ItemDetailData);
        setExplanation(exp);
        setAttention(att.find((a) => a.item.id === id) ?? null);
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load item');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [id]);

  if (loading) {
    return (
      <div className="stack">
        <div className="skeleton" style={{ width: '40%', height: 26 }} />
        <div className="skeleton" style={{ height: 90 }} />
        <div className="skeleton" style={{ height: 200 }} />
      </div>
    );
  }

  if (error || !item) {
    return (
      <div className="stack">
        <Link className="back" to="/media">
          ← Back to media
        </Link>
        <EmptyState title="Item unavailable" tone="error">
          {error ?? 'This correlation key is not present in the control plane. It may have been evicted or never observed.'}
        </EmptyState>
      </div>
    );
  }

  const evidenceBoundary =
    attention?.evidence_boundary ?? item.evidence_boundary ?? explanation?.evidence_boundary;

  return (
    <div className="stack">
      <Link className="back" to="/media">
        ← Back to media
      </Link>

      <header className="detail-head">
        <div>
          <h1 className="detail-title">{item.title}</h1>
          <p className="page-sub mono">{item.id}</p>
        </div>
        <div className="row-wrap">
          <StateBadge state={item.current_state} />
          <EvidenceBadge boundary={evidenceBoundary} solid showLabel />
        </div>
      </header>

      {attention && attention.attention_level !== 'none' && (
        <WhyBlock
          reason={attention.reason}
          evidence={attention.evidence}
          evidenceBoundary={attention.evidence_boundary}
          confidenceBasis={attention.confidence_basis}
          heading="Operational status"
        />
      )}

      <section className="panel">
        <div className="panel-head">
          <span className="panel-title">State</span>
        </div>
        <div className="kv">
          <div className="kv-item">
            <div className="kv-label">Current state</div>
            <div className="kv-value">
              <StateBadge state={item.current_state} />
            </div>
          </div>
          <div className="kv-item">
            <div className="kv-label">Next expected</div>
            <div className="kv-value">
              {item.next_expected_state ? (
                <StateBadge state={item.next_expected_state} label={`${item.next_expected_state}`} />
              ) : (
                <span className="faint">no further state expected</span>
              )}
            </div>
          </div>
          <div className="kv-item">
            <div className="kv-label">Responsible service</div>
            <div className="kv-value mono">{item.current_service ?? '—'}</div>
          </div>
          <div className="kv-item">
            <div className="kv-label">Progress</div>
            <div className="kv-value">
              {typeof item.progress === 'number' ? (
                <div className="row">
                  <div className="bar" style={{ flex: 1 }}>
                    <i style={{ width: `${Math.min(100, item.progress)}%` }} />
                  </div>
                  <span className="mono">{Math.round(item.progress)}%</span>
                </div>
              ) : (
                <span className="faint">not reported</span>
              )}
            </div>
          </div>
          <div className="kv-item">
            <div className="kv-label">Correlation confidence</div>
            <div className="kv-value">
              {item.confidence ? (
                <>
                  <span className="mono">{item.confidence}</span>
                  {item.confidence_basis && (
                    <div className="tiny faint" style={{ marginTop: 2 }}>
                      {item.confidence_basis}
                    </div>
                  )}
                </>
              ) : (
                <span className="faint">not assessed</span>
              )}
            </div>
          </div>
          <div className="kv-item">
            <div className="kv-label">Last event</div>
            <div className="kv-value mono small">{when(item.last_event_at)}</div>
          </div>
        </div>
      </section>

      {explanation && (
        <WhyBlock
          reason={explanation.reason}
          evidence={explanation.evidence}
          evidenceBoundary={explanation.evidence_boundary ?? evidenceBoundary}
          confidenceBasis={explanation.confidence_basis}
        />
      )}

      {item.download_attempts && item.download_attempts.length > 0 && (
        <section className="panel">
          <div className="panel-head">
            <span className="panel-title">Download attempts</span>
          </div>
          <div className="panel-body tight">
            <div className="tbl-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Hash</th>
                    <th>State</th>
                    <th>Progress</th>
                    <th>Category</th>
                    <th>Events</th>
                    <th>Cross-seed</th>
                  </tr>
                </thead>
                <tbody>
                  {item.download_attempts.map((a) => (
                    <tr key={a.hash}>
                      <td className="tiny mono faint">{a.hash.slice(0, 16)}…</td>
                      <td>
                        <StateBadge state={a.state} />
                      </td>
                      <td className="mono tiny">{Math.round(a.progress)}%</td>
                      <td className="tiny mono faint">{a.category || '—'}</td>
                      <td className="tiny mono faint">{a.event_count}</td>
                      <td className="tiny">{a.is_cross_seed ? 'yes' : 'no'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      )}

      <section className="panel">
        <div className="panel-head">
          <span className="panel-title">Evidence chain</span>
          <span className="tiny faint mono">{item.timeline.length} observations</span>
        </div>
        <div className="panel-body tight">
          <Timeline events={item.timeline} />
        </div>
      </section>
    </div>
  );
};

export default ItemDetail;

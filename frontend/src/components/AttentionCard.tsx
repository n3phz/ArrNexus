import { Link } from 'react-router-dom';
import { AttentionItem } from '../types';
import EvidenceBadge from './EvidenceBadge';
import StateBadge from './StateBadge';

const LEVEL_LABEL: Record<string, string> = {
  required: 'Attention required',
  possible: 'Attention possible',
  blocked: 'Diagnosis blocked',
  synthetic: 'Synthetic',
  none: 'No attention',
};

const SEVERITY: Record<string, 'high' | 'medium' | 'low' | 'blocked'> = {
  high: 'high',
  medium: 'medium',
  low: 'low',
  none: 'low',
};

function sinceLabel(ts?: string | null): string {
  if (!ts) return 'no timestamp';
  const diff = Date.now() - new Date(ts).getTime();
  if (Number.isNaN(diff)) return 'unknown time';
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ${mins % 60}m ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

interface Props {
  attention: AttentionItem;
}

/**
 * One operational attention entry.
 * States WHAT needs attention, WHY, and on WHAT evidence — never more than that.
 */
export const AttentionCard: React.FC<Props> = ({ attention }) => {
  const { item } = attention;
  const severity =
    attention.attention_level === 'blocked'
      ? 'blocked'
      : SEVERITY[attention.severity ?? 'low'] ?? 'low';

  return (
    <article className="attn" data-sev={severity} aria-label={`${LEVEL_LABEL[attention.attention_level]}: ${item.title}`}>
      <div className="attn-rail" aria-hidden="true" />
      <div className="attn-body">
        <header className="attn-top">
          <span className="attn-title">
            <Link to={`/item/${encodeURIComponent(item.id)}`}>{item.title}</Link>
          </span>
          <span className="tiny faint mono">
            {item.media_type}
            {item.title.includes('S') && /S\d{2}E\d{2}/.test(item.title) ? '' : ''}
          </span>
          <StateBadge state={attention.state ?? item.current_state} />
          <EvidenceBadge boundary={attention.evidence_boundary} solid showLabel />
        </header>

        <div className="attn-why-label">{LEVEL_LABEL[attention.attention_level] ?? attention.attention_level}</div>
        <p className="attn-reason">{attention.reason}</p>

        {attention.evidence && attention.evidence.length > 0 && (
          <div className="attn-evidence">
            <div className="attn-why-label">Evidence</div>
            <ul>
              {attention.evidence.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
          </div>
        )}

        <footer className="attn-meta">
          <span title={item.last_event_at ?? undefined}>last activity {sinceLabel(item.last_event_at)}</span>
          {attention.confidence_basis && (
            <span className="mono" title="Why this correlation confidence was assigned">
              basis: {attention.confidence_basis}
            </span>
          )}
          <span className="mono">{item.event_count} events</span>
          <span className="spacer" />
          <Link className="btn" to={`/item/${encodeURIComponent(item.id)}`}>
            View timeline
          </Link>
        </footer>
      </div>
    </article>
  );
};

export default AttentionCard;

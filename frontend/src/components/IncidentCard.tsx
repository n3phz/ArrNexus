import { Link } from 'react-router-dom';
import { Incident } from '../types';
import EvidenceBadge from './EvidenceBadge';
import StageBadge, { elapsed } from './StageBadge';
import StateBadge from './StateBadge';

/**
 * Strict lifecycle mirror of the backend (services/incidents.py):
 *
 *   OPEN -> acknowledge -> ACKNOWLEDGED
 *   ACKNOWLEDGED -> resolve -> RESOLVED
 *   RESOLVED -> archive -> ARCHIVED   (terminal)
 *
 * The UI exposes exactly these actions and nothing else; the backend
 * rejects any other transition with 409, so surfacing extra buttons
 * would only produce errors.
 */
export type IncidentAction = 'acknowledge' | 'resolve' | 'archive';

export function allowedActions(status: Incident['status']): IncidentAction[] {
  switch (status) {
    case 'OPEN':
      return ['acknowledge'];
    case 'ACKNOWLEDGED':
      return ['resolve'];
    case 'RESOLVED':
      return ['archive'];
    case 'ARCHIVED':
      return [];
  }
}

const ACTION_LABEL: Record<IncidentAction, string> = {
  acknowledge: 'Acknowledge',
  resolve: 'Resolve',
  archive: 'Archive',
};

const STATUS_TONE: Record<Incident['status'], string> = {
  OPEN: 'warn',
  ACKNOWLEDGED: 'info',
  RESOLVED: 'ok',
  ARCHIVED: 'idle',
};

const STATUS_MEANING: Record<Incident['status'], string> = {
  OPEN: 'OPEN — not yet acknowledged; still needs an operator',
  ACKNOWLEDGED: 'ACKNOWLEDGED — an operator has taken ownership; not yet closed',
  RESOLVED: 'RESOLVED — closed by an operator; kept until archived',
  ARCHIVED: 'ARCHIVED — kept as history; no further lifecycle actions',
};

export function IncidentStatusChip({ status }: { status: Incident['status'] }) {
  return (
    <span
      className="state inc-status"
      data-tone={STATUS_TONE[status]}
      title={STATUS_MEANING[status]}
      aria-label={STATUS_MEANING[status]}
    >
      {status.toLowerCase()}
    </span>
  );
}

function whenLabel(ts?: string | null): string {
  if (!ts) return '—';
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
  incident: Incident;
  /** Lifecycle action; the page performs the API call and feedback. */
  onAction?: (incident: Incident, action: IncidentAction) => void;
  /** True when this incident's condition is currently in the live attention list. */
  activeInAttention?: boolean;
  busyAction?: IncidentAction | null;
}

/**
 * One tracked incident, scannable at list level.
 * Text carries the meaning; the rail colour is secondary, like AttentionCard.
 */
export const IncidentCard: React.FC<Props> = ({ incident, onAction, activeInAttention, busyAction }) => {
  const actions = allowedActions(incident.status);
  const key = incident.correlation_key;
  const sev =
    incident.status === 'OPEN'
      ? incident.severity === 'high'
        ? 'high'
        : incident.severity === 'medium'
          ? 'medium'
          : 'low'
      : 'low';

  return (
    <article className="inc" data-status={incident.status} data-sev={sev} aria-label={`Incident ${incident.attention_class} for ${incident.title ?? key}`}>
      <div className="inc-rail" aria-hidden="true" />
      <div className="inc-body">
        <header className="inc-top">
          <IncidentStatusChip status={incident.status} />
          <span className="inc-title">
            <Link to={`/item/${encodeURIComponent(key)}`}>{incident.title ?? key}</Link>
          </span>
          <span className="tiny faint mono">{incident.media_type ?? ''}</span>
          <StateBadge state={incident.state ?? undefined} />
          <EvidenceBadge boundary={incident.evidence_boundary} solid showLabel />
        </header>

        <div className="attn-why-label">
          {incident.attention_class?.replace(/_/g, ' ').toLowerCase()}
          {activeInAttention ? ' · still active in attention' : ''}
        </div>
        {incident.reason && <p className="attn-reason">{incident.reason}</p>}

        <div className="attn-tags">
          {activeInAttention && (
            <Link className="tag" to="/attention" title="This condition is currently in the live attention list">
              in attention →
            </Link>
          )}
          {typeof incident.stage_duration_seconds === 'number' && (
            <span className="tag" title="Time in the pipeline stage when the incident was last observed">
              in stage {elapsed(incident.stage_duration_seconds)}
            </span>
          )}
          {incident.missing_transition && (
            <span className="tag" title="The transition the pipeline is waiting for">
              waiting for {String(incident.missing_transition).replace(/_/g, ' ')}
            </span>
          )}
          {typeof incident.evidence_age_seconds === 'number' && (
            <span className="tag" title="Time since the media item was last observed">
              last seen {elapsed(incident.evidence_age_seconds)} ago
            </span>
          )}
          <StageBadge stage={incident.stage_freshness} evidence={incident.evidence_freshness} />
          {(incident.recurrence_count ?? 0) > 0 && (
            <span
              className="tag"
              title={`This incident recurred ${incident.recurrence_count} time(s) after resolution and reset to open`}
            >
              recurred ×{incident.recurrence_count}
            </span>
          )}
        </div>

        {incident.evidence && incident.evidence.length > 0 && (
          <div className="attn-evidence">
            <div className="attn-why-label">Evidence</div>
            <ul>
              {incident.evidence.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
          </div>
        )}

        <footer className="inc-meta">
          <span title="First time this condition was tracked">opened {whenLabel(incident.first_observed_at)}</span>
          <span title="Most recent observation of the condition">seen {whenLabel(incident.last_observed_at)}</span>
          <span className="spacer" />
          {actions.length === 0 && <span className="tiny faint">archived — history only</span>}
          {actions.map((a) => (
            <button
              key={a}
              className="btn btn-primary"
              disabled={busyAction !== null || !onAction}
              onClick={() => onAction?.(incident, a)}
              title={
                a === 'acknowledge'
                  ? 'Mark as seen: an operator has taken this problem'
                  : a === 'resolve'
                    ? 'Close the problem: the underlying condition has been handled'
                    : 'Move to history: keep the record, stop tracking'
              }
            >
              {busyAction === a ? 'Working…' : ACTION_LABEL[a]}
            </button>
          ))}
          <Link className="btn" to={`/item/${encodeURIComponent(key)}`}>
            Timeline
          </Link>
        </footer>
      </div>
    </article>
  );
};

export default IncidentCard;

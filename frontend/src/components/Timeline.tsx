import { TimelineEvent } from '../types';
import EvidenceBadge from './EvidenceBadge';
import EmptyState from './EmptyState';

interface Props {
  events: TimelineEvent[];
}

function timeOf(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function parseMeta(meta?: Record<string, any> | string): Record<string, any> | null {
  if (!meta) return null;
  if (typeof meta === 'string') {
    try {
      return JSON.parse(meta);
    } catch {
      return null;
    }
  }
  return meta;
}

/**
 * Evidence chain. Reads as a chain of observations, each carrying its own
 * evidence boundary, rather than as a generic activity feed.
 */
export const Timeline: React.FC<Props> = ({ events }) => {
  if (!events || events.length === 0) {
    return (
      <EmptyState title="No events recorded">
        No observations have been captured for this item yet. The timeline fills in as services
        report state.
      </EmptyState>
    );
  }

  const ordered = [...events].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
  );

  return (
    <div className="chain" role="list">
      {ordered.map((e, i) => {
        const meta = parseMeta(e.normalized_metadata);
        const ingestion = e.ingestion ?? meta?.ingestion;
        return (
          <div className="chain-row" role="listitem" key={`${e.timestamp}-${i}`}>
            <span className="chain-time" title={new Date(e.timestamp).toLocaleString()}>
              {timeOf(e.timestamp)}
            </span>
            <span className="chain-src">
              {e.source}
              {ingestion && (
                <span className="faint" title={`Ingested via ${ingestion}`}>
                  {' '}
                  / {ingestion}
                </span>
              )}
            </span>
            <span className="chain-event">{e.event_type.replace(/_/g, ' ')}</span>
            <EvidenceBadge boundary={e.evidence_boundary} solid />
            {e.error_message && <div className="chain-err">{e.error_message}</div>}
          </div>
        );
      })}
    </div>
  );
};

export default Timeline;

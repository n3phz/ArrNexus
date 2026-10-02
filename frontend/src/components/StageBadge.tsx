import { EvidenceFreshness, StageFreshness } from '../types';

const STAGE_MEANING: Record<StageFreshness, string> = {
  RECENT: 'RECENT — the current stage is within its expected duration',
  STALE: 'STALE — the current stage has run past its expected duration',
  UNKNOWN: 'UNKNOWN — no expected duration applies, or the stage age is unknown',
};

const EVIDENCE_MEANING: Record<EvidenceFreshness, string> = {
  CURRENT: 'CURRENT — this item was observed within the last hour',
  AGED: 'AGED — the last observation is between 1 and 24 hours old',
  STALE: 'STALE — the last observation is more than a day old; conclusions here rest on historical data',
};

interface Props {
  stage?: StageFreshness;
  evidence?: EvidenceFreshness;
  /** Hide the stage chip when only evidence recency is interesting. */
  stageOnly?: boolean;
}

/**
 * Recency indicator for the two independent Phase 4C axes.
 *
 * Stage freshness answers "is this pipeline step overdue?" while evidence
 * freshness answers "how long since we last heard anything?". They are shown
 * as separate chips because they routinely disagree: an item can be overdue in
 * its stage and still be reporting fresh evidence every poll.
 *
 * Text carries the meaning; colour is secondary.
 */
export const StageBadge: React.FC<Props> = ({ stage, evidence, stageOnly }) => {
  if (!stage && !evidence) return null;
  return (
    <>
      {stage && (
        <span className="stage-chip" data-fresh={stage} title={STAGE_MEANING[stage]} aria-label={STAGE_MEANING[stage]}>
          <span className="faint">stage</span> {stage.toLowerCase()}
        </span>
      )}
      {!stageOnly && evidence && (
        <span
          className="stage-chip"
          data-fresh={evidence}
          title={EVIDENCE_MEANING[evidence]}
          aria-label={EVIDENCE_MEANING[evidence]}
        >
          <span className="faint">evidence</span> {evidence.toLowerCase()}
        </span>
      )}
    </>
  );
};

/** Human-readable elapsed time from a seconds count. */
export function elapsed(seconds?: number | null): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—';
  const abs = Math.max(0, seconds);
  if (abs < 60) return `${Math.round(abs)}s`;
  const mins = abs / 60;
  if (mins < 60) return `${Math.round(mins)}m`;
  const hrs = mins / 60;
  if (hrs < 48) return `${hrs.toFixed(1)}h`;
  return `${(hrs / 24).toFixed(1)}d`;
}

export default StageBadge;

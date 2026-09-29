import { EvidenceBoundary } from '../types';

const LETTER: Record<EvidenceBoundary, string> = {
  OBSERVED: 'O',
  CORRELATED: 'C',
  INFERRED: 'I',
  UNKNOWN: 'U',
  BLOCKED: 'B',
  SYNTHETIC: 'S',
};

const MEANING: Record<EvidenceBoundary, string> = {
  OBSERVED: 'OBSERVED — directly observed from an external service',
  CORRELATED: 'CORRELATED — relationship established by joining multiple observations',
  INFERRED: 'INFERRED — derived conclusion; the underlying link is not directly observed',
  UNKNOWN: 'UNKNOWN — insufficient evidence to establish a conclusion',
  BLOCKED: 'BLOCKED — required evidence is unavailable; diagnosis cannot proceed',
  SYNTHETIC: 'SYNTHETIC — test or synthetic data, not from the live environment',
};

interface Props {
  boundary?: EvidenceBoundary;
  solid?: boolean;
  showLabel?: boolean;
}

/**
 * Compact evidence-boundary indicator.
 * Letter + accessible title, never colour alone.
 */
export const EvidenceBadge: React.FC<Props> = ({ boundary, solid, showLabel }) => {
  if (!boundary) return null;
  return (
    <span
      className={solid ? 'ev ev-solid' : 'ev'}
      data-ev={boundary}
      title={MEANING[boundary]}
      aria-label={MEANING[boundary]}
    >
      <span className="ev-letter" aria-hidden="true">
        {LETTER[boundary]}
      </span>
      {showLabel && <span aria-hidden="true">{boundary.toLowerCase()}</span>}
    </span>
  );
};

export default EvidenceBadge;

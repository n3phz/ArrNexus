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

/**
 * The API serializes EvidenceBoundary with its lowercase enum value
 * ("observed", "blocked", …) while the design system and CSS speak uppercase
 * ("OBSERVED", …). Normalising at this display boundary keeps the wire format
 * untouched — evidence semantics are a backend concern, casing is not.
 */
export function normalizeBoundary(boundary?: string): EvidenceBoundary | undefined {
  if (!boundary) return undefined;
  const upper = boundary.toUpperCase() as EvidenceBoundary;
  return upper in LETTER ? upper : undefined;
}

interface Props {
  /** Accepts the wire value (lowercase) or the type value (uppercase). */
  boundary?: EvidenceBoundary | string;
  solid?: boolean;
  showLabel?: boolean;
}

/**
 * Compact evidence-boundary indicator.
 * Letter + accessible title, never colour alone.
 */
export const EvidenceBadge: React.FC<Props> = ({ boundary, solid, showLabel }) => {
  const value = normalizeBoundary(boundary);
  if (!value) return null;
  return (
    <span
      className={solid ? 'ev ev-solid' : 'ev'}
      data-ev={value}
      title={MEANING[value]}
      aria-label={MEANING[value]}
    >
      <span className="ev-letter" aria-hidden="true">
        {LETTER[value]}
      </span>
      {showLabel && <span aria-hidden="true">{value.toLowerCase()}</span>}
    </span>
  );
};

export default EvidenceBadge;

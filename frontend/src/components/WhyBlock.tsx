import { EvidenceBoundary } from '../types';
import EvidenceBadge from './EvidenceBadge';

interface Props {
  reason: string;
  evidence?: string[];
  evidenceBoundary?: EvidenceBoundary;
  confidenceBasis?: string | null;
  heading?: string;
}

/**
 * WHY block. Deterministic and traceable — never generic prose.
 * The evidence boundary drives both wording and accent.
 */
export const WhyBlock: React.FC<Props> = ({
  reason,
  evidence,
  evidenceBoundary,
  confidenceBasis,
  heading = 'Why',
}) => {
  const headingFor = (ev?: EvidenceBoundary) => {
    if (ev === 'BLOCKED') return 'Why diagnosis is blocked';
    if (ev === 'UNKNOWN') return 'Why this is unknown';
    if (ev === 'INFERRED') return 'Why (inferred)';
    if (ev === 'SYNTHETIC') return 'Why (synthetic data)';
    return heading;
  };

  return (
    <section className="why" data-ev={evidenceBoundary} aria-label={headingFor(evidenceBoundary)}>
      <div className="why-label">{headingFor(evidenceBoundary)}</div>
      <p className="why-text">{reason}</p>

      {evidence && evidence.length > 0 && (
        <div className="why-evidence">
          <div className="why-label">Supporting evidence</div>
          <ul>
            {evidence.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="row-wrap" style={{ marginTop: 12 }}>
        <span className="attn-why-label" style={{ margin: 0 }}>
          Evidence
        </span>
        <EvidenceBadge boundary={evidenceBoundary} solid showLabel />
        {confidenceBasis && (
          <span className="tiny faint mono" title="Why this correlation confidence was assigned">
            basis: {confidenceBasis}
          </span>
        )}
      </div>
    </section>
  );
};

export default WhyBlock;

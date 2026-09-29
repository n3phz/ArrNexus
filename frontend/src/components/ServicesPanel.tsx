import { ServiceStatus } from '../types';
import EmptyState from './EmptyState';

const ORDER = ['sonarr', 'radarr', 'qbittorrent', 'prowlarr', 'guardarr'];

const NOTE: Record<string, string> = {
  healthy: 'connected',
  degraded: 'degraded response',
  down: 'unavailable — evidence blocked',
};

interface Props {
  services: ServiceStatus[];
}

/**
 * Service health. An unavailable integration is reported as blocked evidence,
 * never as a media failure.
 */
export const ServicesPanel: React.FC<Props> = ({ services }) => {
  if (!services || services.length === 0) {
    return (
      <EmptyState title="Service status unavailable">
        The control plane could not reach any integration. Observations are blocked, not failing.
      </EmptyState>
    );
  }

  const byName = new Map(services.map((s) => [s.service, s]));
  const rows = ORDER.map((name) => byName.get(name)).filter(Boolean) as ServiceStatus[];

  return (
    <div className="svc-list">
      {rows.map((s) => {
        const status = (s.status || 'unknown').toLowerCase();
        const note = s.error ? `evidence blocked: ${s.error}` : NOTE[status] ?? status;
        return (
          <div className="svc" data-status={status} key={s.service}>
            <span className="svc-dot" aria-hidden="true" />
            <span className="svc-name">{s.service}</span>
            <span className="tiny faint mono">{status}</span>
            {s.version && <span className="tiny faint mono">v{s.version}</span>}
            <span className="svc-note" title={note}>
              {note}
            </span>
          </div>
        );
      })}
    </div>
  );
};

export default ServicesPanel;

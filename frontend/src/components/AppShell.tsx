import { NavLink } from 'react-router-dom';

const LINKS = [
  { to: '/', label: 'Overview', end: true },
  { to: '/attention', label: 'Attention', end: false },
  { to: '/media', label: 'Media', end: false },
  { to: '/activity', label: 'Activity', end: false },
];

interface Props {
  attentionCount: number;
  live: boolean;
  onRefresh: () => void;
  refreshing?: boolean;
  children: React.ReactNode;
}

/**
 * Application shell: identity, live-connection state, compact navigation.
 */
export const AppShell: React.FC<Props> = ({ attentionCount, live, onRefresh, refreshing, children }) => (
  <div className="shell">
    <header className="shell-header">
      <div className="shell-header-row">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span>MEDIA CONTROL PLANE</span>
          <span className="brand-sub">evidence-first</span>
        </div>

        <div className="shell-actions">
          <span
            className="live-chip"
            data-live={live}
            title={
              live
                ? 'Streaming connection to the control plane is open. Polling remains the source of truth for external services.'
                : 'Streaming connection is closed. The interface falls back to periodic polling.'
            }
          >
            <span className="live-dot" aria-hidden="true" />
            {live ? 'stream open' : 'stream closed'}
          </span>
          <button className="btn" onClick={onRefresh} disabled={refreshing}>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      <nav className="nav" aria-label="Primary">
        {LINKS.map((l) => (
          <NavLink key={l.to} to={l.to} end={l.end} className={({ isActive }) => (isActive ? 'active' : '')}>
            {l.label}
            {l.to === '/attention' && attentionCount > 0 && (
              <span className="nav-count" aria-label={`${attentionCount} items requiring attention`}>
                {attentionCount}
              </span>
            )}
          </NavLink>
        ))}
      </nav>
    </header>

    <main className="shell-main">{children}</main>
  </div>
);

export default AppShell;

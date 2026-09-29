interface Props {
  title: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
  tone?: 'ok' | 'error' | 'default';
}

/** Explicit empty / error / blocked state — never a blank panel. */
export const EmptyState: React.FC<Props> = ({ title, children, action, tone = 'default' }) => (
  <div className="state-box" data-tone={tone}>
    <h3>{title}</h3>
    {children && <p>{children}</p>}
    {action && <div style={{ marginTop: 14 }}>{action}</div>}
  </div>
);

export default EmptyState;

type EvidenceBoundaryValue = 'OBSERVED' | 'CORRELATED' | 'INFERRED' | 'UNKNOWN' | 'BLOCKED' | 'SYNTHETIC';

interface TimelineProps {
  events: Array<{
    timestamp: string;
    source: string;
    event_type: string;
    error_message?: string;
    normalized_metadata?: Record<string, any>;
    ingestion?: 'webhook' | 'polling';
    evidence_boundary?: EvidenceBoundaryValue;
  }>;
}

const EVIDENCE_TOOLTIPS: Record<EvidenceBoundaryValue, string> = {
  OBSERVED: 'Directly observed from an external service',
  CORRELATED: 'Relationship established from observed evidence',
  INFERRED: 'Derived conclusion based on available evidence',
  UNKNOWN: 'Insufficient evidence to establish a conclusion',
  BLOCKED: 'Required evidence could not be obtained (external dependency unavailable)',
  SYNTHETIC: 'Test/synthetic data, not from live environment',
};

const EVIDENCE_COLORS: Record<EvidenceBoundaryValue, string> = {
  OBSERVED: '#00b894',
  CORRELATED: '#fdcb6e',
  INFERRED: '#74b9ff',
  UNKNOWN: '#d63031',
  BLOCKED: '#636e72',
  SYNTHETIC: '#8e44ad',
};

export const Timeline: React.FC<TimelineProps> = ({ events }) => {
  const formatTime = (timestamp: string) => {
    try {
      const date = new Date(timestamp);
      return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    } catch {
      return timestamp;
    }
  };

  const getEvidenceBadge = (boundary?: EvidenceBoundaryValue) => {
    if (!boundary) return null;
    return (
      <span
        style={{
          marginLeft: '0.5rem',
          padding: '0.125rem 0.375rem',
          borderRadius: '0.25rem',
          fontSize: '0.65rem',
          fontWeight: 'bold',
          background: EVIDENCE_COLORS[boundary],
          color: '#fff',
          textTransform: 'uppercase',
          cursor: 'help',
          border: 'none',
        }}
        title={EVIDENCE_TOOLTIPS[boundary] || boundary}
      >
        {boundary.charAt(0)}
      </span>
    );
  };

  const getEventIcon = (eventType: string) => {
    const icons: Record<string, string> = {
      wanted: 'W',
      search_started: 'S',
      release_grabbed: 'G',
      download_started: '↓',
      download_progress: '↓',
      download_completed: '✓',
      download_failed: '✗',
      import_started: 'I',
      import_completed: '✓',
      import_failed: '✗',
      available: '✓',
      stuck: '⏸',
      webhook_test: 'T',
      application_update: 'U',
      health_issue: '⚠',
      health_restored: '✓',
      indexer_search: '🔍',
      indexer_search_completed: '✓',
      indexer_search_failed: '✗',
      release_rejected: '⊘',
      storage_estimate: '📊',
      storage_admit: '✅',
      storage_release: '🔓',
      storage_reconcile: '🔄',
      security_scan: '🔍',
      security_allowed: '✅',
      security_blocked: '🚫',
      security_quarantined: '🔒',
      security_alert: '⚠',
      torrent_associated: '🔗',
      torrent_unreserved: '❌',
    };
    return icons[eventType] || '•';
  };

  const getEventColor = (eventType: string) => {
    const colors: Record<string, string> = {
      wanted: '#3742fa',
      search_started: '#5f27cd',
      release_grabbed: '#0abde3',
      download_started: '#10ac84',
      download_progress: '#00b894',
      download_completed: '#00cec9',
      download_failed: '#d63031',
      import_started: '#fdcb6e',
      import_completed: '#00b894',
      import_failed: '#e17055',
      available: '#6c5ce7',
      stuck: '#636e72',
      webhook_test: '#74b9ff',
      application_update: '#a29bfe',
      health_issue: '#d63031',
      health_restored: '#00b894',
      indexer_search: '#55efc4',
      indexer_search_completed: '#00b894',
      indexer_search_failed: '#d63031',
      release_rejected: '#fd79a8',
      storage_estimate: '#74b9ff',
      storage_admit: '#00b894',
      storage_release: '#fdcb6e',
      storage_reconcile: '#a29bfe',
      security_scan: '#74b9ff',
      security_allowed: '#00b894',
      security_blocked: '#d63031',
      security_quarantined: '#e17055',
      security_alert: '#d63031',
      torrent_associated: '#0abde3',
      torrent_unreserved: '#636e72',
    };
    return colors[eventType] || '#00d4ff';
  };

  const getIngestionBadge = (ingestion?: 'webhook' | 'polling') => {
    if (!ingestion) return null;
    return (
      <span
        style={{
          marginLeft: '0.5rem',
          padding: '0.125rem 0.375rem',
          borderRadius: '0.25rem',
          fontSize: '0.65rem',
          fontWeight: 'bold',
          background: ingestion === 'webhook' ? '#00b894' : '#74b9ff',
          color: '#fff',
          textTransform: 'uppercase',
        }}
      >
        {ingestion}
      </span>
    );
  };

  return (
    <div className="timeline">
      <h3>Timeline</h3>
      {events.length === 0 ? (
        <p style={{ color: '#666', fontSize: '0.875rem' }}>No events recorded</p>
      ) : (
        events.map((event, index) => (
          <div key={index} className="timeline-event">
            <span className="timeline-time">{formatTime(event.timestamp)}</span>
            <div
              className="timeline-icon"
              style={{ background: getEventColor(event.event_type), color: '#fff' }}
            >
              {getEventIcon(event.event_type)}
            </div>
            <div className="timeline-content">
              <div className="event-type">
                {event.event_type.replace(/_/g, ' ')}
                {getEvidenceBadge(event.evidence_boundary)}
                {getIngestionBadge(event.ingestion)}
              </div>
              <div className="event-source">{event.source}</div>
              {event.error_message && (
                <div className="error-message">{event.error_message}</div>
              )}
              {event.normalized_metadata && (
                <details style={{ marginTop: '0.5rem', fontSize: '0.75rem' }}>
                  <summary style={{ color: '#888', cursor: 'pointer' }}>Metadata</summary>
                  <pre style={{ marginTop: '0.25rem', whiteSpace: 'pre-wrap' }}>
                    {JSON.stringify(event.normalized_metadata, null, 2)}
                  </pre>
                </details>
              )}
            </div>
          </div>
        ))
      )}
    </div>
  );
};

export default Timeline;

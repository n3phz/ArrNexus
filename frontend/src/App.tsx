import { useState, useEffect, useRef } from 'react';
import { api } from './services/api';
import { MediaItem, ServiceStatus, SseEvent } from './types';
import SummaryCards from './components/SummaryCards';
import PipelineList from './components/PipelineList';
import ServiceStatusBadge from './components/ServiceStatus';

function App() {
  const [items, setItems] = useState<MediaItem[]>([]);
  const [services, setServices] = useState<ServiceStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sseConnected, setSseConnected] = useState(false);
  const sseEventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 30000);
    return () => {
      clearInterval(interval);
      sseEventSourceRef.current?.close();
    };
  }, []);

  // Setup SSE connection
  useEffect(() => {
    const connectSSE = () => {
      const eventSource = new EventSource('/api/events/stream');
      sseEventSourceRef.current = eventSource;

      eventSource.addEventListener('connected', (event: MessageEvent) => {
        setSseConnected(true);
        console.log('SSE connected:', event.data);
      });

      eventSource.addEventListener('media_event', (event: MessageEvent) => {
        const sseEvent: SseEvent = JSON.parse(event.data);
        console.log('Received SSE event:', sseEvent);
        // Handle incoming events - could update specific items or show notifications
        // For now, just log and optionally refresh items
        setItems(prev => {
          // Avoid duplicate updates by checking if event already exists
          const exists = prev.some(item => item.id === sseEvent.media_id);
          if (exists) return prev;
          return [...prev, { id: sseEvent.media_id || '', ...sseEvent } as MediaItem];
        });
      });

      eventSource.addEventListener('heartbeat', () => {
        // Heartbeat received, connection is alive
      });

      eventSource.addEventListener('error', (event: any) => {
        console.error('SSE error:', event);
        setSseConnected(false);
        // Attempt reconnect after 5 seconds
        setTimeout(connectSSE, 5000);
      });
    };

    connectSSE();

    return () => {
      sseEventSourceRef.current?.close();
    };
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      const [itemsData, servicesData] = await Promise.all([
        api.getItems({ limit: 100 }),
        api.getServices()
      ]);
      setItems(itemsData);
      setServices(servicesData);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load data');
    } finally {
      setLoading(false);
    }
  };

  const handleItemSelect = (id: string) => {
    window.location.href = `/item/${id}`;
  };

  const summary = {
    active: items.filter(i => !['available', 'download_completed', 'import_completed'].includes(i.current_state)).length,
    attention: items.filter(i => ['download_failed', 'import_failed', 'stuck'].includes(i.current_state)).length,
    completedToday: items.filter(i => i.current_state === 'available').length,
    failed: items.filter(i => ['download_failed', 'import_failed'].includes(i.current_state)).length,
  };

  if (error) {
    return (
      <div className="error">
        <h2>Error</h2>
        <p>{error}</p>
        <button onClick={loadData} style={{ marginTop: '1rem', padding: '0.5rem 1rem', background: '#00d4ff', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="header">
        <h1>ARR CONTROL</h1>
        <button onClick={loadData} style={{ padding: '0.5rem 1rem', background: '#00d4ff', border: 'none', borderRadius: '4px', cursor: 'pointer', fontWeight: '500' }}>
          Refresh
        </button>
      </header>

      {loading ? (
        <div className="loading">Loading...</div>
      ) : (
        <>
          <SummaryCards {...summary} />

          <section className="pipeline-section">
            <h2 className="section-title">Active Pipeline</h2>
            <PipelineList items={items.map(i => ({...i, state: i.current_state}))} onItemSelect={handleItemSelect} />
          </section>

          {summary.attention > 0 && (
            <section className="attention-section">
              <h2 className="section-title">Attention Required</h2>
              {items
                .filter(i => ['download_failed', 'import_failed', 'stuck'].includes(i.current_state))
                .map(item => (
                  <div key={item.id} className="attention-item">
                    <div>
                      <span className="title">{item.title}</span>
                      {item.season && item.episode && (
                        <span style={{ color: '#888', marginLeft: '0.5rem', fontSize: '0.875rem' }}>
                          S{item.season.toString().padStart(2, '0')}E{item.episode.toString().padStart(2, '0')}
                        </span>
                      )}
                    </div>
                    <span className="type">{item.current_state.replace(/_/g, ' ')}</span>
                  </div>
                ))
              }
            </section>
          )}

          <section className="services-section">
            <h2 className="section-title">Services</h2>
            <div className="services-list">
              {services.map(service => (
                <ServiceStatusBadge key={service.service} status={service.status} serviceName={service.service} />
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

export default App;
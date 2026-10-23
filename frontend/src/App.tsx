import { useCallback, useEffect, useState } from 'react';
import { Route, Routes } from 'react-router-dom';
import { api } from './services/api';
import { AttentionItem } from './types';
import AppShell from './components/AppShell';
import Overview from './pages/Overview';
import Attention from './pages/Attention';
import Media from './pages/Media';
import Activity from './pages/Activity';
import ItemDetailPage from './pages/ItemDetail';
import Incidents from './pages/Incidents';
import IncidentDetailPage from './pages/IncidentDetail';

/**
 * Root composition: shared attention state, live-update plumbing, routing.
 *
 * SSE is a delivery mechanism only — external services are still observed by
 * the polling service, and REST remains the source of truth for rendering.
 */
function App() {
  const [attention, setAttention] = useState<AttentionItem[]>([]);
  const [live, setLive] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [incidentCount, setIncidentCount] = useState(0);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      setRefreshKey((k) => k + 1);
    } finally {
      setTimeout(() => setRefreshing(false), 400);
    }
  }, []);

  // Establish the event stream. On any inbound event we invalidate the REST
  // queries rather than splicing data into local state, so the UI can never
  // drift from the backend's view of the world.
  useEffect(() => {
    let closed = false;
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (closed) return;
      source = new EventSource('/api/events/stream');

      source.addEventListener('connected', () => {
        if (!closed) setLive(true);
      });

      source.addEventListener('media_event', () => {
        if (!closed) setRefreshKey((k) => k + 1);
      });

      source.addEventListener('error', () => {
        if (closed) return;
        setLive(false);
        source?.close();
        // EventSource retries on its own, but it stops after the connection
        // is closed by the server; schedule an explicit reconnect.
        retry = setTimeout(connect, 5000);
      });
    };

    connect();

    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      source?.close();
    };
  }, []);

  // Keep the nav counters current even when not on those pages.
  // The incident counter is the tracked-work figure (open + acknowledged);
  // attention items are recomputed live and are not the same population.
  useEffect(() => {
    if (refreshKey === 0) return;
    let cancelled = false;
    api
      .getAttention({ limit: 200 })
      .then((data) => {
        if (!cancelled) setAttention(data);
      })
      .catch(() => {
        /* counter is best-effort; the attention view surfaces failures */
      });
    api
      .getIncidentStats()
      .then((stats) => {
        if (!cancelled) setIncidentCount(stats.active);
      })
      .catch(() => {
        /* the incidents view surfaces failures */
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  const attentionCount = attention.filter(
    (a) => a.attention_level === 'required' || a.attention_level === 'blocked',
  ).length;

  return (
    <AppShell
      attentionCount={attentionCount}
      incidentCount={incidentCount}
      live={live}
      onRefresh={refresh}
      refreshing={refreshing}
    >
      <Routes>
        <Route path="/" element={<Overview onData={setAttention} />} />
        <Route path="/attention" element={<Attention onData={setAttention} refreshKey={refreshKey} />} />
        <Route path="/media" element={<Media refreshKey={refreshKey} />} />
        <Route path="/activity" element={<Activity refreshKey={refreshKey} />} />
        <Route path="/incidents" element={<Incidents refreshKey={refreshKey} />} />
        <Route path="/incidents/:id" element={<IncidentDetailPage refreshKey={refreshKey} />} />
        <Route path="/item/:id" element={<ItemDetailPage />} />
        <Route path="*" element={<Overview onData={setAttention} />} />
      </Routes>
    </AppShell>
  );
}

export default App;

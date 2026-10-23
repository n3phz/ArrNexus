import {
  MediaItem,
  Event,
  ServiceStatus,
  Explanation,
  AttentionItem,
  AttentionStats,
  ActivityStats,
  ItemDetail,
  TimelineEvent,
  Incident,
  IncidentStats,
  IncidentSyncResult,
} from '../types';

const API_BASE = '/api';

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, options);
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== '') search.append(k, String(v));
  });
  const s = search.toString();
  return s ? `?${s}` : '';
}

export const api = {
  // Health
  health: () => request('/health'),
  ready: () => request('/ready'),

  // Items
  getItems: (params?: { state?: string; media_type?: string; limit?: number; offset?: number }) =>
    request<MediaItem[]>(`/items${qs({ ...params })}`),

  getItem: (id: string) => request<ItemDetail>(`/items/${encodeURIComponent(id)}`),

  getItemTimeline: (id: string) =>
    request<TimelineEvent[]>(`/items/${encodeURIComponent(id)}/timeline`),

  getItemExplanation: (id: string) =>
    request<Explanation>(`/items/${encodeURIComponent(id)}/explain`),

  // Events
  getEvents: (params?: { limit?: number; offset?: number; source_service?: string; event_type?: string }) =>
    request<Event[]>(`/events${qs({ ...params })}`),

  // Services
  getServices: () => request<ServiceStatus[]>('/services'),

  forcePoll: () => request('/services/poll', { method: 'POST' }),

  // Phase 4B: operational attention
  getAttention: (params?: { include_blocked?: boolean; exclude_synthetic?: boolean; limit?: number }) =>
    request<AttentionItem[]>(`/attention${qs({ include_blocked: true, ...params })}`),

  getAttentionStats: (params?: { exclude_synthetic?: boolean }) =>
    request<AttentionStats>(`/attention/stats${qs({ ...params })}`),

  // Activity
  getActivity: (params?: {
    limit?: number;
    offset?: number;
    days?: number;
    source_service?: string;
    event_type?: string;
    search?: string;
    stuck_only?: boolean;
    failed_only?: boolean;
  }) => request<Event[]>(`/activity${qs({ limit: 100, days: 7, ...params })}`),

  getActivityStats: (days = 7) => request<ActivityStats>(`/activity/stats${qs({ days })}`),

  // Phase 4D: incident lifecycle
  getIncidents: (params?: { status?: string }) =>
    request<Incident[]>(`/incidents${qs({ ...params })}`),

  getIncident: (id: string) => request<Incident>(`/incidents/${encodeURIComponent(id)}`),

  getIncidentStats: () => request<IncidentStats>('/incidents/stats'),

  acknowledgeIncident: (id: string) =>
    request<Incident>(`/incidents/${encodeURIComponent(id)}/acknowledge`, { method: 'POST' }),

  resolveIncident: (id: string) =>
    request<Incident>(`/incidents/${encodeURIComponent(id)}/resolve`, { method: 'POST' }),

  archiveIncident: (id: string) =>
    request<Incident>(`/incidents/${encodeURIComponent(id)}/archive`, { method: 'POST' }),

  /** Data refresh (not a lifecycle action): re-evaluate attention into the store. */
  syncIncidents: () => request<IncidentSyncResult>('/incidents/sync', { method: 'POST' }),
};

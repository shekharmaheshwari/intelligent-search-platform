import type { AnalyticsData } from '../types';

const BASE = import.meta.env.PUBLIC_API_URL || 'http://localhost:8001/v1';
const AUTH_ENABLED = import.meta.env.PUBLIC_AUTH_ENABLED === 'true';
const API_ROOT = BASE.replace('/v1', '');

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...opts.headers as Record<string, string>,
  };
  if (AUTH_ENABLED) {
    const token = typeof localStorage !== 'undefined' ? localStorage.getItem('isp-auth-token') : null;
    if (token) headers['Authorization'] = `Bearer ${token}`;
  }
  const res = await fetch(`${BASE}${path}`, { ...opts, headers });
  if (res.status === 401 && AUTH_ENABLED) {
    localStorage.removeItem('isp-auth-token');
    window.location.href = `${API_ROOT}/auth/login`;
    throw new Error('Session expired');
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/**
 * Repository for analytics HTTP calls.
 * Each method maps 1-to-1 with a backend endpoint. No business logic, no store access.
 */
export class AnalyticsRepository {
  getAnalytics(indexId: string, range: '7d' | '30d' | '90d' = '30d'): Promise<AnalyticsData> {
    return req<AnalyticsData>(`/indexes/${indexId}/analytics?range=${range}`);
  }
}

export const analyticsRepository = new AnalyticsRepository();

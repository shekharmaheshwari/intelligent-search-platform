import type { AffinityDimension } from '../../lib/types';

export function toggleDimension(dims: AffinityDimension[], idx: number, enabled: boolean): AffinityDimension[] {
  return dims.map((d, j) => j === idx ? { ...d, enabled } : d);
}

export function setDimensionBoost(dims: AffinityDimension[], idx: number, raw: string): AffinityDimension[] | null {
  const n = parseFloat(raw);
  if (isNaN(n) || n < 0 || n > 1) return null;
  return dims.map((d, j) => j === idx ? { ...d, boost: n } : d);
}

// Single validated data-access layer (directive Stage 3): loads bundle.json,
// builds indexes once, exposes typed queries. All numbers derive from the
// approved synthetic dataset — nothing is invented.
export interface Shipment { id: string; cargo: string; vehicle: string; departed: string; arrived: string; verdict: string; minC: number; maxC: number; pallets: number }
export interface Reading { at: string; tempC: number; hum: number | null; lat: number | null; lon: number | null; risk: string }
export interface Alert { at: string; type: string; severity: string; message: string }
export interface Custody { no: number; by: string; at: string; sealIntact: string }

export interface Dataset {
  meta: { counts: { shipments: number; telemetry: number; alerts: number; custody: number; sops: number }; shipments_with_alerts: number; integrity: { shipments_sha256: string; telemetry_rows_sha256: string } };
  shipments: [string, string, string, string, string, string, number, number, number][];
  telemetry: Record<string, [string, number, number | null, number | null, number | null, string][]>;
  alerts: Record<string, [string, string, string, string][]>;
  custody: Record<string, [number, string, string, string][]>;
  sops: { file: string; title: string; section: string; excerpt: string }[];
}

let _ds: Dataset | null = null;
export async function loadDataset(): Promise<Dataset> {
  if (_ds) return _ds;
  const r = await fetch("/data/bundle.json");
  if (!r.ok) throw new Error(`dataset load failed: ${r.status}`);
  _ds = (await r.json()) as Dataset;
  return _ds;
}

export interface Store {
  byId: Map<string, Shipment>;
  ids: string[];
  readings(id: string): Reading[];
  alertsOf(id: string): Alert[];
  custodyOf(id: string): Custody[];
  stats(id: string): { n: number; min: number; max: number; avg: number };
}

export function buildStore(ds: Dataset): Store {
  const byId = new Map<string, Shipment>();
  for (const s of ds.shipments)
    byId.set(s[0], { id: s[0], cargo: s[1], vehicle: s[2], departed: s[3], arrived: s[4], verdict: s[5], minC: s[6], maxC: s[7], pallets: s[8] });
  const readings = (id: string): Reading[] =>
    (ds.telemetry[id] ?? []).map((r) => ({ at: r[0], tempC: r[1], hum: r[2], lat: r[3], lon: r[4], risk: r[5] }));
  return {
    byId,
    ids: ds.shipments.map((s) => s[0]),
    readings,
    alertsOf: (id) => (ds.alerts[id] ?? []).map((a) => ({ at: a[0], type: a[1], severity: a[2], message: a[3] })),
    custodyOf: (id) => (ds.custody[id] ?? []).map((c) => ({ no: c[0], by: c[1], at: c[2], sealIntact: c[3] })),
    stats(id) {
      const rs = readings(id).filter((r) => !Number.isNaN(r.tempC));
      if (!rs.length) return { n: 0, min: NaN, max: NaN, avg: NaN };
      const t = rs.map((r) => r.tempC);
      return { n: t.length, min: Math.min(...t), max: Math.max(...t), avg: t.reduce((a, b) => a + b, 0) / t.length };
    },
  };
}

// dataset-wide aggregates (bounded, computed from the bundle)
export function aggregates(ds: Dataset) {
  let accepted = 0, rejected = 0, withNote = 0;
  const cargoCount = new Map<string, number>();
  for (const s of ds.shipments) {
    if (s[5] === "accepted") accepted++;
    else if (s[5] === "rejected") rejected++;
    else withNote++;
    cargoCount.set(s[1], (cargoCount.get(s[1]) ?? 0) + 1);
  }
  return {
    shipments: ds.shipments.length, telemetry: ds.meta.counts.telemetry,
    alerts: ds.meta.counts.alerts, custody: ds.meta.counts.custody,
    accepted, rejected, withNote,
    cargoTypes: [...cargoCount.entries()].sort((a, b) => b[1] - a[1]),
  };
}

export const SID_RE = /^UC-\d{6}$/;
export function validateShipmentId(raw: string): string | null {
  const id = raw.trim().toUpperCase();
  return SID_RE.test(id) ? id : null;
}

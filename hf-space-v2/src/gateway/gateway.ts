// Provider-modular gateway contracts (directive: auth, quota and inference
// must NOT be tightly coupled to one provider). Implementations:
//   now  → in-memory mocks (P8-B local prototype)
//   later→ SupabaseAuthProvider / PostgresQuotaStore / NimProvider (P8-E staging)
// NVIDIA inference stays DISABLED until a deployed guardrail runtime is
// verified (inference_enabled=false is enforced by the gateway itself).

export interface AuthUser { id: string; role: "demo_user" | "suspended" | "admin"; active: boolean }
export interface AuthProvider { signInWithGoogle(): Promise<AuthUser>; currentUser(): AuthUser | null; signOut(): void }

export class MockAuthProvider implements AuthProvider {
  private user: AuthUser | null = null;
  async signInWithGoogle(): Promise<AuthUser> {
    this.user = { id: "mock-user-1", role: "demo_user", active: true };
    return this.user;
  }
  currentUser() { return this.user; }
  signOut() { this.user = null; }
}

export interface QuotaDecision { allowed: boolean; reason?: string; retryAfterSeconds?: number }
export interface QuotaStore {
  reserve(user: string): QuotaDecision;          // atomic user+global counters + concurrency lease
  release(user: string): void;
  remaining(user: string): { minute: number; hour: number; day: number };
}
export const QUOTA_LIMITS = { perUserPerMinute: 2, perUserPerHour: 5, perUserPerDay: 10, concurrencyUser: 1, concurrencyGlobal: 2, globalPerDay: 100 } as const;

export class InMemoryQuotaStore implements QuotaStore {
  private use = new Map<string, { minute: number[]; hour: number[]; day: number[]; inflight: number }>();
  private globalDay: number[] = [];
  private globalInflight = 0;
  constructor(private now: () => number = () => Date.now()) {} // clock injectable for tier tests
  private window(list: number[], ms: number) { const now = this.now(); return list.filter((t) => now - t < ms); }
  reserve(user: string): QuotaDecision {
    const u = this.use.get(user) ?? { minute: [], hour: [], day: [], inflight: 0 };
    u.minute = this.window(u.minute, 60_000); u.hour = this.window(u.hour, 3_600_000); u.day = this.window(u.day, 86_400_000);
    this.globalDay = this.window(this.globalDay, 86_400_000);
    const t = this.now();
    if (u.day.length >= QUOTA_LIMITS.perUserPerDay) return { allowed: false, reason: "daily_quota", retryAfterSeconds: 3600 };
    if (u.hour.length >= QUOTA_LIMITS.perUserPerHour) return { allowed: false, reason: "hourly_quota", retryAfterSeconds: 600 };
    if (u.minute.length >= QUOTA_LIMITS.perUserPerMinute) return { allowed: false, reason: "per_minute", retryAfterSeconds: 30 };
    if (u.inflight >= QUOTA_LIMITS.concurrencyUser) return { allowed: false, reason: "user_concurrency" };
    if (this.globalInflight >= QUOTA_LIMITS.concurrencyGlobal) return { allowed: false, reason: "global_concurrency" };
    if (this.globalDay.length >= QUOTA_LIMITS.globalPerDay) return { allowed: false, reason: "global_daily_budget" };
    u.minute.push(t); u.hour.push(t); u.day.push(t); u.inflight++;
    this.globalDay.push(t); this.globalInflight++;
    this.use.set(user, u);
    return { allowed: true };
  }
  release(user: string) {
    const u = this.use.get(user); if (!u) return;
    u.inflight = Math.max(0, u.inflight - 1); this.globalInflight = Math.max(0, this.globalInflight - 1);
  }
  remaining(user: string) {
    const u = this.use.get(user) ?? { minute: [], hour: [], day: [], inflight: 0 };
    return {
      minute: QUOTA_LIMITS.perUserPerMinute - this.window(u.minute, 60_000).length,
      hour: QUOTA_LIMITS.perUserPerHour - this.window(u.hour, 3_600_000).length,
      day: QUOTA_LIMITS.perUserPerDay - this.window(u.day, 86_400_000).length,
    };
  }
}

export interface InferenceRequest { question: string; conversationId: string }
export interface InferenceResult { text: string; model: string; usageTokens: number; guarded: boolean }
export interface InferenceProvider { enabled(): boolean; infer(req: InferenceRequest): Promise<InferenceResult> }

// Real NVIDIA NIM stays OFF: the gateway refuses until a deployed,
// verified guardrail runtime exists (operator rule 5).
export const inferenceEnabled = false;

export class MockNvidiaProvider implements InferenceProvider {
  enabled() { return inferenceEnabled; }
  async infer(req: InferenceRequest): Promise<InferenceResult> {
    throw new Error("live inference disabled (mock provider, guardrail runtime not deployed)");
  }
}

// Guardrail runtime contract: the local Python NeMo service implements this
// in tests; production requires a deployed, verified runtime before the
// inference provider may ever be enabled.
export interface GuardrailRuntime {
  checkInput(text: string): Promise<{ ok: boolean; reason?: string }>;
  checkOutput(text: string, evidence: string[]): Promise<{ ok: boolean; reason?: string }>;
}
export class LocalNemoRuntime implements GuardrailRuntime {
  constructor(private endpoint: string) {}
  async checkInput(text: string) {
    const r = await fetch(`${this.endpoint}/check-input`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text }) });
    return r.json();
  }
  async checkOutput(text: string, evidence: string[]) {
    const r = await fetch(`${this.endpoint}/check-output`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text, evidence }) });
    return r.json();
  }
}

// The gateway pipeline (directive 6A.8 steps 1-16, provider-modular).
export class Gateway {
  constructor(private auth: AuthProvider, private quota: QuotaStore, private inference: InferenceProvider, private rails: GuardrailRuntime | null) {}
  async chat(req: InferenceRequest): Promise<{ status: number; body: Record<string, unknown> }> {
    const user = this.auth.currentUser();
    if (!user) return { status: 401, body: { error: "authentication required" } };
    if (!user.active || user.role === "suspended") return { status: 403, body: { error: "account not authorized" } };
    if (!this.inference.enabled()) return { status: 503, body: { error: "live inference unavailable (offline demo remains fully functional)" } };
    if (!this.rails) return { status: 503, body: { error: "guardrail runtime unavailable — fail closed" } };
    if ((req.question ?? "").length > 4000) return { status: 400, body: { error: "request too large" } };
    const q = this.quota.reserve(user.id);
    if (!q.allowed) return { status: 429, body: { error: q.reason, retryAfterSeconds: q.retryAfterSeconds ?? 30 } };
    try {
      const inR = await this.rails.checkInput(req.question);
      if (!inR.ok) return { status: 400, body: { error: "input rejected by guardrails", reason: inR.reason } };
      const result = await this.inference.infer(req);
      const outR = await this.rails.checkOutput(result.text, []);
      if (!outR.ok) return { status: 502, body: { error: "response failed output validation" } };
      return { status: 200, body: { text: result.text, model: result.model } };
    } finally {
      this.quota.release(user.id);
    }
  }
}

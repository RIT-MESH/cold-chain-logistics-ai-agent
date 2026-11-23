// Supabase Google OAuth adapter (P8-F production implementation).
// Implements the AuthProvider interface from gateway.ts.
// Uses the official @supabase/supabase-js client — no custom auth code.
//
// SCOPES: This adapter requests ONLY 'openid email profile' via Supabase's
// Google OAuth provider. It NEVER requests Gmail, Contacts, or Drive scopes.
// Supabase's Google provider configuration controls which scopes the OAuth
// client requests; our code has no scope parameters to tamper with.
import type { AuthProvider, AuthUser } from "./gateway";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

export interface SupabaseAuthProviderConfig {
  supabaseUrl: string;       // e.g. https://xyz.supabase.co
  supabaseAnonKey: string;   // the public anon key (safe for the browser)
}

export class SupabaseAuthProvider implements AuthProvider {
  private client: SupabaseClient;
  private user: AuthUser | null = null;

  constructor(config: SupabaseAuthProviderConfig) {
    // supabase-js is a regular dependency (see package.json).
    // Vite bundles this import at build time; the package is always
    // available after `npm install`.
    this.client = createClient(config.supabaseUrl, config.supabaseAnonKey);
  }

  /** Google OAuth sign-in — opens the Supabase Google OAuth flow.
   * Only openid + email + profile scopes (configured in the Supabase dashboard).
   * No Gmail, Contacts, or Drive scopes. */
  async signInWithGoogle(): Promise<AuthUser> {
    const { data, error } = await this.client.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: typeof window !== "undefined" ? window.location.origin : "",
      },
    });
    if (error) throw new Error(`Google sign-in failed: ${error.message}`);
    // After the OAuth redirect, the user lands back on the app with a session
    // The onAuthStateChange listener below picks it up.
    return this.awaitSession();
  }

  /** Wait for the auth callback to complete and return the user. */
  private async awaitSession(): Promise<AuthUser> {
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("Auth session timeout")), 60_000);
      this.client.auth.onAuthStateChange((event: string, session: any) => {
        if (event === "SIGNED_IN" && session?.user) {
          clearTimeout(timeout);
          this.user = this.mapUser(session.user);
          resolve(this.user);
        }
      });
    });
  }

  /** Map a Supabase user to our AuthUser interface.
   * Uses the Supabase auth UID as the stable identity key — NEVER the email
   * (which can change or be client-supplied). */
  private mapUser(supabaseUser: any): AuthUser {
    return {
      id: supabaseUser.id,  // stable UUID from Supabase auth (server-verified)
      role: "demo_user",    // default role; suspension checked server-side
      active: true,          // suspension state checked on every API request
    };
  }

  /** Get the current authenticated user from the session.
   * Returns null if no active session (expired, signed out, never signed in). */
  currentUser(): AuthUser | null {
    return this.user;
  }

  /** Check and refresh the auth session. Called before every API request. */
  async checkSession(): Promise<boolean> {
    const { data: { session }, error } = await this.client.auth.getSession();
    if (error || !session) {
      this.user = null;
      return false;
    }
    // Check if the access token is about to expire
    const expiresAt = session.expires_at ?? 0;
    const now = Math.floor(Date.now() / 1000);
    if (expiresAt <= now) {
      // Attempt refresh
      const { data: { session: refreshed } } = await this.client.auth.refreshSession();
      if (!refreshed) {
        this.user = null;
        return false;
      }
    }
    this.user = this.mapUser(session.user);
    return true;
  }

  /** Sign out — invalidates the session. */
  signOut(): void {
    this.client.auth.signOut();
    this.user = null;
  }

  /** Get the access token for API calls (bearer token for the gateway).
   * This token is verified server-side by Supabase Edge Functions. */
  async getAccessToken(): Promise<string | null> {
    const { data: { session } } = await this.client.auth.getSession();
    return session?.access_token ?? null;
  }
}

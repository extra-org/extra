/** The bearer token the widget sends, resolved in order: a token the host
 * supplies (`tokenProvider` or `token-url`), then a visitor pass. `null` means
 * "send no bearer" — the host's session cookie speaks instead.
 *
 * A pass is only minted after a 401, so a cookie-authenticated host is never
 * handed an anonymous identity by mistake.
 */
export type TokenProvider = () => Promise<string | null>;

/** Why a configured host identity could not be obtained.
 *
 * `unreachable` and `malformed` are almost always integration mistakes — a
 * `token-url` pointing at the wrong origin, or an endpoint answering with
 * something other than `{ "token": "..." }`. `unauthorized` is the ordinary
 * signed-out case and the only one a host should expect in normal use.
 */
export type IdentityFailureReason = "unauthorized" | "unreachable" | "malformed";

export interface IdentityFailure {
  reason: IdentityFailureReason;
  /** Present when the endpoint answered at all. */
  status?: number;
  url: string;
}

export interface TokenSourceOptions {
  tokenUrl?: string;
  provider?: TokenProvider | null;
  storage?: Storage;
  /** Refuse to fall back to an anonymous visitor. For products where a chat
   *  with no proven user is not acceptable at all. */
  requireIdentity?: boolean;
  onIdentityFailure?: (failure: IdentityFailure) => void;
}

const PASS_ENDPOINT = "/auth/anonymous";

export function visitorPassKey(endpoint: string): string {
  return `agent-chat:pass:${endpoint}`;
}

export class TokenSource {
  private cached: string | null = null;
  private pending: Promise<string | null> | null = null;
  /** Bumped by `reset()` so a resolution already in flight, once it lands, can
   *  tell it is answering a question nobody is asking anymore. */
  private generation = 0;
  private readonly tokenUrl: string;
  private readonly provider: TokenProvider | null;
  private readonly storage: Storage;
  private readonly requireIdentity: boolean;
  private readonly onIdentityFailure: (failure: IdentityFailure) => void;

  constructor(
    private readonly endpoint: string,
    options: TokenSourceOptions = {},
  ) {
    this.tokenUrl = options.tokenUrl ?? "";
    this.provider = options.provider ?? null;
    this.storage = options.storage ?? localStorage;
    this.requireIdentity = options.requireIdentity ?? false;
    this.onIdentityFailure = options.onIdentityFailure ?? (() => {});
  }

  /** The stored anonymous visitor pass, if one exists.
   *
   * Sent on every request as `X-Extra-Visitor-Pass` so the server can
   * opportunistically adopt anonymous history the moment it sees an
   * authenticated principal alongside it. The server validates and consumes it;
   * the frontend just carries it passively until cleared post-adoption.
   */
  get visitorPass(): string | null {
    return this.storedPass();
  }

  async current(): Promise<string | null> {
    if (!this.cached) {
      await this.resolve(() => this.storedPass());
    }
    return this.cached;
  }

  /** After a 401: whatever we sent is no good, so get another. */
  async renew(): Promise<string | null> {
    return this.resolve(() => this.issuePass());
  }

  /** Forget the token in hand, so the next request works out who the caller is
   *  again. Keeps the visitor pass: that pass is what the sign-in merge hands
   *  over, and dropping it here would strand whatever this browser chatted
   *  about before logging in. */
  reset(): void {
    this.generation += 1;
    this.cached = null;
    // Not just the value — the in-flight resolution itself is disowned, so the
    // next call starts a fresh one instead of awaiting an answer to a question
    // that no longer applies (e.g. the old tokenProvider).
    this.pending = null;
  }

  /** Drop this browser's identity entirely — a host app signing its user out. */
  forget(): void {
    this.reset();
    this.clearPass();
  }

  /** Concurrent callers share one resolution. Without this, parallel requests
   *  each fetch a token and each hand over the visitor pass. */
  private resolve(fallback: () => string | null | Promise<string | null>): Promise<string | null> {
    const generation = this.generation;
    this.pending ??= (async () => {
      try {
        const host = await this.hostToken();
        // A host that demands a proven user gets no anonymous consolation
        // prize: better a visibly broken chat than a silently wrong identity.
        const result = host ?? (this.requireIdentity ? null : await fallback());
        // A reset() since this resolution started means someone has already
        // moved on — most likely `refreshIdentity()` with a new tokenProvider.
        // Landing late must not overwrite whatever answered in the meantime.
        if (generation === this.generation) this.cached = result;
        return result;
      } finally {
        if (generation === this.generation) this.pending = null;
      }
    })();
    return this.pending;
  }

  /** A host token only. In cookie mode this returns null — the session cookie
   *  speaks for the caller directly. Anonymous history adoption is now handled
   *  server-side via the X-Extra-Visitor-Pass header. */
  private async hostToken(): Promise<string | null> {
    return this.fromHost();
  }

  private clearPass(): void {
    try {
      this.storage.removeItem(visitorPassKey(this.endpoint));
    } catch {
      // Private-mode storage failures must not break the chat.
    }
  }

  private async fromHost(): Promise<string | null> {
    if (this.provider) return this.provider();
    if (!this.tokenUrl) return null;
    let response: Response;
    try {
      response = await fetch(this.tokenUrl, {
        credentials: "include",
        headers: { Accept: "application/json" },
      });
    } catch {
      return this.failed({ reason: "unreachable", url: this.tokenUrl });
    }
    if (!response.ok) {
      const reason = response.status === 401 || response.status === 403 ? "unauthorized" : "unreachable";
      return this.failed({ reason, status: response.status, url: this.tokenUrl });
    }
    const token = await response
      .json()
      .then((body) => body?.token)
      .catch(() => null);
    if (typeof token !== "string" || !token) {
      return this.failed({ reason: "malformed", status: response.status, url: this.tokenUrl });
    }
    return token;
  }

  /** Report and degrade. A configured identity that cannot be obtained is worth
   *  saying out loud — silence here is what makes a broken `token-url` look
   *  like a working anonymous chat. */
  private failed(failure: IdentityFailure): null {
    this.onIdentityFailure(failure);
    return null;
  }

  private storedPass(): string | null {
    try {
      return this.storage.getItem(visitorPassKey(this.endpoint));
    } catch {
      return null;
    }
  }

  private async issuePass(): Promise<string | null> {
    try {
      const response = await fetch(`${this.endpoint}${PASS_ENDPOINT}`, { method: "POST" });
      if (!response.ok) return null;
      const token = (await response.json())?.token;
      if (typeof token !== "string" || !token) return null;
      this.storage.setItem(visitorPassKey(this.endpoint), token);
      return token;
    } catch {
      return null;
    }
  }
}

import { createHash, createPrivateKey, createPublicKey, generateKeyPairSync, randomUUID, sign, verify,
  type KeyObject, type JsonWebKey } from 'node:crypto';
import { AppError } from '@podacha/shared';

/** Minimal ES256 JWT (RFC 7515/7519) on node:crypto — no external deps. */
const b64u = (b: Buffer | string) => Buffer.from(b).toString('base64url');
const fromB64u = (s: string) => Buffer.from(s, 'base64url');

export interface SigningKey { kid: string; privateKey?: KeyObject; publicKey: KeyObject }

export function kidOf(pub: KeyObject): string {
  const j = pub.export({ format: 'jwk' }) as JsonWebKey;
  // RFC 7638 thumbprint
  return createHash('sha256').update(JSON.stringify({ crv: j.crv, kty: j.kty, x: j.x, y: j.y })).digest('base64url');
}

export class KeyStore {
  private keys: SigningKey[] = [];
  constructor(current: { privateKey: KeyObject }, previousPublic: KeyObject[] = []) {
    const pub = createPublicKey(current.privateKey);
    this.keys.push({ kid: kidOf(pub), privateKey: current.privateKey, publicKey: pub });
    for (const p of previousPublic) this.keys.push({ kid: kidOf(p), publicKey: p });
  }
  static generate(): KeyStore {
    const { privateKey } = generateKeyPairSync('ec', { namedCurve: 'P-256' });
    return new KeyStore({ privateKey });
  }
  /** AUTH_JWT_PRIVATE_KEY (PEM, P-256) + optional AUTH_JWT_PREVIOUS_PUBLIC_KEYS (PEMs joined by `|||`). */
  static fromEnv(env: NodeJS.ProcessEnv = process.env): KeyStore {
    const pem = env.AUTH_JWT_PRIVATE_KEY;
    if (!pem) {
      if (env.NODE_ENV === 'production') throw new Error('AUTH_JWT_PRIVATE_KEY is required in production');
      return KeyStore.generate();
    }
    const prev = (env.AUTH_JWT_PREVIOUS_PUBLIC_KEYS ?? '').split('|||').map((s) => s.trim()).filter(Boolean).map((p) => createPublicKey(p));
    return new KeyStore({ privateKey: createPrivateKey(pem.replace(/\\n/g, '\n')) }, prev);
  }
  get current(): SigningKey { return this.keys[0]!; }
  find(kid: string): SigningKey | undefined { return this.keys.find((k) => k.kid === kid); }
  jwks(): { keys: (JsonWebKey & { kid: string; alg: string; use: string })[] } {
    return { keys: this.keys.map((k) => ({ ...(k.publicKey.export({ format: 'jwk' }) as JsonWebKey), kid: k.kid, alg: 'ES256', use: 'sig' })) };
  }
}

export function signJwt(ks: KeyStore, payload: Record<string, unknown>): string {
  const k = ks.current;
  const head = b64u(JSON.stringify({ alg: 'ES256', typ: 'JWT', kid: k.kid }));
  const body = b64u(JSON.stringify({ jti: randomUUID(), ...payload }));
  const sig = sign('sha256', Buffer.from(`${head}.${body}`), { key: k.privateKey!, dsaEncoding: 'ieee-p1363' });
  return `${head}.${body}.${b64u(sig)}`;
}

export interface VerifyOptions { aud: string; iss: string; now?: number; leewaySec?: number }

export function verifyJwt<T extends Record<string, unknown>>(ks: KeyStore, token: string, o: VerifyOptions): T {
  const bad = (m: string) => new AppError('UNAUTHORIZED', 401, m);
  const parts = token.split('.');
  if (parts.length !== 3) throw bad('Malformed token');
  let header: { alg?: string; kid?: string }; let claims: T & { exp?: number; nbf?: number; aud?: unknown; iss?: unknown };
  try { header = JSON.parse(fromB64u(parts[0]!).toString()); claims = JSON.parse(fromB64u(parts[1]!).toString()); }
  catch { throw bad('Malformed token'); }
  if (header.alg !== 'ES256' || !header.kid) throw bad('Unsupported token');
  const key = ks.find(header.kid);
  if (!key) throw bad('Unknown signing key');
  const ok = verify('sha256', Buffer.from(`${parts[0]}.${parts[1]}`), { key: key.publicKey, dsaEncoding: 'ieee-p1363' }, fromB64u(parts[2]!));
  if (!ok) throw bad('Invalid signature');
  const now = Math.floor((o.now ?? Date.now()) / 1000); const leeway = o.leewaySec ?? 30;
  if (typeof claims.exp !== 'number' || claims.exp + leeway < now) throw bad('Token expired');
  if (typeof claims.nbf === 'number' && claims.nbf - leeway > now) throw bad('Token not yet valid');
  if (claims.aud !== o.aud) throw bad('Wrong audience');
  if (claims.iss !== o.iss) throw bad('Wrong issuer');
  return claims;
}

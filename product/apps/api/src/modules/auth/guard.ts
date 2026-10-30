import type { FastifyReply, FastifyRequest, preHandlerAsyncHookHandler } from 'fastify';
import { AppError, type AccessClaims, type RoleGrant, type Scope } from '@podacha/shared';
import type { TenantContext } from '@podacha/db';
import { authConfig } from './config.js';
import { verifyJwt } from './jwt.js';
import { canAccessVenue, hasScope } from './rbac.js';

/**
 * Tenant isolation middleware. Other modules use:
 *   app.get('/venues/:venueId/x', { preHandler: requireAuth({ scope: 'menu:write', venueParam: 'venueId' }) }, h)
 * then `req.tenant` (TenantContext for Db.repo) and `req.auth`.
 * Organization ALWAYS comes from the token (never from the body/query) [Р-4].
 * Foreign/inaccessible venue → 404; visible but not allowed → 403 [Р-3].
 */
export interface AuthContext { userId: string; organizationId: string; roles: RoleGrant[]; scopes: Scope[]; tokenId: string }

declare module 'fastify' {
  interface FastifyRequest { auth?: AuthContext; tenant?: TenantContext }
}

export function authenticate(req: FastifyRequest): AuthContext {
  if (req.auth) return req.auth;
  const h = req.headers.authorization;
  if (!h || !/^Bearer\s+/i.test(h)) throw new AppError('UNAUTHORIZED', 401, 'Bearer token required');
  const c = authConfig();
  const claims = verifyJwt<AccessClaims & Record<string, unknown>>(c.keys, h.replace(/^Bearer\s+/i, '').trim(),
    { aud: 'admin', iss: c.issuer, now: c.now() });
  if (typeof claims.org !== 'string' || typeof claims.sub !== 'string' || !Array.isArray(claims.roles))
    throw new AppError('UNAUTHORIZED', 401, 'Malformed claims');
  req.auth = { userId: claims.sub, organizationId: claims.org, roles: claims.roles, scopes: claims.scp ?? [], tokenId: claims.jti };
  req.tenant = { organizationId: claims.org };
  return req.auth;
}

export interface RequireAuthOptions { scope?: Scope; venueParam?: string }

export function requireAuth(opts: RequireAuthOptions = {}): preHandlerAsyncHookHandler {
  return async function (req: FastifyRequest, _reply: FastifyReply) {
    const a = authenticate(req);
    const venueId = opts.venueParam ? (req.params as Record<string, string | undefined>)[opts.venueParam] : undefined;
    if (opts.venueParam) {
      if (!venueId || !canAccessVenue(a.roles, venueId)) throw new AppError('NOT_FOUND', 404, 'Venue not found');
    }
    if (opts.scope && !hasScope(a.roles, opts.scope, venueId))
      throw new AppError('FORBIDDEN', 403, `Missing scope ${opts.scope}`);
  };
}

/** Typed accessor inside handlers guarded by requireAuth. */
export function ctxOf(req: FastifyRequest): { auth: AuthContext; tenant: TenantContext } {
  const auth = authenticate(req);
  return { auth, tenant: req.tenant! };
}

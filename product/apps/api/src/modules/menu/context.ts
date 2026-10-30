import type { FastifyInstance, FastifyRequest } from 'fastify';
import { AppError, type QrResolution } from '@podacha/shared';
import type { TenantContext } from '@podacha/db';

/**
 * Staff principal for admin routes. The `auth` module (owner: platform) is expected to set
 * `request.auth = { organizationId, userId, scopes, venueIds }` from the access JWT.
 * Until it exists, non-production builds accept dev headers `x-org-id`, `x-user-id`, `x-scopes`.
 */
export interface StaffPrincipal { organizationId: string; userId: string | null; scopes: string[]; venueIds: string[] | '*' }

export function staffPrincipal(req: FastifyRequest): StaffPrincipal {
  const a = (req as unknown as { auth?: Partial<StaffPrincipal> }).auth;
  if (a?.organizationId) return { organizationId: a.organizationId, userId: a.userId ?? null, scopes: a.scopes ?? [], venueIds: a.venueIds ?? '*' };
  if (process.env.NODE_ENV !== 'production') {
    const org = req.headers['x-org-id'];
    if (typeof org === 'string' && org) {
      const user = req.headers['x-user-id'];
      const scp = req.headers['x-scopes'];
      return {
        organizationId: org, userId: typeof user === 'string' ? user : null,
        scopes: typeof scp === 'string' ? scp.split(/[ ,]+/).filter(Boolean) : ['menu:write', 'stoplist:write'],
        venueIds: '*',
      };
    }
  }
  throw new AppError('UNAUTHORIZED', 401, 'Authentication required');
}

/** Checks scope + venue membership. Venue outside membership → 404 (api-contract.md §2.3). */
export function requireStaff(req: FastifyRequest, venueId: string, scope?: string): { ctx: TenantContext; principal: StaffPrincipal } {
  const p = staffPrincipal(req);
  if (p.venueIds !== '*' && !p.venueIds.includes(venueId)) throw new AppError('NOT_FOUND', 404, 'venue not found');
  if (scope && !p.scopes.includes(scope)) throw new AppError('FORBIDDEN', 403, `scope ${scope} required`);
  return { ctx: { organizationId: p.organizationId }, principal: p };
}

/**
 * QR token → venue. The tenancy module owns qr_code (token hash, SECURITY DEFINER lookup) and should
 * `app.decorate('resolveQrToken', fn)`. Tests/demo may register a resolver via `setQrResolver`.
 */
export type QrResolver = (token: string) => Promise<QrResolution | null>;
let fallbackResolver: QrResolver | null = null;
export function setQrResolver(r: QrResolver | null): void { fallbackResolver = r; }

export async function resolveQr(app: FastifyInstance, token: string): Promise<QrResolution> {
  const decorated = (app as unknown as { resolveQrToken?: QrResolver }).resolveQrToken;
  const resolver = typeof decorated === 'function' ? decorated : fallbackResolver;
  if (!/^[A-Za-z0-9_-]{8,64}$/.test(token) || !resolver) throw new AppError('NOT_FOUND', 404, 'QR code not found');
  const r = await resolver(token);
  if (!r) throw new AppError('NOT_FOUND', 404, 'QR code not found');
  if (!r.isActive) throw new AppError('QR_REVOKED', 410, 'QR code revoked, ask the waiter');
  return r;
}

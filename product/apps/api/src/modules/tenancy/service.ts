import type { Db, TenantContext } from '@podacha/db';
import { AppError, PLATFORM_ORG_ID, uuidv7, type RoleGrant } from '@podacha/shared';
import { randomBytes } from 'node:crypto';
import type { z } from 'zod';
import { tenancyRepos, type OrganizationRow, type VenueRow } from './repo.js';
import type { orgUpdateBody, venueCreateBody, venueUpdateBody } from './schemas.js';
import { venueScope } from '../auth/rbac.js';

const PLATFORM: TenantContext = { organizationId: PLATFORM_ORG_ID };
const nowIso = () => new Date().toISOString();

const TRANSLIT: Record<string, string> = { а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z', и: 'i', й: 'y',
  к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f', х: 'h', ц: 'ts', ч: 'ch', ш: 'sh',
  щ: 'sch', ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya' };
export function slugify(name: string): string {
  const s = [...name.toLowerCase()].map((c) => TRANSLIT[c] ?? c).join('')
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40).replace(/-+$/, '');
  return s.length >= 3 ? s : `venue-${s}`.replace(/-+$/, '');
}

/** Organization lifecycle. Called by auth signup. */
export async function createOrganization(db: Db, input: { legal_name: string; inn: string; kpp?: string; ogrn?: string;
  tax_system?: OrganizationRow['tax_system'] }): Promise<OrganizationRow> {
  const id = uuidv7();
  return tenancyRepos(db).orgs.insert({ organizationId: id }, {
    id, organization_id: id, legal_name: input.legal_name, inn: input.inn, kpp: input.kpp ?? null, ogrn: input.ogrn ?? null,
    tax_system: input.tax_system ?? 'usn_income', plan: 'start', status: 'trial', created_at: nowIso(), updated_at: null,
  });
}

export async function getOrganization(db: Db, ctx: TenantContext): Promise<OrganizationRow> {
  const o = await tenancyRepos(db).orgs.get(ctx, ctx.organizationId);
  if (!o) throw new AppError('NOT_FOUND', 404, 'Organization not found');
  return o;
}

export async function updateOrganization(db: Db, ctx: TenantContext, patch: z.output<typeof orgUpdateBody>) {
  const o = await tenancyRepos(db).orgs.update(ctx, ctx.organizationId, { ...patch, updated_at: nowIso() });
  if (!o) throw new AppError('NOT_FOUND', 404, 'Organization not found');
  return o;
}

async function reserveSlug(db: Db, wanted: string | undefined, name: string, venueId: string): Promise<string> {
  const slugs = tenancyRepos(db).slugs;
  const base = wanted ?? slugify(name);
  for (let i = 0; i < 6; i++) {
    const candidate = i === 0 ? base : `${base.slice(0, 44)}-${randomBytes(3).toString('hex').slice(0, 4)}`;
    if ((await slugs.list(PLATFORM, { slug: candidate }, 1)).length === 0) {
      await slugs.insert(PLATFORM, { id: uuidv7(), slug: candidate, venue_id: venueId });
      return candidate;
    }
    if (wanted) throw new AppError('CONFLICT', 409, 'Slug already taken');
  }
  throw new AppError('CONFLICT', 409, 'Could not allocate slug');
}

export async function createVenue(db: Db, ctx: TenantContext, input: z.output<typeof venueCreateBody>): Promise<VenueRow> {
  const id = uuidv7();
  const slug = await reserveSlug(db, input.slug, input.name, id);
  return tenancyRepos(db).venues.insert(ctx, {
    id, brand_id: null, name: input.name, slug, address: input.address ?? null, lat: input.lat ?? null, lon: input.lon ?? null,
    timezone: input.timezone ?? 'Europe/Moscow', default_locale: input.default_locale ?? 'ru', currency: 'RUB',
    service_charge_bps: input.service_charge_bps ?? 0, pos_system: input.pos_system ?? 'none', settings: input.settings ?? {},
    created_at: nowIso(), updated_at: null, deleted_at: null,
  });
}

/** Returns the venue if it exists in the tenant and is not deleted; otherwise 404. Use from other modules. */
export async function getVenue(db: Db, ctx: TenantContext, venueId: string): Promise<VenueRow> {
  const v = await tenancyRepos(db).venues.get(ctx, venueId);
  if (!v || v.deleted_at) throw new AppError('NOT_FOUND', 404, 'Venue not found');
  return v;
}

export async function listVenues(db: Db, ctx: TenantContext, roles: RoleGrant[]): Promise<VenueRow[]> {
  const scope = venueScope(roles);
  const all = await tenancyRepos(db).venues.list(ctx, {}, 200);
  return all.filter((v) => !v.deleted_at && (scope === '*' || scope.includes(v.id)))
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
}

/** Checks that all ids are live venues of the tenant (used when assigning roles). */
export async function assertVenuesExist(db: Db, ctx: TenantContext, ids: string[]): Promise<void> {
  for (const id of ids) await getVenue(db, ctx, id);
}

export async function updateVenue(db: Db, ctx: TenantContext, venueId: string, patch: z.output<typeof venueUpdateBody>) {
  const v = await getVenue(db, ctx, venueId);
  const { slug, ...rest } = patch;
  const next: Partial<VenueRow> = { ...rest, updated_at: nowIso() };
  if (slug && slug !== v.slug) {
    next.slug = await reserveSlug(db, slug, v.name, v.id);
    const old = await tenancyRepos(db).slugs.list(PLATFORM, { slug: v.slug }, 1);
    if (old[0]) await tenancyRepos(db).slugs.update(PLATFORM, old[0].id, { slug: `__released__${old[0].id}` });
  }
  return (await tenancyRepos(db).venues.update(ctx, venueId, next))!;
}

export async function deleteVenue(db: Db, ctx: TenantContext, venueId: string): Promise<void> {
  await getVenue(db, ctx, venueId);
  await tenancyRepos(db).venues.update(ctx, venueId, { deleted_at: nowIso() });
}

/** Public projection (no internal fields). */
export const venueDto = (v: VenueRow) => ({ id: v.id, name: v.name, slug: v.slug, address: v.address, lat: v.lat, lon: v.lon,
  timezone: v.timezone, default_locale: v.default_locale, currency: v.currency, service_charge_bps: v.service_charge_bps,
  pos_system: v.pos_system, settings: v.settings, created_at: v.created_at, updated_at: v.updated_at });
export const orgDto = (o: OrganizationRow) => ({ id: o.id, legal_name: o.legal_name, inn: o.inn, kpp: o.kpp, ogrn: o.ogrn,
  tax_system: o.tax_system, plan: o.plan, status: o.status, created_at: o.created_at });

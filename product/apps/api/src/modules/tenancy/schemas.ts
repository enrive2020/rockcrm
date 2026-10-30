import { z } from 'zod';

const settings = z.record(z.unknown()).refine((o) => JSON.stringify(o).length <= 32_000, 'settings too large');
const tz = z.string().refine((t) => { try { new Intl.DateTimeFormat('en', { timeZone: t }); return true; } catch { return false; } }, 'Unknown timezone');

export const venueCreateBody = z.object({
  name: z.string().trim().min(1).max(200),
  slug: z.string().trim().toLowerCase().regex(/^[a-z0-9](?:[a-z0-9-]{1,48}[a-z0-9])$/, 'slug: 3-50 chars a-z, 0-9, -').optional(),
  address: z.string().trim().max(500).nullable().optional(),
  lat: z.number().min(-90).max(90).nullable().optional(), lon: z.number().min(-180).max(180).nullable().optional(),
  timezone: tz.optional(), default_locale: z.string().regex(/^[a-z]{2}(-[A-Z]{2})?$/).optional(),
  service_charge_bps: z.number().int().min(0).max(3000).optional(),
  pos_system: z.enum(['none', 'iiko', 'poster', 'rkeeper']).optional(),
  settings: settings.optional(),
});
export const venueUpdateBody = venueCreateBody.partial().refine((b) => Object.keys(b).length > 0, 'nothing to update');

export const orgUpdateBody = z.object({
  legal_name: z.string().trim().min(1).max(300).optional(),
  kpp: z.string().regex(/^\d{9}$/).nullable().optional(),
  ogrn: z.string().regex(/^\d{13}(\d{2})?$/).nullable().optional(),
  tax_system: z.enum(['osn', 'usn_income', 'usn_income_expense', 'patent']).optional(),
}).refine((b) => Object.keys(b).length > 0, 'nothing to update');

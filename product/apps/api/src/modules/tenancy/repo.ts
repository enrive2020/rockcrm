import type { Db } from '@podacha/db';

export type OrganizationRow = { id: string; organization_id?: string; legal_name: string; inn: string; kpp: string | null;
  ogrn: string | null; tax_system: 'osn' | 'usn_income' | 'usn_income_expense' | 'patent'; plan: string;
  status: 'trial' | 'active' | 'suspended' | 'closed'; created_at: string; updated_at: string | null };
export type VenueRow = { id: string; organization_id?: string; brand_id: string | null; name: string; slug: string;
  address: string | null; lat: number | null; lon: number | null; timezone: string; default_locale: string; currency: string;
  service_charge_bps: number; pos_system: 'none' | 'iiko' | 'poster' | 'rkeeper'; settings: Record<string, unknown>;
  created_at: string; updated_at: string | null; deleted_at: string | null };
export type VenueSlugRow = { id: string; organization_id?: string; slug: string; venue_id: string };

export function tenancyRepos(db: Db) {
  return {
    orgs: db.repo<OrganizationRow>('organization'),
    venues: db.repo<VenueRow>('venue'),
    slugs: db.repo<VenueSlugRow>('venue_slug'),
  };
}

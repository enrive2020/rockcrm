/**
 * Lightweight query layer. Every call is tenant-scoped: implementations MUST set
 * `SET LOCAL app.org_id` (RLS) or filter by organization_id. No ORM by design.
 */
export interface TenantContext { organizationId: string }
export type Row = Record<string, unknown> & { id: string; organization_id?: string };

export interface Repo<T extends Row> {
  get(ctx: TenantContext, id: string): Promise<T | null>;
  list(ctx: TenantContext, where?: Partial<T>, limit?: number): Promise<T[]>;
  insert(ctx: TenantContext, row: T): Promise<T>;
  update(ctx: TenantContext, id: string, patch: Partial<T>): Promise<T | null>;
}

export interface Db {
  repo<T extends Row>(table: string): Repo<T>;
  ping(): Promise<boolean>;
  close(): Promise<void>;
}

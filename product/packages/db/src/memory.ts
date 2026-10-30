import type { Db, Repo, Row, TenantContext } from './repo.js';

/** In-memory Db for tests and local demos. Enforces tenant isolation like RLS would. */
export function createMemoryDb(): Db {
  const tables = new Map<string, Map<string, Row>>();
  const t = (name: string) => { let m = tables.get(name); if (!m) tables.set(name, (m = new Map())); return m; };
  const visible = (ctx: TenantContext, r: Row) => r.organization_id === undefined || r.organization_id === ctx.organizationId;
  return {
    repo<T extends Row>(name: string): Repo<T> {
      const m = t(name);
      return {
        async get(ctx, id) { const r = m.get(id); return r && visible(ctx, r) ? ({ ...r } as T) : null; },
        async list(ctx, where = {}, limit = 50) {
          return [...m.values()].filter((r) => visible(ctx, r) &&
            Object.entries(where).every(([k, v]) => r[k] === v)).slice(0, limit).map((r) => ({ ...r }) as T);
        },
        async insert(ctx, row) {
          if (m.has(row.id)) throw new Error(`duplicate id ${row.id} in ${name}`);
          const r = { ...row, organization_id: row.organization_id ?? ctx.organizationId };
          if (r.organization_id !== ctx.organizationId) throw new Error('RLS violation: foreign organization_id');
          m.set(row.id, r); return { ...r } as T;
        },
        async update(ctx, id, patch) {
          const r = m.get(id); if (!r || !visible(ctx, r)) return null;
          const n = { ...r, ...patch, id, organization_id: r.organization_id }; m.set(id, n); return { ...n } as T;
        },
      };
    },
    async ping() { return true; },
    async close() { tables.clear(); },
  };
}

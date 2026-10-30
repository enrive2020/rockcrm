import pg from 'pg';
import type { Db, Repo, Row, TenantContext } from './repo.js';

const ident = (s: string) => { if (!/^[a-z_][a-z0-9_]*$/.test(s)) throw new Error(`bad identifier ${s}`); return `"${s}"`; };

/** PostgreSQL Db. Each call runs in a tx with SET LOCAL app.org_id (works with PgBouncer transaction mode). */
export function createPgDb(connectionString: string): Db {
  const pool = new pg.Pool({ connectionString });
  async function tx<R>(ctx: TenantContext, fn: (c: pg.PoolClient) => Promise<R>): Promise<R> {
    const c = await pool.connect();
    try {
      await c.query('BEGIN');
      await c.query(`SELECT set_config('app.org_id', $1, true)`, [ctx.organizationId]);
      const r = await fn(c); await c.query('COMMIT'); return r;
    } catch (e) { await c.query('ROLLBACK'); throw e; } finally { c.release(); }
  }
  return {
    repo<T extends Row>(table: string): Repo<T> {
      const tbl = ident(table);
      return {
        get: (ctx, id) => tx(ctx, async (c) => ((await c.query(`SELECT * FROM ${tbl} WHERE id = $1`, [id])).rows[0] as T) ?? null),
        list: (ctx, where = {}, limit = 50) => tx(ctx, async (c) => {
          const keys = Object.keys(where);
          const cond = keys.length ? 'WHERE ' + keys.map((k, i) => `${ident(k)} = $${i + 1}`).join(' AND ') : '';
          return (await c.query(`SELECT * FROM ${tbl} ${cond} ORDER BY id LIMIT ${Math.min(Math.max(limit, 1), 200)}`,
            Object.values(where))).rows as T[];
        }),
        insert: (ctx, row) => tx(ctx, async (c) => {
          const r: Row = { ...row, organization_id: row.organization_id ?? ctx.organizationId };
          const keys = Object.keys(r);
          return (await c.query(`INSERT INTO ${tbl} (${keys.map(ident).join(',')}) VALUES (${keys.map((_, i) => `$${i + 1}`).join(',')}) RETURNING *`,
            Object.values(r))).rows[0] as T;
        }),
        update: (ctx, id, patch) => tx(ctx, async (c) => {
          const keys = Object.keys(patch).filter((k) => k !== 'id' && k !== 'organization_id');
          if (!keys.length) return ((await c.query(`SELECT * FROM ${tbl} WHERE id = $1`, [id])).rows[0] as T) ?? null;
          return ((await c.query(`UPDATE ${tbl} SET ${keys.map((k, i) => `${ident(k)} = $${i + 2}`).join(',')} WHERE id = $1 RETURNING *`,
            [id, ...keys.map((k) => (patch as Row)[k])])).rows[0] as T) ?? null;
        }),
      };
    },
    async ping() { try { await pool.query('SELECT 1'); return true; } catch { return false; } },
    async close() { await pool.end(); },
  };
}

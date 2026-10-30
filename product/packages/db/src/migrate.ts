import { readdirSync, readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';

export const MIGRATIONS_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', 'migrations');

export function listMigrations(dir = MIGRATIONS_DIR): { name: string; sql: string }[] {
  return readdirSync(dir).filter((f) => /^\d{4}_.+\.sql$/.test(f)).sort()
    .map((name) => ({ name, sql: readFileSync(join(dir, name), 'utf8') }));
}

/** Forward-only migrations tracked in schema_migrations. */
export async function migrate(connectionString: string): Promise<string[]> {
  const c = new pg.Client({ connectionString }); await c.connect();
  try {
    await c.query('CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())');
    const done = new Set((await c.query('SELECT name FROM schema_migrations')).rows.map((r: { name: string }) => r.name));
    const applied: string[] = [];
    for (const m of listMigrations()) {
      if (done.has(m.name)) continue;
      await c.query('BEGIN');
      try { await c.query(m.sql); await c.query('INSERT INTO schema_migrations(name) VALUES ($1)', [m.name]); await c.query('COMMIT'); }
      catch (e) { await c.query('ROLLBACK'); throw e; }
      applied.push(m.name);
    }
    return applied;
  } finally { await c.end(); }
}

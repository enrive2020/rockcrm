import { describe, it, expect } from 'vitest';
import { createMemoryDb, listMigrations } from '@podacha/db';

describe('db', () => {
  it('memory repo isolates tenants', async () => {
    const db = createMemoryDb();
    const a = { organizationId: 'org-a' }, b = { organizationId: 'org-b' };
    await db.repo('venue').insert(a, { id: 'v1', name: 'Кафе' });
    expect(await db.repo('venue').get(a, 'v1')).toMatchObject({ name: 'Кафе' });
    expect(await db.repo('venue').get(b, 'v1')).toBeNull();
    await expect(db.repo('venue').insert(b, { id: 'v2', organization_id: 'org-a' })).rejects.toThrow(/RLS/);
  });
  it('ships the initial migration', () => {
    const m = listMigrations();
    expect(m[0]?.name).toBe('0001_init.sql');
    expect(m[0]?.sql).toMatch(/CREATE TABLE "order"/);
  });
});

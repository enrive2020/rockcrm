import { describe, it, expect, afterAll } from 'vitest';
import { buildApp } from '../src/app.js';

const app = await buildApp();
afterAll(() => app.close());

describe('api', () => {
  it('GET /v1/health returns ok', async () => {
    const res = await app.inject({ method: 'GET', url: '/v1/health' });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toMatchObject({ status: 'ok', db: 'ok' });
  });
  it('unknown route returns RFC 9457 problem', async () => {
    const res = await app.inject({ method: 'GET', url: '/v1/nope' });
    expect(res.statusCode).toBe(404);
    expect(res.headers['content-type']).toMatch(/problem\+json/);
    expect(res.json()).toMatchObject({ code: 'NOT_FOUND' });
  });
});

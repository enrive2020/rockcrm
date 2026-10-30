import { buildApp } from './app.js';
import { createPgDb } from '@podacha/db';

const db = process.env.DATABASE_URL ? createPgDb(process.env.DATABASE_URL) : undefined;
const app = await buildApp({ db, logger: { level: process.env.LOG_LEVEL ?? 'info' } });
await app.listen({ host: process.env.API_HOST ?? '0.0.0.0', port: Number(process.env.API_PORT ?? 3000) });

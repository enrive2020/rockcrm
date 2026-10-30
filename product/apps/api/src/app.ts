import Fastify, { type FastifyInstance, type FastifyServerOptions } from 'fastify';
import { readdirSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { AppError } from '@podacha/shared';
import { createMemoryDb, type Db } from '@podacha/db';

declare module 'fastify' { interface FastifyInstance { db: Db } }

export interface BuildOptions { db?: Db; logger?: FastifyServerOptions['logger'] }
const MODULES_DIR = join(dirname(fileURLToPath(import.meta.url)), 'modules');

/** Auto-loads every apps/api/src/modules/<name>/index.(ts|js) default-exported Fastify plugin under /v1. */
export async function buildApp(opts: BuildOptions = {}): Promise<FastifyInstance> {
  const app = Fastify({ logger: opts.logger ?? false, genReqId: () => `req_${crypto.randomUUID()}` });
  app.decorate('db', opts.db ?? createMemoryDb());

  app.setErrorHandler((err, req, reply) => {
    if (err instanceof AppError) return reply.status(err.status).type('application/problem+json').send(err.toProblem(req.id));
    const e = err as { validation?: unknown; statusCode?: number; message: string };
    if (e.validation) return reply.status(400).type('application/problem+json')
      .send(new AppError('VALIDATION_FAILED', 400, e.message).toProblem(req.id));
    req.log.error(err);
    return reply.status(e.statusCode ?? 500).type('application/problem+json')
      .send(new AppError('INTERNAL', e.statusCode ?? 500, 'Internal error').toProblem(req.id));
  });
  app.setNotFoundHandler((req, reply) =>
    reply.status(404).type('application/problem+json').send(new AppError('NOT_FOUND', 404, 'Route not found').toProblem(req.id)));

  for (const name of readdirSync(MODULES_DIR).sort()) {
    const file = ['index.js', 'index.ts'].map((f) => join(MODULES_DIR, name, f)).find(existsSync);
    if (!file) continue;
    const mod = (await import(pathToFileURL(file).href)) as { default: Parameters<FastifyInstance['register']>[0] };
    await app.register(mod.default, { prefix: '/v1' });
  }
  return app;
}

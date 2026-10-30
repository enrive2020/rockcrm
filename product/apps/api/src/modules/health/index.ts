import type { FastifyPluginAsync } from 'fastify';

/** Module: health. Owner: platform. GET /v1/health — liveness + DB ping. */
const health: FastifyPluginAsync = async (app) => {
  app.get('/health', async () => ({
    status: 'ok' as const,
    db: (await app.db.ping()) ? 'ok' : 'down',
    version: process.env.npm_package_version ?? '0.1.0',
    time: new Date().toISOString(),
  }));
};
export default health;

import type { FastifyPluginAsync } from 'fastify';
import { tenancyRoutes } from './routes.js';

/** Module: tenancy. Owner: platform. Organization + venues (zones/tables/QR: TODO, see README). */
const tenancy: FastifyPluginAsync = async (app) => { await app.register(tenancyRoutes); };
export default tenancy;

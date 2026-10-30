import type { FastifyPluginAsync } from 'fastify';
import { authRoutes } from './routes.js';

/**
 * Module: auth. Owner: platform. app_user, membership, refresh tokens, OTP, invitations, JWT (ES256), RBAC.
 * Other modules: import { requireAuth, ctxOf } from '../auth/guard.js'.
 */
const auth: FastifyPluginAsync = async (app) => { await app.register(authRoutes); };
export default auth;
export { requireAuth, authenticate, ctxOf, type AuthContext } from './guard.js';
export { configureAuth, authConfig, type OtpSender } from './config.js';

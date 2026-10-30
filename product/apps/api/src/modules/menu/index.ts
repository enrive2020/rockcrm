import type { FastifyPluginAsync } from 'fastify';
import { getMenuService } from './service.js';
import { registerMenuRoutes } from './routes.js';

/**
 * Module: menu. Owner: menu. Menus, categories, items, modifiers, allergens, translations, stop-list,
 * and the public menu by table QR token. Other modules call `getMenuService(app.db)`
 * (e.g. orders → `checkAvailability`, integrations → `replaceStopList(..., 'pos')`).
 */
const menu: FastifyPluginAsync = async (app) => {
  registerMenuRoutes(app, getMenuService(app.db));
};
export default menu;
export { MenuService, getMenuService } from './service.js';
export { setQrResolver } from './context.js';

import type { FastifyPluginAsync } from 'fastify';
import { requireAuth, ctxOf } from '../auth/guard.js';
import { parse } from '../auth/schemas.js';
import { orgUpdateBody, venueCreateBody, venueUpdateBody } from './schemas.js';
import * as svc from './service.js';

type VenueParams = { Params: { venueId: string } };

export const tenancyRoutes: FastifyPluginAsync = async (app) => {
  app.get('/organization', { preHandler: requireAuth({ scope: 'org:read' }) }, async (req) =>
    svc.orgDto(await svc.getOrganization(app.db, ctxOf(req).tenant)));

  app.patch('/organization', { preHandler: requireAuth({ scope: 'org:write' }) }, async (req) =>
    svc.orgDto(await svc.updateOrganization(app.db, ctxOf(req).tenant, parse(orgUpdateBody, req.body))));

  app.get('/venues', { preHandler: requireAuth({}) }, async (req) => {
    const { auth, tenant } = ctxOf(req);
    return { data: (await svc.listVenues(app.db, tenant, auth.roles)).map(svc.venueDto) };
  });

  app.post('/venues', { preHandler: requireAuth({ scope: 'venues:create' }) }, async (req, reply) =>
    reply.status(201).send(svc.venueDto(await svc.createVenue(app.db, ctxOf(req).tenant, parse(venueCreateBody, req.body)))));

  app.get<VenueParams>('/venues/:venueId', { preHandler: requireAuth({ scope: 'venues:read', venueParam: 'venueId' }) }, async (req) =>
    svc.venueDto(await svc.getVenue(app.db, ctxOf(req).tenant, req.params.venueId)));

  app.patch<VenueParams>('/venues/:venueId', { preHandler: requireAuth({ scope: 'venues:write', venueParam: 'venueId' }) }, async (req) =>
    svc.venueDto(await svc.updateVenue(app.db, ctxOf(req).tenant, req.params.venueId, parse(venueUpdateBody, req.body))));

  app.delete<VenueParams>('/venues/:venueId', { preHandler: requireAuth({ scope: 'venues:create', venueParam: 'venueId' }) }, async (req, reply) => {
    await svc.deleteVenue(app.db, ctxOf(req).tenant, req.params.venueId);
    return reply.status(204).send();
  });
};

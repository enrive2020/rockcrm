import type { FastifyPluginAsync } from 'fastify';
import { authConfig } from './config.js';
import { ctxOf, requireAuth } from './guard.js';
import {
  acceptInviteBody, changePasswordBody, inviteBody, loginBody, otpRequestBody, otpVerifyBody, parse, refreshBody, signupBody,
  switchOrgBody, updateMemberBody,
} from './schemas.js';
import * as svc from './service.js';
import { ROLES, ROLE_SCOPES } from '@podacha/shared';

type UserParams = { Params: { userId: string } };

export const authRoutes: FastifyPluginAsync = async (app) => {
  app.get('/.well-known/jwks.json', async (_req, reply) =>
    reply.header('cache-control', 'public, max-age=300').send(authConfig().keys.jwks()));

  app.post('/auth/signup', async (req, reply) => reply.status(201).send(await svc.signup(app.db, parse(signupBody, req.body))));

  app.post('/auth/login', async (req) => {
    const b = parse(loginBody, req.body);
    return svc.login(app.db, b.login, b.password, b.organization_id);
  });
  app.post('/auth/otp::request', async (req, reply) =>
    reply.status(202).send(await svc.requestOtp(app.db, parse(otpRequestBody, req.body).phone)));
  app.post('/auth/otp::verify', async (req) => {
    const b = parse(otpVerifyBody, req.body);
    return svc.verifyOtp(app.db, b.phone, b.code, b.organization_id);
  });
  app.post('/auth/refresh', async (req) => svc.refresh(app.db, parse(refreshBody, req.body).refresh_token));
  app.post('/auth/logout', async (req, reply) => {
    await svc.logout(app.db, parse(refreshBody, req.body).refresh_token);
    return reply.status(204).send();
  });
  app.post('/auth/switch-organization', { preHandler: requireAuth() }, async (req) =>
    svc.switchOrg(app.db, ctxOf(req).auth, parse(switchOrgBody, req.body).organization_id));
  app.post('/auth/password', { preHandler: requireAuth() }, async (req, reply) => {
    const b = parse(changePasswordBody, req.body);
    await svc.changePassword(app.db, ctxOf(req).auth, b.current_password, b.new_password);
    return reply.status(204).send();
  });
  app.post('/auth/invitations::accept', async (req) => svc.acceptInvitation(app.db, parse(acceptInviteBody, req.body)));

  app.get('/me', { preHandler: requireAuth() }, async (req) => svc.me(app.db, ctxOf(req).auth));

  app.get('/roles', { preHandler: requireAuth() }, async () => ({ data: ROLES.map((r) => ({ role: r, scopes: ROLE_SCOPES[r] })) }));

  // Users of the organization. owner/admin: everyone; manager: staff of own venues (prd §3.3).
  const canManage = requireAuth();
  app.get('/users', { preHandler: canManage }, async (req) => ({ data: await svc.listMembers(app.db, ctxOf(req).auth) }));
  app.post('/users/invitations', { preHandler: canManage }, async (req, reply) =>
    reply.status(201).send(await svc.invite(app.db, ctxOf(req).auth, parse(inviteBody, req.body))));
  app.patch<UserParams>('/users/:userId', { preHandler: canManage }, async (req) =>
    svc.updateMember(app.db, ctxOf(req).auth, req.params.userId, parse(updateMemberBody, req.body)));
  app.delete<UserParams>('/users/:userId', { preHandler: canManage }, async (req, reply) => {
    await svc.removeMember(app.db, ctxOf(req).auth, req.params.userId);
    return reply.status(204).send();
  });
};

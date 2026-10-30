import { z } from 'zod';
import { AppError, ROLES } from '@podacha/shared';

export const phoneSchema = z.string().transform((s, ctx) => {
  const d = s.replace(/[^\d+]/g, '');
  const m = /^(?:\+7|8|7)?(\d{10})$/.exec(d.replace(/^\+/, '+'));
  if (!m) { ctx.addIssue({ code: 'custom', message: 'Expected Russian phone number' }); return z.NEVER; }
  return `+7${m[1]}`;
});
export const emailSchema = z.string().trim().toLowerCase().email().max(254);
export const passwordSchema = z.string().min(10, 'Minimum 10 characters').max(200);
const innSchema = z.string().regex(/^\d{10}(\d{2})?$/, 'INN must be 10 or 12 digits');

export const signupBody = z.object({
  email: emailSchema.optional(), phone: phoneSchema.optional(),
  password: passwordSchema, full_name: z.string().trim().min(1).max(200),
  organization: z.object({
    legal_name: z.string().trim().min(1).max(300), inn: innSchema,
    kpp: z.string().regex(/^\d{9}$/).optional(), ogrn: z.string().regex(/^\d{13}(\d{2})?$/).optional(),
    tax_system: z.enum(['osn', 'usn_income', 'usn_income_expense', 'patent']).optional(),
  }),
  pd_consent: z.literal(true, { errorMap: () => ({ message: 'Consent to personal data processing (152-FZ) is required' }) }),
}).refine((b) => b.email || b.phone, { message: 'email or phone is required', path: ['email'] });

export const loginBody = z.object({ login: z.string().trim().min(3).max(254), password: z.string().min(1).max(200),
  organization_id: z.string().uuid().optional() });
export const otpRequestBody = z.object({ phone: phoneSchema });
export const otpVerifyBody = z.object({ phone: phoneSchema, code: z.string().regex(/^\d{6}$/), organization_id: z.string().uuid().optional() });
export const refreshBody = z.object({ refresh_token: z.string().min(10).max(500) });
export const switchOrgBody = z.object({ organization_id: z.string().uuid() });
export const changePasswordBody = z.object({ current_password: z.string().max(200).optional(), new_password: passwordSchema });

const venueIds = z.array(z.string().uuid()).max(500).nullable().optional();
export const inviteBody = z.object({ email: emailSchema.optional(), phone: phoneSchema.optional(), role: z.enum(ROLES), venue_ids: venueIds })
  .refine((b) => b.email || b.phone, { message: 'email or phone is required', path: ['email'] });
export const acceptInviteBody = z.object({ token: z.string().min(10).max(500), full_name: z.string().trim().min(1).max(200).optional(),
  password: passwordSchema.optional() });
export const updateMemberBody = z.object({ role: z.enum(ROLES).optional(), venue_ids: venueIds })
  .refine((b) => b.role !== undefined || b.venue_ids !== undefined, { message: 'nothing to update' });

/** zod → AppError(VALIDATION_FAILED) with field errors. */
export function parse<T extends z.ZodTypeAny>(schema: T, data: unknown): z.output<T> {
  const r = schema.safeParse(data ?? {});
  if (r.success) return r.data;
  throw new AppError('VALIDATION_FAILED', 400, 'Validation failed',
    r.error.issues.map((i) => ({ field: i.path.join('.') || '(root)', message: i.message })));
}

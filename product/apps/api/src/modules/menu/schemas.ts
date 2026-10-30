import { z } from 'zod';
import { ALLERGEN_CODES } from '@podacha/shared';

/** API DTOs for the menu module. Money is integer kopecks. */
const kopecks = z.number().int().min(0).max(100_000_000);
const uuid = z.string().uuid();

export const menuCreate = z.object({
  name: z.string().min(1).max(120),
  schedule: z.unknown().nullable().optional(),
  is_active: z.boolean().optional(),
}).strict();
export const menuPatch = menuCreate.partial().strict();

export const categoryCreate = z.object({
  menu_id: uuid,
  parent_id: uuid.nullable().optional(),
  name: z.string().min(1).max(120),
  sort: z.number().int().optional(),
  is_visible: z.boolean().optional(),
}).strict();
export const categoryPatch = categoryCreate.omit({ menu_id: true }).partial().strict();

export const itemCreate = z.object({
  category_id: uuid,
  name: z.string().min(1).max(120),
  description: z.string().max(1000).nullable().optional(),
  price: kopecks,
  weight_g: z.number().int().min(0).nullable().optional(),
  volume_ml: z.number().int().min(0).nullable().optional(),
  kcal: z.number().int().min(0).nullable().optional(),
  tags: z.array(z.string().min(1).max(40)).max(30).optional(),
  upsell_item_ids: z.array(uuid).max(20).optional(),
  vat_rate: z.enum(['none', '0', '5', '7', '10', '22']).optional(),
  marking_required: z.boolean().optional(),
  image_file_id: uuid.nullable().optional(),
  sort: z.number().int().optional(),
  hidden: z.boolean().optional(),
  allergens: z.array(z.enum(ALLERGEN_CODES)).optional(),
  may_contain: z.array(z.enum(ALLERGEN_CODES)).optional(),
}).strict();
export const itemPatch = itemCreate.partial().strict();
export type ItemCreate = z.infer<typeof itemCreate>;
export type ItemPatch = z.infer<typeof itemPatch>;

export const itemListQuery = z.object({
  category_id: uuid.optional(),
  q: z.string().max(100).optional(),
  updated_since: z.string().datetime({ offset: true }).optional(),
  include_deleted: z.coerce.boolean().optional(),
  limit: z.coerce.number().int().min(1).max(200).default(50),
  cursor: z.string().regex(/^\d+$/).optional(),
});

export const modifierGroupCreate = z.object({
  name: z.string().min(1).max(120),
  min_select: z.number().int().min(0).max(50).default(0),
  max_select: z.number().int().min(1).max(50).default(1),
}).strict().refine((g) => g.min_select <= g.max_select, { message: 'min_select must be <= max_select', path: ['min_select'] });
export const modifierGroupPatch = z.object({
  name: z.string().min(1).max(120).optional(),
  min_select: z.number().int().min(0).max(50).optional(),
  max_select: z.number().int().min(1).max(50).optional(),
}).strict();

export const modifierCreate = z.object({
  name: z.string().min(1).max(120),
  price_delta: z.number().int().min(-10_000_000).max(10_000_000).default(0),
  sort: z.number().int().optional(),
}).strict();
export const modifierPatch = z.object({
  name: z.string().min(1).max(120).optional(),
  price_delta: z.number().int().min(-10_000_000).max(10_000_000).optional(),
  sort: z.number().int().optional(),
}).strict();

export const itemModifierGroupsPut = z.object({ group_ids: z.array(uuid).max(20) }).strict();

export const TRANSLATABLE = {
  menu_item: ['name', 'description'],
  menu_category: ['name'],
  modifier_group: ['name'],
  modifier: ['name'],
} as const;
export const locale = z.string().regex(/^[a-z]{2}(-[A-Z]{2})?$/);
export const translationPut = z.object({
  entity_type: z.enum(['menu_item', 'menu_category', 'modifier_group', 'modifier']),
  entity_id: uuid,
  field: z.string(),
  locale,
  text: z.string().min(1).max(2000),
  status: z.enum(['machine', 'reviewed']).default('reviewed'),
}).strict().refine((t) => (TRANSLATABLE[t.entity_type] as readonly string[]).includes(t.field),
  { message: 'field is not translatable for this entity_type', path: ['field'] });
export const translationsPut = z.object({ items: z.array(translationPut).min(1).max(500) }).strict();

export const stopListEntryInput = z.object({
  item_id: uuid.optional(),
  modifier_id: uuid.optional(),
  /** Contract name `balance`; null = fully unavailable. */
  balance: z.number().min(0).nullable().optional(),
  reason: z.string().max(200).nullable().optional(),
  valid_until: z.string().datetime({ offset: true }).nullable().optional(),
}).strict().refine((e) => !!e.item_id !== !!e.modifier_id, { message: 'exactly one of item_id/modifier_id is required' });
export type StopListInput = z.infer<typeof stopListEntryInput>;
export const stopListPut = z.object({ items: z.array(stopListEntryInput).max(1000) }).strict();

export const publicMenuQuery = z.object({ locale: locale.optional() });

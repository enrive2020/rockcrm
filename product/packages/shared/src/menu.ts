/** Public DTOs of the menu module (guest menu, availability). Owner: menu. */
export const ALLERGEN_CODES = [
  'gluten', 'crustaceans', 'eggs', 'fish', 'peanuts', 'soy', 'milk', 'nuts',
  'celery', 'mustard', 'sesame', 'sulphites', 'lupin', 'molluscs',
] as const;
export type AllergenCode = (typeof ALLERGEN_CODES)[number];

export interface PublicModifierOption { id: string; name: string; price: number; available: boolean }
export interface PublicModifierGroup { id: string; name: string; min: number; max: number; options: PublicModifierOption[] }
export interface PublicMenuItem {
  id: string; external_id: string | null; category_id: string; name: string; description: string;
  price: number; weight_g: number | null; volume_ml: number | null; kcal: number | null;
  allergens: AllergenCode[]; tags: string[]; photo_url: string | null; available: boolean;
  remaining_qty: number | null; modifier_groups: PublicModifierGroup[]; updated_at: string;
}
export interface PublicMenuCategory { id: string; parent_id: string | null; name: string; items: PublicMenuItem[] }
export interface PublicMenu { version: string; locale: string; venue_id: string; categories: PublicMenuCategory[] }

/** Result of resolving a table QR token (implemented by the tenancy module). */
export interface QrResolution { organizationId: string; venueId: string; tableId: string | null; isActive: boolean }

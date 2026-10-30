import { describe, it, expect } from 'vitest';
import { entitySchemas, orderSchema, uuidv7, rub, AppError } from '@podacha/shared';

describe('shared', () => {
  it('has a schema per table', () => { expect(Object.keys(entitySchemas).length).toBeGreaterThanOrEqual(60); });
  it('validates order enums', () => {
    expect(orderSchema.shape.status.safeParse('submitted').success).toBe(true);
    expect(orderSchema.shape.status.safeParse('bogus').success).toBe(false);
  });
  it('uuidv7 is time-ordered', () => { expect(uuidv7(1) < uuidv7(2)).toBe(true); expect(uuidv7()).toMatch(/^[0-9a-f-]{36}$/); });
  it('money & problems', () => { expect(rub(12.5)).toBe(1250); expect(new AppError('NOT_FOUND', 404, 'x').toProblem('r').code).toBe('NOT_FOUND'); });
});

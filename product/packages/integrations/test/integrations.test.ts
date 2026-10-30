import { describe, it, expect } from 'vitest';
import { MockPos, MockPayments } from '@podacha/integrations';

describe('integrations mocks', () => {
  it('pos rejects stop-listed items', async () => {
    const pos = new MockPos(); pos.stop.add('i1');
    await expect(pos.createOrder('v', { orderId: 'o1', lines: [{ externalItemId: 'i1', qty: 1, price: 45000 }] })).rejects.toThrow();
  });
  it('payments are idempotent', async () => {
    const p = new MockPayments();
    const a = await p.createPayment({ idempotencyKey: 'k', amount: 100, description: '', orderId: 'o', method: 'sbp' });
    const b = await p.createPayment({ idempotencyKey: 'k', amount: 100, description: '', orderId: 'o', method: 'sbp' });
    expect(a.providerPaymentId).toBe(b.providerPaymentId);
    p.settle(a.providerPaymentId); expect((await p.getPayment(a.providerPaymentId)).status).toBe('succeeded');
  });
});

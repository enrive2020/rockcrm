/**
 * Adapter interfaces (docs/product/integrations.md). Core depends on these only.
 * POS is the source of truth for menu, prices, stop-lists. Money = integer kopecks.
 */
export interface PosMenuItem { externalId: string; name: string; price: number; categoryExternalId?: string; vatRate?: number }
export interface PosMenu { categories: { externalId: string; name: string }[]; items: PosMenuItem[] }
export interface PosOrderLine { externalItemId: string; qty: number; price: number; modifiers?: { externalId: string; qty: number }[] }
export interface PosOrder { orderId: string; tableExternalId?: string; lines: PosOrderLine[]; comment?: string }

export interface PosAdapter {
  readonly system: 'iiko' | 'poster' | 'rkeeper' | 'quickresto' | 'mock';
  fetchMenu(venueExternalId: string): Promise<PosMenu>;
  fetchStopList(venueExternalId: string): Promise<string[]>;
  createOrder(venueExternalId: string, order: PosOrder): Promise<{ posOrderId: string }>;
}

export type PaymentStatus = 'pending' | 'succeeded' | 'canceled' | 'refunded';
export interface CreatePaymentInput { idempotencyKey: string; amount: number; description: string; orderId: string; method: 'sbp' }
export interface PaymentResult { providerPaymentId: string; status: PaymentStatus; sbpQrPayload?: string; confirmationUrl?: string }

export interface PaymentAdapter {
  readonly provider: 'yookassa' | 'tbank' | 'cloudpayments' | 'mock';
  createPayment(input: CreatePaymentInput): Promise<PaymentResult>;
  getPayment(providerPaymentId: string): Promise<PaymentResult>;
  refund(providerPaymentId: string, amount: number, idempotencyKey: string): Promise<PaymentResult>;
  /** Verify webhook authenticity; providers without signatures must re-request status. */
  verifyWebhook(headers: Record<string, string | undefined>, rawBody: string): Promise<boolean>;
}

export class MockPos implements PosAdapter {
  readonly system = 'mock' as const;
  stop = new Set<string>(); orders: PosOrder[] = [];
  constructor(public menu: PosMenu = { categories: [{ externalId: 'c1', name: 'Супы' }],
    items: [{ externalId: 'i1', name: 'Борщ', price: 45000, categoryExternalId: 'c1' }] }) {}
  async fetchMenu() { return structuredClone(this.menu); }
  async fetchStopList() { return [...this.stop]; }
  async createOrder(_v: string, order: PosOrder) {
    const bad = order.lines.find((l) => this.stop.has(l.externalItemId));
    if (bad) throw new Error(`item ${bad.externalItemId} is in stop-list`);
    this.orders.push(order); return { posOrderId: `pos-${this.orders.length}` };
  }
}

export class MockPayments implements PaymentAdapter {
  readonly provider = 'mock' as const;
  private byKey = new Map<string, PaymentResult>(); private byId = new Map<string, PaymentResult & { amount: number }>();
  async createPayment(i: CreatePaymentInput) {
    const prev = this.byKey.get(i.idempotencyKey); if (prev) return prev;
    if (!Number.isSafeInteger(i.amount) || i.amount <= 0) throw new Error('amount must be positive kopecks');
    const r: PaymentResult = { providerPaymentId: `mock_${this.byId.size + 1}`, status: 'pending', sbpQrPayload: `https://qr.nspk.ru/mock${this.byId.size + 1}` };
    this.byKey.set(i.idempotencyKey, r); this.byId.set(r.providerPaymentId, { ...r, amount: i.amount }); return r;
  }
  async getPayment(id: string) { const p = this.byId.get(id); if (!p) throw new Error('not found'); const { amount: _a, ...r } = p; return r; }
  async refund(id: string) { const p = this.byId.get(id); if (!p) throw new Error('not found'); p.status = 'refunded'; return this.getPayment(id); }
  async verifyWebhook() { return true; }
  /** test helper */
  settle(id: string) { const p = this.byId.get(id); if (p) p.status = 'succeeded'; }
}

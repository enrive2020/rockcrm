/** RFC 9457 problem details + stable `code` (see docs/product/api-contract.md). Clients branch on `code` only. */
export const ERROR_CODES = [
  'VALIDATION_FAILED', 'UNAUTHORIZED', 'FORBIDDEN', 'NOT_FOUND', 'CONFLICT', 'RATE_LIMITED',
  'IDEMPOTENCY_KEY_REQUIRED', 'ORDER_ITEM_UNAVAILABLE', 'BILL_CHANGED', 'MENU_MANAGED_BY_POS',
  'AI_QUOTA_EXCEEDED', 'QR_REVOKED', 'INTERNAL',
] as const;
export type ErrorCode = (typeof ERROR_CODES)[number];

export interface Problem {
  type: string; title: string; status: number; code: ErrorCode; detail?: string;
  request_id?: string; errors?: { field: string; message: string }[];
}

export class AppError extends Error {
  constructor(public readonly code: ErrorCode, public readonly status: number, message: string,
    public readonly fieldErrors?: Problem['errors']) { super(message); }
  toProblem(requestId?: string): Problem {
    return { type: `https://api.podacha.ai/problems/${this.code.toLowerCase()}`, title: this.code,
      status: this.status, code: this.code, detail: this.message, request_id: requestId, errors: this.fieldErrors };
  }
}

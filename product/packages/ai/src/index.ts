/**
 * LLM adapter layer (docs/product/ai-agents.md). One interface for all providers.
 * Only Russian providers may receive personal data (152-ФЗ): yandexgpt, gigachat, selfhosted.
 */
export type ProviderId = 'yandexgpt' | 'gigachat' | 'selfhosted' | 'mock';
export const RU_PROVIDERS: readonly ProviderId[] = ['yandexgpt', 'gigachat', 'selfhosted', 'mock'];

export interface ChatMessage { role: 'system' | 'user' | 'assistant' | 'tool'; content: string; toolCallId?: string }
export interface ToolSpec { name: string; description: string; parameters: Record<string, unknown> }
export interface ToolCall { id: string; name: string; arguments: Record<string, unknown> }
export interface ChatRequest { messages: ChatMessage[]; tools?: ToolSpec[]; temperature?: number; maxTokens?: number; containsPii?: boolean }
export interface ChatResponse { content: string; toolCalls: ToolCall[]; usage: { inputTokens: number; outputTokens: number }; provider: ProviderId; model: string }

export interface LLMProvider {
  readonly id: ProviderId;
  chat(req: ChatRequest): Promise<ChatResponse>;
  embed(texts: string[]): Promise<number[][]>;
}

/** Deterministic provider for CI/demos: echoes, and calls a tool if the last user message is `/tool <name>`. */
export class MockProvider implements LLMProvider {
  readonly id = 'mock' as const;
  async chat(req: ChatRequest): Promise<ChatResponse> {
    const last = [...req.messages].reverse().find((m) => m.role === 'user')?.content ?? '';
    const tm = last.match(/^\/tool (\w+)/);
    const toolCalls = tm && req.tools?.some((t) => t.name === tm[1]) ? [{ id: 'call_1', name: tm[1]!, arguments: {} }] : [];
    const content = toolCalls.length ? '' : `mock: ${last}`;
    const tokens = (s: string) => Math.ceil(s.length / 4);
    return { content, toolCalls, provider: 'mock', model: 'mock-1',
      usage: { inputTokens: req.messages.reduce((n, m) => n + tokens(m.content), 0), outputTokens: tokens(content) } };
  }
  async embed(texts: string[]): Promise<number[][]> {
    return texts.map((t) => { const v = new Array(8).fill(0); for (let i = 0; i < t.length; i++) v[i % 8] += t.charCodeAt(i) / 1000; return v; });
  }
}

/** Tries providers in order; refuses non-RU providers for PII requests. */
export class FallbackProvider implements LLMProvider {
  constructor(private readonly chain: LLMProvider[]) { if (!chain.length) throw new Error('empty provider chain'); }
  get id(): ProviderId { return this.chain[0]!.id; }
  async chat(req: ChatRequest): Promise<ChatResponse> {
    let lastErr: unknown;
    for (const p of this.chain) {
      if (req.containsPii && !RU_PROVIDERS.includes(p.id)) continue;
      try { return await p.chat(req); } catch (e) { lastErr = e; }
    }
    throw lastErr ?? new Error('no eligible provider');
  }
  embed(texts: string[]) { return this.chain[0]!.embed(texts); }
}

const PHONE = /(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}/g;
const EMAIL = /[\w.+-]+@[\w-]+\.[\w.]+/g;
/** Masks phones/emails before sending to an LLM; returns map to restore inside tool calls. */
export function maskPii(text: string): { masked: string; vault: Map<string, string> } {
  const vault = new Map<string, string>(); let n = 0;
  const sub = (m: string) => { const k = `[[PII_${++n}]]`; vault.set(k, m); return k; };
  return { masked: text.replace(PHONE, sub).replace(EMAIL, sub), vault };
}

/** Fills {{price:ITEM_ID}} placeholders from DB prices (kopecks). LLM never states prices itself. */
export function renderPrices(text: string, priceOf: (itemId: string) => number | undefined): string {
  return text.replace(/\{\{price:([\w-]+)\}\}/g, (_, id: string) => {
    const k = priceOf(id); if (k === undefined) throw new Error(`unknown item ${id}`);
    return `${(k / 100).toLocaleString('ru-RU')} ₽`;
  });
}

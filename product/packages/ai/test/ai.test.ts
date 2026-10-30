import { describe, it, expect } from 'vitest';
import { MockProvider, FallbackProvider, maskPii, renderPrices, type LLMProvider } from '@podacha/ai';

describe('ai', () => {
  it('mock provider is deterministic and calls tools', async () => {
    const p = new MockProvider();
    const r = await p.chat({ messages: [{ role: 'user', content: '/tool get_menu' }], tools: [{ name: 'get_menu', description: '', parameters: {} }] });
    expect(r.toolCalls[0]?.name).toBe('get_menu');
  });
  it('fallback skips failing provider', async () => {
    const bad: LLMProvider = { id: 'yandexgpt', chat: async () => { throw new Error('down'); }, embed: async () => [] };
    const r = await new FallbackProvider([bad, new MockProvider()]).chat({ messages: [{ role: 'user', content: 'hi' }] });
    expect(r.provider).toBe('mock');
  });
  it('masks pii and renders db prices', () => {
    const { masked, vault } = maskPii('Звоните +7 999 123-45-67');
    expect(masked).not.toMatch(/999/); expect(vault.size).toBe(1);
    expect(renderPrices('Борщ {{price:i1}}', () => 45000)).toMatch(/450/);
  });
});

import { it, expect } from 'vitest';
import { handleMessage, type ChannelAdapter } from '../src/index.js';

it('routes inbound message through llm to channel', async () => {
  const sent: string[] = [];
  const out: ChannelAdapter = { channel: 'telegram', send: async (_c, t) => { sent.push(t); }, verifyWebhook: () => true };
  await handleMessage({ channel: 'telegram', chatId: '1', text: 'Столик на 2', venueId: 'v' }, out);
  expect(sent).toEqual(['mock: Столик на 2']);
});

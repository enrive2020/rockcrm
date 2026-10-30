/**
 * Channel workers (Telegram / VK / MAX). One ChannelAdapter per messenger; the agent logic
 * (booking admin, AI waiter) is channel-agnostic. Outgoing messages go through a rate-limited queue.
 */
import { MockProvider, type LLMProvider } from '@podacha/ai';

export type Channel = 'telegram' | 'vk' | 'max';
export interface InboundMessage { channel: Channel; chatId: string; text: string; venueId: string }
export interface ChannelAdapter {
  readonly channel: Channel;
  send(chatId: string, text: string): Promise<void>;
  verifyWebhook(headers: Record<string, string | undefined>, rawBody: string): boolean;
}

export async function handleMessage(msg: InboundMessage, out: ChannelAdapter, llm: LLMProvider = new MockProvider()): Promise<void> {
  const r = await llm.chat({ containsPii: true, messages: [
    { role: 'system', content: 'Ты администратор ресторана. Бронь создаётся только после явного «да».' },
    { role: 'user', content: msg.text },
  ] });
  await out.send(msg.chatId, r.content);
}

export class TelegramAdapter implements ChannelAdapter {
  readonly channel = 'telegram' as const;
  constructor(private token: string, private secret: string) {}
  verifyWebhook(h: Record<string, string | undefined>) { return h['x-telegram-bot-api-secret-token'] === this.secret; }
  async send(chatId: string, text: string) {
    const r = await fetch(`https://api.telegram.org/bot${this.token}/sendMessage`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ chat_id: chatId, text }) });
    if (!r.ok) throw new Error(`telegram send failed: ${r.status}`);
  }
}

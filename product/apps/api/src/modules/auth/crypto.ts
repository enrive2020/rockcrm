import { createHash, randomBytes, randomInt, scrypt as scryptCb, timingSafeEqual } from 'node:crypto';
import { promisify } from 'node:util';

const scrypt = promisify(scryptCb) as (pw: string, salt: Buffer, len: number, o: object) => Promise<Buffer>;
const N = 16384, r = 8, p = 1, LEN = 32;

/** Format: scrypt$N$r$p$saltB64$hashB64 */
export async function hashPassword(pw: string): Promise<string> {
  const salt = randomBytes(16);
  const h = await scrypt(pw, salt, LEN, { N, r, p });
  return `scrypt$${N}$${r}$${p}$${salt.toString('base64')}$${h.toString('base64')}`;
}

export async function verifyPassword(pw: string, stored: string | null | undefined): Promise<boolean> {
  if (!stored) { await scrypt(pw, randomBytes(16), LEN, { N, r, p }); return false; } // equalize timing
  const [alg, n, rr, pp, salt, hash] = stored.split('$');
  if (alg !== 'scrypt' || !salt || !hash) return false;
  const expected = Buffer.from(hash, 'base64');
  const h = await scrypt(pw, Buffer.from(salt, 'base64'), expected.length, { N: +n!, r: +rr!, p: +pp! });
  return timingSafeEqual(h, expected);
}

export const sha256 = (s: string) => createHash('sha256').update(s).digest('hex');
export const randomToken = (bytes = 32) => randomBytes(bytes).toString('base64url');
export const otpCode = () => String(randomInt(0, 1_000_000)).padStart(6, '0');
export function safeEqualHex(a: string, b: string): boolean {
  const x = Buffer.from(a, 'hex'), y = Buffer.from(b, 'hex');
  return x.length === y.length && timingSafeEqual(x, y);
}

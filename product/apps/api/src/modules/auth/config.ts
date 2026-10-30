import { KeyStore } from './jwt.js';

/** Delivers OTP codes (SMS / Telegram / MAX via Channel Gateway). Default: log to stdout in dev only. */
export interface OtpSender { send(phone: string, code: string): Promise<void> }

export interface AuthConfig {
  issuer: string;
  accessTtlSec: number;
  refreshTtlSec: number;
  otpTtlSec: number;
  otpMaxAttempts: number;
  otpMaxPerWindow: number;
  otpWindowSec: number;
  invitationTtlSec: number;
  keys: KeyStore;
  otpSender: OtpSender;
  now: () => number;
}

let cfg: AuthConfig | undefined;

function defaults(): AuthConfig {
  return {
    issuer: process.env.AUTH_ISSUER ?? 'https://api.podacha.ai',
    accessTtlSec: 15 * 60,
    refreshTtlSec: 30 * 24 * 3600,
    otpTtlSec: 5 * 60, otpMaxAttempts: 5, otpMaxPerWindow: 5, otpWindowSec: 15 * 60,
    invitationTtlSec: 7 * 24 * 3600,
    keys: KeyStore.fromEnv(),
    otpSender: {
      async send(phone, code) {
        if (process.env.NODE_ENV === 'production') throw new Error('OTP sender is not configured');
        console.info(`[auth] OTP for ${phone.slice(0, 4)}***: ${code}`);
      },
    },
    now: () => Date.now(),
  };
}

export function authConfig(): AuthConfig { return (cfg ??= defaults()); }
/** Override settings (tests, server bootstrap). Must be called before the app handles requests. */
export function configureAuth(patch: Partial<AuthConfig>): AuthConfig { cfg = { ...authConfig(), ...patch }; return cfg; }

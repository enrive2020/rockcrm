import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';

const r = (p: string) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  resolve: {
    alias: {
      '@podacha/shared': r('./packages/shared/src/index.ts'),
      '@podacha/db': r('./packages/db/src/index.ts'),
      '@podacha/ai': r('./packages/ai/src/index.ts'),
      '@podacha/integrations': r('./packages/integrations/src/index.ts'),
    },
  },
  test: { include: ['apps/*/test/**/*.test.ts', 'packages/*/test/**/*.test.ts'], environment: 'node' },
});

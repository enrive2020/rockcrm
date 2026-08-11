import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  // Адрес бэкенда берём из .env, а не из process.env: так конфигурация
  // одного проекта живёт в одном месте и не требует типов Node.
  const env = loadEnv(mode, '.', 'VITE_');

  return {
    plugins: [react()],
    server: {
      port: 5173,
      // Бэкенд пишется параллельно и живёт на 8000. Прокси нужен, чтобы фронтенд
      // ходил в /api/v1 одинаково и с моками, и без них — без CORS и правки кода.
      proxy: {
        '/api': {
          target: env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
  };
});

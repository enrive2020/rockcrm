import { defineConfig } from 'vite';
import preact from '@preact/preset-vite';
export default defineConfig({ plugins: [preact()], server: { proxy: { '/v1': 'http://localhost:3000' } } });

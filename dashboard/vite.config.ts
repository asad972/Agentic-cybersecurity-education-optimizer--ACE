import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// /api/... goes to the FastAPI service started on port 8000 (see api/README.md).
const api = { target: 'http://127.0.0.1:8000', changeOrigin: true, rewrite: (path: string) => path.replace(/^\/api/, '') };

export default defineConfig({ plugins: [react()], server: { proxy: { '/api': api } }, preview: { proxy: { '/api': api } } });

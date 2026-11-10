import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// base 用相对路径，Electron 打包后可直接 file:// 加载
export default defineConfig({
  plugins: [react()],
  base: './',
  server: { port: 5173, strictPort: true },
  build: { outDir: 'dist', emptyOutDir: true }
});

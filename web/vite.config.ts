import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
// /api va al backend FastAPI. Il callback del login OAC arriva invece direttamente al backend
// (127.0.0.1:8000/oac-mcp-connect/callback/…), che rimanda a /api/auth/finish: lì, passando dal proxy,
// il cookie di sessione si imposta su localhost:5173.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { '/api': 'http://localhost:8000' },
  },
})

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Bind IPv4 as well so links using 127.0.0.1 (e.g. REPORT_VERIFICATION_BASE_URL) work on Windows.
  server: {
    host: true,
  },
})

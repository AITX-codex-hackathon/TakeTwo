import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  envDir: '..',
  server: {
    proxy: {
      '/jobs': 'http://localhost:5050',
    },
  },
})

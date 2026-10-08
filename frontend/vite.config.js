import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
    plugins: [react()],
    server: {
        host: '0.0.0.0',
        port: 5173,
    },
    // Test unit dan komponen (npm test). jsdom menyediakan DOM untuk komponen React.
    test: {
        environment: 'jsdom',
        include: ['src/**/*.test.{js,jsx}'],
    },
})
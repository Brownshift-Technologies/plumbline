/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { configDefaults } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    globals: true,
    // web/e2e/*.spec.ts are Playwright specs, run via `npm run test:e2e`
    // (playwright.config.ts), not vitest -- without this exclude, vitest's
    // default `**/*.spec.ts` include picks them up too and crashes trying
    // to call Playwright's `test()` outside a Playwright runner.
    exclude: [...configDefaults.exclude, 'e2e/**'],
  },
})

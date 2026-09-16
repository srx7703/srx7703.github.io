// @ts-check
import { defineConfig } from 'astro/config';
import mdx from '@astrojs/mdx';

export default defineConfig({
  site: 'https://srx7703.github.io',
  integrations: [mdx()],
  vite: {
    // data/ lives one level above the site; allow the dev server to read it
    server: { fs: { allow: ['..'] } },
  },
});

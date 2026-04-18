import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';

// https://astro.build/config
export default defineConfig({
  site: 'https://RoryQ.github.io',
  base: '/holyfuckingshit40000',
  integrations: [tailwind()],
});

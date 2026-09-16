import { defineCollection } from 'astro:content';
import { z } from 'astro/zod';
import { glob } from 'astro/loaders';

const projects = defineCollection({
  loader: glob({ pattern: '**/*.mdx', base: './src/content/projects' }),
  schema: z.object({
    title: z.string(),
    summary: z.string(),
    status: z.enum(['live', 'case-study', 'in-progress']),
    domain: z.array(z.string()),
    tools: z.array(z.string()),
    featured: z.boolean().default(false),
    order: z.number().default(100),
    started: z.string(),
  }),
});

export const collections = { projects };

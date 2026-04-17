import { defineCollection, z } from 'astro:content';

const postsCollection = defineCollection({
  type: 'content',
  schema: z.object({
    title: z.string(),
    date: z.string(),
    artist: z.string().optional().nullable(),
    album: z.string().optional().nullable(),
    spotify_url: z.string().optional().nullable(),
    apple_music_url: z.string().optional().nullable(),
    youtube_url: z.string().optional().nullable(),
    original_url: z.string().optional().nullable(),
    archived_url: z.string().optional().nullable(),
  }),
});

export const collections = {
  'posts': postsCollection,
};

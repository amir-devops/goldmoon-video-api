import {defineField, defineType} from 'sanity'

export const tour = defineType({
  name: 'tour',
  title: 'Goldmoon Tour',
  type: 'document',
  fields: [
    defineField({
      name: 'title',
      title: 'Tour Title',
      type: 'string',
      validation: (rule) => rule.required().max(80),
    }),
    defineField({
      name: 'slug',
      title: 'Slug',
      type: 'slug',
      options: {source: 'title', maxLength: 96},
      validation: (rule) => rule.required(),
    }),
    defineField({
      name: 'images',
      title: 'Tour Images',
      type: 'array',
      of: [
        {
          type: 'image',
          options: {hotspot: true},
          fields: [
            defineField({
              name: 'alt',
              title: 'Alt Text',
              type: 'string',
              validation: (rule) => rule.max(120),
            }),
          ],
        },
      ],
      validation: (rule) => rule.min(2).max(12),
    }),
    defineField({
      name: 'text_scene_1',
      title: 'Video Hook Text',
      type: 'string',
      description: 'English only. Max 60 characters.',
      validation: (rule) => rule.required().max(60),
    }),
    defineField({
      name: 'text_scene_2',
      title: 'Video CTA Text',
      type: 'string',
      description: 'English only. Max 60 characters.',
      validation: (rule) => rule.required().max(60),
    }),
    defineField({
      name: 'bg_music',
      title: 'Background Music',
      type: 'string',
      options: {
        list: [
          {title: 'Desert Ambient', value: 'desert_ambient'},
          {title: 'Luxury Chill', value: 'luxury_chill'},
          {title: 'Cinematic Epic', value: 'cinematic_epic'},
        ],
        layout: 'radio',
      },
      initialValue: 'luxury_chill',
    }),
    defineField({
      name: 'style',
      title: 'Video Style',
      type: 'string',
      description: 'Visual preset applied when rendering this tour video.',
      options: {
        list: [
          {title: 'Luxury Gold', value: 'luxury_gold'},
          {title: 'Cinematic Dark', value: 'cinematic_dark'},
          {title: 'Modern Vibe', value: 'modern_vibe'},
          {title: 'Minimal Clean', value: 'minimal_clean'},
          {title: 'Vintage Film', value: 'vintage_film'},
          {title: 'Desert Safari', value: 'desert_safari'},
          {title: 'Night Chill', value: 'night_chill'},
          {title: 'Dreamy Soft', value: 'dreamy_soft'},
        ],
        layout: 'dropdown',
      },
      initialValue: 'desert_safari',
    }),
  ],
  preview: {
    select: {
      title: 'title',
      media: 'images.0',
      subtitle: 'slug.current',
      style: 'style',
    },
    prepare({title, media, subtitle, style}) {
      return {
        title,
        media,
        subtitle: [subtitle, style].filter(Boolean).join(' · '),
      }
    },
  },
})

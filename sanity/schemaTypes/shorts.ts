import {defineField, defineType} from 'sanity'

export const shorts = defineType({
  name: 'shorts',
  title: 'Shorts Library',
  type: 'document',
  fields: [
    defineField({
      name: 'locationName',
      title: 'Location Name',
      type: 'string',
      validation: (rule) => rule.required(),
    }),
    defineField({
      name: 'mainImage',
      title: 'Main Image',
      type: 'image',
      options: {hotspot: true},
    }),
    defineField({
      name: 'processed',
      title: 'Processed',
      type: 'boolean',
      initialValue: false,
    }),
  ],
  preview: {
    select: {
      title: 'locationName',
      media: 'mainImage',
      subtitle: 'processed',
    },
    prepare({title, media, subtitle}) {
      return {
        title,
        media,
        subtitle: subtitle ? 'Processed' : 'Pending',
      }
    },
  },
})

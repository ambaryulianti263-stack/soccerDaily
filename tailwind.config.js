/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./layouts/**/*.html", "./content/**/*.md"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Roboto', 'sans-serif'],
        display: ['Libre Franklin', 'sans-serif'],
      },
      colors: {
        news: {
            red: '#d0021b',
            dark: '#101010',
            gray: '#f2f2f2',
        }
      }
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
}
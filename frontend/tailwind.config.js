/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
      },
      colors: {
        'bg-color': 'var(--bg-color)',
        'sidebar-bg': 'var(--sidebar-bg)',
        'card-bg': 'var(--card-bg)',
        'card-hover': 'var(--card-hover)',
        'text-main': 'var(--text-main)',
        'text-dim': 'var(--text-dim)',
        'text-muted': 'var(--text-muted)',
      },
      borderColor: {
        'subtle': 'var(--border-subtle)',
        'hover': 'var(--border-hover)',
      }
    },
  },
  plugins: [
    require("tailwindcss-animate"),
  ],
}

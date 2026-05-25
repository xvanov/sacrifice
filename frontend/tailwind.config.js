/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./App.{js,jsx,ts,tsx}",
    "./components/**/*.{js,jsx,ts,tsx}",
    "./screens/**/*.{js,jsx,ts,tsx}",
  ],
  presets: [require("nativewind/preset")],
  theme: {
    extend: {
      colors: {
        codex: {
          bg: '#F0ECE4',
          surface: '#FFFFFF',
          border: '#E8DFC9',
          muted: '#85796A',
          text: '#0D0B08',
          'text-secondary': '#3A3327',
          accent: '#8A2A1C',
          'accent-light': '#A53C2E',
          dark: '#14110D',
          'dark-light': '#2A241B',
        },
      },
      fontFamily: {
        serif: ['CormorantGaramond_400Regular', 'Georgia', 'serif'],
        'serif-light': ['CormorantGaramond_300Light', 'Georgia', 'serif'],
        'serif-medium': ['CormorantGaramond_500Medium', 'Georgia', 'serif'],
        'serif-italic': ['CormorantGaramond_400Regular_Italic', 'Georgia', 'serif'],
        sans: ['Inter_400Regular', 'system-ui', 'sans-serif'],
        'sans-medium': ['Inter_500Medium', 'system-ui', 'sans-serif'],
        'sans-bold': ['Inter_700Bold', 'system-ui', 'sans-serif'],
        mono: ['JetBrainsMono_400Regular', 'monospace'],
      },
    },
  },
  plugins: [],
}

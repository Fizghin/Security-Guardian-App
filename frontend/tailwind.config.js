/** @type {import('tailwindcss').Config} */
export default {
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
        extend: {
            colors: {
                cyan: {
                    DEFAULT: '#00f0ff',
                    dim: 'rgba(0, 240, 255, 0.2)',
                },
                magenta: {
                    DEFAULT: '#ff00ff',
                    dim: 'rgba(255, 0, 255, 0.2)',
                },
                electric: {
                    DEFAULT: '#0080ff',
                    dim: 'rgba(0, 128, 255, 0.2)',
                },
                dark: {
                    bg: '#050510',
                    panel: 'rgba(10, 10, 20, 0.8)',
                }
            },
            fontFamily: {
                orbitron: ['Orbitron', 'sans-serif'],
                rajdhani: ['Rajdhani', 'sans-serif'],
                mono: ['Share Tech Mono', 'monospace'],
            },
            animation: {
                'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
                'glitch': 'glitch 1s linear infinite',
            },
            keyframes: {
                glitch: {
                    '2%, 64%': { transform: 'translate(2px,0) skew(0deg)' },
                    '4%, 60%': { transform: 'translate(-2px,0) skew(0deg)' },
                    '62%': { transform: 'translate(0,0) skew(5deg)' },
                }
            }
        },
    },
    plugins: [],
}

import colors from "tailwindcss/colors";
import plugin from "tailwindcss/plugin";

/**
 * Theme: the Nidhi (NGU storefront) palette, i.e. warm cream surfaces, saffron
 * primary, Inter. Light by default, with `.dark` on <html> for dark mode.
 *
 * Every colour class goes through a CSS variable, so the whole app re-themes
 * without changing markup. The scales are SEMANTIC, by step:
 *   slate-950  page / input surface      slate-900  card surface
 *   slate-800  border, subtle fill        slate-700  strong border, hover fill
 *   slate-500/400  muted / secondary text  slate-200/100  body / heading text
 *   indigo (alias `brand`)  the saffron primary: 600 solid button, 500 hover/focus,
 *     300-400 link text, 950 tinted fill
 *   rose / emerald / amber / ...  status colours: 950 tinted fill, 800 border,
 *     300-400 text, 600 solid
 * In light mode the scales are therefore "inverted": bg-slate-950 is cream and
 * text-slate-100 is near-black. Keep to the roles above and both modes work.
 */

/** "r g b" channels of a hex colour, for rgb(var(--x) / <alpha-value>). */
const rgb = (hex) => {
  const n = parseInt(hex.slice(1), 16);
  return `${(n >> 16) & 255} ${(n >> 8) & 255} ${n & 255}`;
};

const STEPS = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950];

// Warm neutrals. Light values come from the NGU storefront tokens (--background,
// --foreground, --muted, --border); dark values from its `.dark` block.
const SLATE = {
  light: {
    50: "25 45% 9%", 100: "25 40% 14%", 200: "25 30% 22%", 300: "25 22% 32%", 400: "25 14% 42%",
    500: "28 10% 52%", 600: "30 10% 64%", 700: "33 18% 82%", 800: "35 22% 89%", 900: "0 0% 100%",
    950: "36 33% 97%",
  },
  dark: {
    50: "35 30% 98%", 100: "35 25% 94%", 200: "35 18% 85%", 300: "35 12% 74%", 400: "35 10% 62%",
    500: "30 8% 48%", 600: "28 10% 36%", 700: "25 14% 26%", 800: "25 18% 19%", 900: "25 24% 11%",
    950: "25 30% 7%",
  },
};

// Saffron primary (NGU --primary 18 75% 55%).
const BRAND = {
  light: {
    50: "18 90% 97%", 100: "18 85% 93%", 200: "18 70% 32%", 300: "18 72% 38%", 400: "18 75% 44%",
    500: "18 72% 47%", 600: "18 75% 52%", 700: "18 75% 70%", 800: "18 70% 84%", 900: "18 80% 91%",
    950: "18 90% 96%",
  },
  dark: {
    50: "18 100% 97%", 100: "18 95% 92%", 200: "18 90% 82%", 300: "18 85% 72%", 400: "18 80% 64%",
    500: "18 75% 58%", 600: "18 75% 52%", 700: "18 70% 40%", 800: "18 65% 30%", 900: "18 60% 20%",
    950: "18 55% 12%",
  },
};

// Status colours: dark mode uses Tailwind's scale as-is; light mode maps each
// step to the one that plays the same role on a light surface.
const LIGHT_STEP = { 50: 950, 100: 900, 200: 800, 300: 700, 400: 700, 500: 700, 600: 600, 700: 300, 800: 200, 900: 100, 950: 50 };
const ACCENTS = ["rose", "red", "emerald", "green", "amber", "yellow", "sky", "blue", "purple", "violet", "teal", "orange"];

const themeVars = (mode) => {
  const vars = {};
  for (const s of STEPS) {
    vars[`--slate-${s}`] = SLATE[mode][s];
    vars[`--brand-${s}`] = BRAND[mode][s];
    for (const name of ACCENTS) {
      vars[`--${name}-${s}`] = rgb(colors[name][mode === "light" ? LIGHT_STEP[s] : s]);
    }
  }
  return vars;
};

const scale = (prefix, fn) => Object.fromEntries(STEPS.map((s) => [s, `${fn}(var(--${prefix}-${s}) / <alpha-value>)`]));

const brand = scale("brand", "hsl");

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
      },
      colors: {
        slate: scale("slate", "hsl"),
        indigo: brand,
        brand,
        ...Object.fromEntries(ACCENTS.map((name) => [name, scale(name, "rgb")])),
      },
      // Soft, warm shadows (NGU --shadow-card / --shadow-lift) instead of Tailwind's heavy greys.
      boxShadow: {
        sm: "0 1px 2px hsl(25 40% 15% / 0.05)",
        md: "0 2px 8px hsl(25 40% 15% / 0.06)",
        lg: "0 4px 16px hsl(25 40% 15% / 0.07)",
        xl: "0 4px 20px hsl(25 40% 15% / 0.06)",
        "2xl": "0 16px 40px -12px hsl(25 40% 15% / 0.22)",
      },
    },
  },
  plugins: [
    plugin(({ addBase }) => {
      addBase({ ":root": { ...themeVars("light"), colorScheme: "light" } });
      addBase({ ".dark": { ...themeVars("dark"), colorScheme: "dark" } });
    }),
  ],
};

import { createTheme } from '@mui/material/styles'

// Primary accent — neon orange (plan default). Change this one value to
// re-skin the dashboard.
export const NEON_ORANGE = '#FF6A00'

// Verdict → display color for decision chips, mapped to readable hexes on the
// dark background (the API's color field is green/yellow/red).
export const VERDICT_HEX: Record<string, string> = {
  green: '#39D98A',
  yellow: '#F5B14C',
  red: '#FF5470',
}

// Letter-grade → color, gradient from strong-hold green to sell red. A/B sit in
// the green family (B lighter), C is amber (on-watch), D is the sell red.
export const GRADE_HEX: Record<string, string> = {
  A: '#39D98A',
  B: '#A3D977',
  C: '#F5B14C',
  D: '#FF5470',
}

// Professional dark theme with the neon-orange primary. Near-black surfaces
// keep the accent and the green/red P&L the loudest things on screen.
export const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: { main: NEON_ORANGE },
    background: { default: '#0E0E10', paper: '#17171A' },
    success: { main: '#39D98A' },
    error: { main: '#FF5470' },
    divider: 'rgba(255,255,255,0.08)',
  },
  shape: { borderRadius: 10 },
  typography: {
    fontFamily: `'Inter', 'Roboto', 'Helvetica', 'Arial', sans-serif`,
    h6: { fontWeight: 700, letterSpacing: 0.2 },
  },
  components: {
    MuiCard: {
      styleOverrides: {
        root: { border: '1px solid rgba(255,255,255,0.06)' },
      },
    },
  },
})

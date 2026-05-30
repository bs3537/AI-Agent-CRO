import Box from '@mui/material/Box'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'

export default function BrandLogo() {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.2}
      sx={{ minWidth: { xs: 190, sm: 250 }, flexShrink: 0 }}
    >
      <Box
        aria-hidden
        sx={{
          width: 42,
          height: 42,
          display: 'grid',
          placeItems: 'center',
          borderRadius: '12px',
          border: '1px solid rgba(255,255,255,0.12)',
          background:
            'linear-gradient(145deg, rgba(255,106,0,0.22), rgba(24,24,27,0.92) 48%, rgba(47,128,255,0.22))',
          boxShadow: '0 0 22px rgba(255,106,0,0.18), inset 0 0 18px rgba(255,255,255,0.05)',
        }}
      >
        <svg width="30" height="30" viewBox="0 0 30 30" role="img" aria-label="Aegis CRO mark">
          <path
            d="M15 3.4 24.2 7v7.2c0 6.1-3.6 10.4-9.2 12.4-5.6-2-9.2-6.3-9.2-12.4V7L15 3.4Z"
            fill="rgba(14,14,16,0.78)"
            stroke="#FF8A2A"
            strokeWidth="1.4"
            strokeLinejoin="round"
          />
          <path
            d="M8.7 18.8h3.1l3.2-8.1 3.2 8.1h3.1"
            fill="none"
            stroke="#2F80FF"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d="M12.6 15.7h4.8"
            fill="none"
            stroke="#39D98A"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
        </svg>
      </Box>

      <Box sx={{ minWidth: 0 }}>
        <Typography
          component="div"
          sx={{
            color: 'rgba(255,255,255,0.96)',
            fontWeight: 850,
            fontSize: { xs: 17, sm: 20 },
            lineHeight: 1,
            letterSpacing: 0,
            whiteSpace: 'nowrap',
          }}
        >
          Aegis CRO
        </Typography>
        <Typography
          component="div"
          sx={{
            color: 'primary.main',
            fontSize: { xs: 10.5, sm: 11.5 },
            fontWeight: 700,
            lineHeight: 1.2,
            letterSpacing: 0,
            textTransform: 'uppercase',
            whiteSpace: 'nowrap',
          }}
        >
          AI Chief Risk Officer
        </Typography>
      </Box>
    </Stack>
  )
}

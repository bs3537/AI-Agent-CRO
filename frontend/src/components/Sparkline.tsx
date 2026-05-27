import Box from '@mui/material/Box'

// Dependency-free SVG sparkline: a single price polyline (no axes, markers, or
// indicators — just the 1-year daily EOD close path). Colored green/red by the
// net change over the window. Renders a faint placeholder when there's no data.
export default function Sparkline({
  points,
  width = 132,
  height = 36,
}: {
  points: number[] | null
  width?: number
  height?: number
}) {
  if (!points || points.length < 2) {
    return (
      <Box
        sx={{
          width,
          height,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: 10,
          opacity: 0.35,
          border: '1px dashed rgba(255,255,255,0.12)',
          borderRadius: 1,
        }}
      >
        no price data
      </Box>
    )
  }

  const min = Math.min(...points)
  const max = Math.max(...points)
  const span = max - min || 1
  const pad = 2
  const w = width - pad * 2
  const h = height - pad * 2
  const stepX = w / (points.length - 1)

  // Map each close to an (x, y); y is inverted so higher prices sit higher.
  const coords = points.map((p, i) => {
    const x = pad + i * stepX
    const y = pad + h - ((p - min) / span) * h
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })

  const up = points[points.length - 1] >= points[0]
  const stroke = up ? '#39D98A' : '#FF5470'

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="1Y price sparkline">
      <polyline
        points={coords.join(' ')}
        fill="none"
        stroke={stroke}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  )
}

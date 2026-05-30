import Box from '@mui/material/Box'
import type { SparklineData } from '../types'

// Dependency-free SVG sparkline: daily close plus a 20-day EMA overlay. Price
// is bright blue; EMA20 is red so the lines stay readable on dark cards.
export default function Sparkline({
  spark,
  width = 132,
  height = 36,
}: {
  spark: SparklineData | null
  width?: number
  height?: number
}) {
  const points = spark?.closes ?? null
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

  const emaPoints = spark?.ema20 ?? []
  const all = [...points, ...emaPoints.filter((p): p is number => typeof p === 'number')]
  const min = Math.min(...all)
  const max = Math.max(...all)
  const span = max - min || 1
  const pad = 2
  const w = width - pad * 2
  const h = height - pad * 2

  const mapPoint = (p: number, i: number, n: number) => {
    const step = w / Math.max(n - 1, 1)
    const x = pad + i * step
    const y = pad + h - ((p - min) / span) * h
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }

  // Map each close to an (x, y); y is inverted so higher prices sit higher.
  const coords = points.map((p, i) => mapPoint(p, i, points.length))
  const emaCoords = emaPoints
    .map((p, i) => (typeof p === 'number' ? mapPoint(p, i, emaPoints.length) : null))
    .filter((p): p is string => Boolean(p))

  const priceStroke = '#2F80FF'
  const emaStroke = '#FF4D5E'

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="1Y price and EMA20 sparkline">
      <polyline
        points={coords.join(' ')}
        fill="none"
        stroke={priceStroke}
        strokeWidth={1.8}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {emaCoords.length > 1 && (
        <polyline
          points={emaCoords.join(' ')}
          fill="none"
          stroke={emaStroke}
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
          opacity={0.95}
        />
      )}
    </svg>
  )
}

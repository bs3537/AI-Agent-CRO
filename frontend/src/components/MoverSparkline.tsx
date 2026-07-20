import Box from '@mui/material/Box'

export default function MoverSparkline({
  closes,
  positive,
  width = 104,
  height = 32,
}: {
  closes: Array<number | null>
  positive: boolean
  width?: number
  height?: number
}) {
  const values = closes
    .map((value, index) => ({ value, index }))
    .filter((point): point is { value: number; index: number } => point.value !== null)
  if (values.length < 2) {
    return <Box sx={{ width, height, bgcolor: 'rgba(255,255,255,0.025)' }} />
  }
  const min = Math.min(...values.map((point) => point.value))
  const max = Math.max(...values.map((point) => point.value))
  const range = max - min || 1
  const xStep = (width - 4) / Math.max(closes.length - 1, 1)
  const points = values
    .map(
      (point) =>
        `${2 + point.index * xStep},${height - 3 - ((point.value - min) / range) * (height - 6)}`,
    )
    .join(' ')
  const color = positive ? '#39D98A' : '#FF5470'

  return (
    <Box sx={{ width, height, display: 'flex', alignItems: 'center' }}>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={positive ? 'Rising price trend' : 'Falling price trend'}
      >
        <line
          x1="2"
          x2={width - 2}
          y1={height - 3}
          y2={height - 3}
          stroke="rgba(255,255,255,0.08)"
        />
        <polyline
          points={points}
          fill="none"
          stroke={color}
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </Box>
  )
}

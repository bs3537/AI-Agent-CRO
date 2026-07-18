// Shared dashboard display helpers.

export function sourceLabel(source: string | undefined) {
  const labels: Record<string, string> = {
    scheduler: 'auto scheduler',
    manual_single: 'manual tile',
    manual_all: 'manual all',
    scheduler_morning_full_codex: 'morning full-book Codex',
    scheduler_new_position_draft: 'new-position AI draft',
    manual_new_position_draft: 'manual new-position AI draft',
    manual_preliminary_thesis: 'manual preliminary research',
    manual_preliminary_thesis_cli: 'CLI preliminary research',
    hermes_manual_preliminary_thesis: 'Hermes preliminary research',
    hermes_preliminary_thesis_one: 'Hermes preliminary research',
    hermes_preliminary_thesis_backfill: 'Hermes preliminary backfill',
    preliminary_thesis_followup: 'preliminary thesis follow-up',
    unknown: 'legacy run',
  }
  return labels[source ?? 'unknown'] ?? source
}

export function compactUsd(value: number): string {
  const sign = value < 0 ? '-' : ''
  const absolute = Math.abs(value)
  if (absolute >= 1e12) return `${sign}$${(absolute / 1e12).toFixed(2)}T`
  if (absolute >= 1e9) return `${sign}$${(absolute / 1e9).toFixed(2)}B`
  if (absolute >= 1e6) return `${sign}$${(absolute / 1e6).toFixed(2)}M`
  if (absolute >= 1e3) return `${sign}$${(absolute / 1e3).toFixed(0)}k`
  return `${sign}$${absolute.toFixed(0)}`
}

export function signedPercent(fraction: number, digits = 1): string {
  const value = fraction * 100
  return `${value >= 0 ? '+' : '-'}${Math.abs(value).toFixed(digits)}%`
}

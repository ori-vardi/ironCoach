export const COLORS = {
  swim: '#65bcff', bike: '#c3e88d', run: '#ff966c',
  strength: '#c099ff', other: '#7a88b8'
}

export const PLOTLY_LAYOUT = {
  paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
  font: { color: '#c8d3f5', size: 11 },
  margin: { l: 52, r: 24, t: 32, b: 48 },
  xaxis: { gridcolor: '#313450', linecolor: '#313450' },
  yaxis: { gridcolor: '#313450', linecolor: '#313450' },
  legend: { bgcolor: 'transparent', font: { size: 11 } },
  autosize: true,
}

export const PLOTLY_CONFIG = {
  responsive: true,
  displayModeBar: 'hover',
  modeBarButtonsToRemove: ['select2d', 'lasso2d', 'toImage', 'sendDataToCloud', 'hoverClosestCartesian', 'hoverCompareCartesian', 'toggleSpikelines'],
  displaylogo: false,
}

export const HR_ZONE_COLORS = {
  Z1: '#3478B0', Z2: '#2B8070', Z3: '#7C9B2E', Z4: '#B07028', Z5: '#862248'
}

export const HR_ZONE_LABELS = {
  Z1: 'Z1 (<129)', Z2: 'Z2 (130-142)', Z3: 'Z3 (143-155)', Z4: 'Z4 (156-168)', Z5: 'Z5 (169+)'
}

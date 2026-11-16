/**
 * MineSafe-AI Bridge Health Monitoring Dashboard - Client Logic
 * Handles real-time WebSocket telemetry, Canvas multi-trace strip charts,
 * interactive bridge SVG schematic, calibration countdown, and replay controls.
 *
 * STRICT RULE: REAL DATA ONLY. Zero mock or randomized values.
 */

// Global State
let ws = null;
let latestSnapshot = null;
let isReplaying = false;
let isRecording = false;
let isCalibrating = false;
let configCache = null;

// Time-series rolling historical buffers for charts (pure real data points only)
const MAX_HIST_POINTS = 300; // ~60 seconds at 5 Hz chart downsampling
const chartHistory = {
  timestamps: [],
  vib_s1: [],
  vib_s2: [],
  vib_s3: [],
  tilt_s1: [],
  tilt_s2: [],
  tilt_diff: [],
  disp_mm: [],
  flex_pct: [],
  load_kg: [],
  vbat_v: [],
  vsol_v: [],
  loss_pct: []
};

// Canvas Chart Controllers
const charts = {};

// Color Palette
const COLORS = {
  safe: '#00e676',
  watch: '#ffd600',
  inspect: '#ff9100',
  unsafe: '#ff1744',
  nodata: '#64748b',
  cyan: '#00d2ff',
  purple: '#c084fc',
  blue: '#38bdf8',
  amber: '#f59e0b',
  grid: '#1e293b',
  text: '#94a3b8'
};

// -----------------------------------------------------------------------------
// Initialization
// -----------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  initCanvases();
  setupEventListeners();
  fetchSerialPorts();
  fetchRecordingsList();
  connectWebSocket();
});

// -----------------------------------------------------------------------------
// WebSocket Connection
// -----------------------------------------------------------------------------
function connectWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/live`;

  console.log(`[WebSocket] Connecting to ${wsUrl}...`);
  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log('[WebSocket] Connected.');
    document.getElementById('conn-source-val').textContent = 'REAL HARDWARE ONLY';
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      latestSnapshot = data;
      renderDashboard(data);
    } catch (e) {
      console.error('[WebSocket] Parse error:', e);
    }
  };

  ws.onclose = () => {
    console.warn('[WebSocket] Closed. Reconnecting in 1.5s...');
    setTimeout(connectWebSocket, 1500);
  };

  ws.onerror = (err) => {
    console.error('[WebSocket] Error:', err);
    ws.close();
  };
}

// -----------------------------------------------------------------------------
// Main Render Dispatcher
// -----------------------------------------------------------------------------
function renderDashboard(data) {
  const isDisconnected = (data.risk && data.risk.level === 'NO_DATA');
  isReplaying = !!data.is_replay;
  isRecording = !!data.is_recording;

  // 1. Top Status Banner (5-Second Judge View)
  renderBanner(data.risk, isReplaying, data.replay_filename);

  // 2. Hardware Connection Strip
  renderConnectionStrip(data, isDisconnected);

  // 3. Sensor Value Tiles
  renderTiles(data, isDisconnected);

  // 4. Interactive Bridge Map Nodes
  renderBridgeMap(data, isDisconnected);

  // 5. Append Real Data to Chart Buffers & Render
  if (!isDisconnected) {
    appendChartData(data);
  }
  renderAllCharts(data, isDisconnected);

  // 6. Calibration Status
  renderCalibration(data.calibration);

  // 7. Event Log Table
  renderEventLog(data.event_log);

  // 8. Recording / Replay Status Badges
  updateButtonStates(data);
}

// -----------------------------------------------------------------------------
// 1. Top Banner
// -----------------------------------------------------------------------------
function renderBanner(risk, replaying, replayFilename) {
  const banner = document.getElementById('status-banner');
  const pill = document.getElementById('status-pill');
  const reason = document.getElementById('banner-reason');
  const action = document.getElementById('banner-action');
  const replayBadge = document.getElementById('replay-badge');
  const diagShort = document.getElementById('banner-diagnosis-short');
  const tsElem = document.getElementById('banner-timestamp');

  if (!risk) return;

  const level = risk.level || 'NO_DATA';
  banner.className = `status-banner ${level}`;
  pill.textContent = level;

  if (replaying) {
    replayBadge.style.display = 'block';
    replayBadge.textContent = `REPLAY OF RECORDED REAL DATA (${replayFilename || 'session'})`;
  } else {
    replayBadge.style.display = 'none';
  }

  reason.textContent = risk.reason || 'NO DATA – device disconnected';
  action.innerHTML = risk.recommended_action ? `<strong>Recommended Action:</strong> ${risk.recommended_action}` : '';
  diagShort.textContent = risk.diagnosis || '';
  tsElem.textContent = risk.timestamp || new Date().toLocaleTimeString();
}

// -----------------------------------------------------------------------------
// 2. Connection Strip
// -----------------------------------------------------------------------------
function renderConnectionStrip(data, isDisconnected) {
  document.getElementById('conn-port-val').textContent = data.active_port || 'Disconnected';
  document.getElementById('conn-pps-val').textContent = isDisconnected ? '0.0' : (data.link.pps || '0.0');
  document.getElementById('conn-loss-val').textContent = isDisconnected ? '--' : `${data.link.packet_loss_pct}%`;
  document.getElementById('conn-lost-count').textContent = isDisconnected ? '0' : data.link.packets_lost;
  document.getElementById('conn-age-val').textContent = isDisconnected ? 'Device offline' : `${data.link.last_packet_age_s}s`;

  // 12 Sensor online chips
  const sensors = data.sensors_online || {};
  const chips = [
    { id: 'chip-s1', online: sensors.s1_online },
    { id: 'chip-s2', online: sensors.s2_online },
    { id: 'chip-s3', online: sensors.s3_online },
    { id: 'chip-d1', online: sensors.d1_online },
    { id: 'chip-f1', online: sensors.f1_online },
    { id: 'chip-f2', online: sensors.f2_online },
    { id: 'chip-l1', online: sensors.l1_online },
    { id: 'chip-v1', online: sensors.v1_online },
    { id: 'chip-p1', online: sensors.p1_online },
    { id: 'chip-p2', online: sensors.p2_online },
    { id: 'chip-r1', online: sensors.r1_online },
    { id: 'chip-g1', online: sensors.g1_online }
  ];

  chips.forEach(c => {
    const el = document.getElementById(c.id);
    if (!el) return;
    if (c.online && !isDisconnected) {
      el.className = 'chip online';
    } else {
      el.className = 'chip offline';
    }
  });
}

// -----------------------------------------------------------------------------
// 3. Sensor Tiles
// -----------------------------------------------------------------------------
function renderTiles(data, isDisconnected) {
  if (isDisconnected) {
    // Zero out / mark offline strictly. Never invent numbers.
    setText('val-s1-rms', '--');
    setText('delta-s1-rms', 'Sensor offline');
    setText('val-f0', '--');
    setText('delta-f0', 'Sensor offline');
    setText('val-tilt-s1', '--');
    setText('delta-tilt-s1', 'Sensor offline');
    setText('val-tilt-diff', '--');
    setText('val-tilt-s2', 'Ground: --');
    setText('val-disp-deflection', '--');
    setText('val-disp-residual', 'Residual: --');
    setText('val-flex-pct', '--');
    setText('val-crack-wire', 'Wire: --');
    setText('val-load-kg', '--');
    setText('val-s3-ratio', '--');
    setText('val-s3-rms', 'S3: --');
    setText('val-vbat', '--');
    setText('val-vsol', '--');
    setText('val-rssi', '--');
    setText('val-snr', 'SNR: --');
    setText('val-gps-fix', 'No Data');
    setText('val-gps-coords', 'Sats: 0');
    return;
  }

  const v = data.vibration || {};
  const t = data.tilt || {};
  const d = data.displacement || {};
  const c = data.crack || {};
  const s = data.spatial || {};
  const p = data.power || {};
  const l = data.link || {};
  const g = data.gps || {};

  // Tile 1: S1 Deck Vibration
  setText('val-s1-rms', v.s1_rms_g !== null ? v.s1_rms_g.toFixed(4) : '--');
  setBadge('badge-s1', v.s1_rms_g > 0.08 ? 'unsafe' : v.s1_rms_g > 0.04 ? 'watch' : 'safe', v.s1_rms_g > 0.08 ? 'HIGH' : 'NORMAL');

  // Tile 2: Modal f0
  setText('val-f0', v.f0_hz !== null ? v.f0_hz.toFixed(2) : '--');
  if (v.f0_drop_pct) {
    setText('delta-f0', `\u0394 drop: -${v.f0_drop_pct}%`);
    setBadge('badge-f0', v.f0_drop_pct >= 10 ? 'inspect' : 'watch', `-${v.f0_drop_pct}%`);
  } else {
    setText('delta-f0', `Modal f0 peak`);
    setBadge('badge-f0', 'safe', 'NOMINAL');
  }

  // Tile 3: Pitch Tilt S1
  setText('val-tilt-s1', t.s1_pitch_deg !== null ? t.s1_pitch_deg.toFixed(2) : '--');
  if (t.s1_delta_deg !== null) {
    setText('delta-tilt-s1', `\u0394 ${t.s1_delta_deg > 0 ? '+' : ''}${t.s1_delta_deg.toFixed(2)}\u00B0 baseline`);
    setBadge('badge-tilt', Math.abs(t.s1_delta_deg) >= 0.6 ? 'inspect' : Math.abs(t.s1_delta_deg) >= 0.3 ? 'watch' : 'safe', `${Math.abs(t.s1_delta_deg).toFixed(2)}\u00B0`);
  }

  // Tile 4: Differential Settle
  setText('val-tilt-diff', t.structure_settlement_deg !== null ? t.structure_settlement_deg.toFixed(2) : '--');
  setText('val-tilt-s2', `Ground S2: ${t.s2_pitch_deg !== null ? t.s2_pitch_deg.toFixed(2) : '--'}\u00B0`);

  // Tile 5: Deflection
  setText('val-disp-deflection', d.deflection_mm !== null ? `${d.deflection_mm > 0 ? '+' : ''}${d.deflection_mm.toFixed(2)}` : (d.current_mm !== null ? d.current_mm.toFixed(2) : '--'));
  setText('val-disp-residual', `Residual: ${d.residual_mm !== null ? d.residual_mm.toFixed(2) : '0.00'} mm`);
  setBadge('badge-d1', d.residual_mm >= 1.0 ? 'inspect' : d.residual_mm >= 0.5 ? 'watch' : 'safe', d.residual_mm > 0.5 ? 'RESIDUAL' : 'SAFE');

  // Tile 6: Flex & Crack Wire
  setText('val-flex-pct', c.flex_percent !== null ? c.flex_percent.toFixed(1) : '--');
  if (c.crack_open === 1) {
    setText('val-crack-wire', 'Wire: TORN (RED)');
    document.getElementById('val-crack-wire').style.color = COLORS.unsafe;
    setBadge('badge-f1', 'unsafe', 'TORN');
  } else {
    setText('val-crack-wire', 'Wire: INTACT (SAFE)');
    document.getElementById('val-crack-wire').style.color = COLORS.safe;
    setBadge('badge-f1', c.flex_delta_percent >= 40 ? 'inspect' : c.flex_delta_percent >= 20 ? 'watch' : 'safe', `${c.flex_percent || 0}%`);
  }

  // Tile 7: Load Cell
  setText('val-load-kg', data.load_kg !== null ? data.load_kg.toFixed(2) : '--');
  setBadge('badge-l1', data.load_kg > 0.25 ? 'watch' : 'safe', data.load_kg > 0.25 ? 'LOADED' : 'UNLOADED');

  // Tile 8: S3 Spatial
  setText('val-s3-ratio', s.s3_s1_ratio !== null ? s.s3_s1_ratio.toFixed(2) : '--');
  setText('val-s3-rms', `S3: ${v.s3_rms_g !== null ? v.s3_rms_g.toFixed(4) : '--'} g RMS`);

  // Tile 9 & 10: Power
  setText('val-vbat', p.vbat_v !== null ? p.vbat_v.toFixed(2) : '--');
  setBadge('badge-p1', p.vbat_v < 3.4 ? 'unsafe' : p.vbat_v < 3.6 ? 'watch' : 'safe', p.vbat_v < 3.4 ? 'CRITICAL' : 'OK');
  setText('val-vsol', p.vsol_v !== null ? p.vsol_v.toFixed(2) : '--');

  // Tile 11: LoRa RF
  setText('val-rssi', l.rssi_dbm !== null ? `${l.rssi_dbm.toFixed(0)}` : 'USB Direct');
  setText('val-snr', l.snr_db !== null ? `SNR: ${l.snr_db.toFixed(1)} dB` : 'USB Connection');

  // Tile 12: GPS
  if (g.fix === 1 && g.lat && g.lon) {
    setText('val-gps-fix', `${g.lat.toFixed(4)}, ${g.lon.toFixed(4)}`);
    setText('val-gps-coords', `Sats: ${g.sats} (Fix OK)`);
    setBadge('badge-g1', 'safe', 'FIX 3D');
  } else {
    setText('val-gps-fix', 'No Fix (Indoors)');
    setText('val-gps-coords', `Sats: ${g.sats || 0}`);
    setBadge('badge-g1', 'watch', 'NO FIX');
  }
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function setBadge(id, cls, text) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = `tile-badge ${cls}`;
  el.textContent = text;
}

// -----------------------------------------------------------------------------
// 4. Interactive Bridge Map
// -----------------------------------------------------------------------------
function renderBridgeMap(data, isDisconnected) {
  const nodeIds = ['map-node-s1', 'map-node-s2', 'map-node-s3', 'map-node-d1', 'map-node-f1', 'map-node-l1', 'map-node-v1'];

  if (isDisconnected) {
    nodeIds.forEach(id => {
      const g = document.getElementById(id);
      if (!g) return;
      const shape = g.querySelector('circle, polygon, rect');
      if (shape) shape.setAttribute('fill', COLORS.nodata);
    });
    return;
  }

  const riskLvl = data.risk ? data.risk.level : 'SAFE';
  const c = data.crack || {};
  const t = data.tilt || {};

  // S1 Node
  setNodeColor('map-node-s1', (data.vibration && data.vibration.s1_rms_g > 0.08) ? COLORS.unsafe : (Math.abs(t.s1_delta_deg || 0) > 0.3) ? COLORS.watch : COLORS.safe);

  // S2 Ground
  setNodeColor('map-node-s2', (data.vibration && data.vibration.s2_rms_g > 0.06) ? COLORS.inspect : COLORS.safe);

  // S3 Quarter
  setNodeColor('map-node-s3', (data.vibration && data.vibration.s3_rms_g > 0.08) ? COLORS.unsafe : COLORS.safe);

  // D1 Laser
  setNodeColor('map-node-d1', (data.displacement && data.displacement.residual_mm > 0.5) ? COLORS.inspect : COLORS.safe);

  // F1/F2 Joint
  setNodeColor('map-node-f1', (c.crack_open === 1) ? COLORS.unsafe : (c.flex_delta_percent > 20) ? COLORS.watch : COLORS.safe);

  // L1 Load
  setNodeColor('map-node-l1', (data.load_kg > 0.25) ? COLORS.cyan : COLORS.blue);

  // V1 Shock
  setNodeColor('map-node-v1', COLORS.purple);
}

function setNodeColor(id, color) {
  const g = document.getElementById(id);
  if (!g) return;
  const shape = g.querySelector('circle, polygon, rect');
  if (shape) shape.setAttribute('fill', color);
}

window.highlightTile = function(tileId) {
  const el = document.getElementById(tileId);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  el.style.borderColor = COLORS.cyan;
  el.style.boxShadow = `0 0 12px ${COLORS.cyan}`;
  setTimeout(() => {
    el.style.borderColor = '';
    el.style.boxShadow = '';
  }, 1800);
};

// -----------------------------------------------------------------------------
// 5. Canvas Strip Charts
// -----------------------------------------------------------------------------
function initCanvases() {
  const ids = ['canvas-vib', 'canvas-fft', 'canvas-tilt', 'canvas-disp', 'canvas-flex', 'canvas-load', 'canvas-power', 'canvas-link'];
  ids.forEach(id => {
    const canvas = document.getElementById(id);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    charts[id] = { canvas, ctx };
    resizeCanvas(canvas);
  });

  window.addEventListener('resize', () => {
    ids.forEach(id => {
      const c = charts[id];
      if (c && c.canvas) resizeCanvas(c.canvas);
    });
  });
}

function resizeCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
}

function appendChartData(data) {
  const v = data.vibration || {};
  const t = data.tilt || {};
  const d = data.displacement || {};
  const c = data.crack || {};
  const p = data.power || {};
  const l = data.link || {};

  const nowStr = new Date().toLocaleTimeString();

  chartHistory.timestamps.push(nowStr);
  chartHistory.vib_s1.push(v.s1_rms_g !== null ? v.s1_rms_g : 0);
  chartHistory.vib_s2.push(v.s2_rms_g !== null ? v.s2_rms_g : 0);
  chartHistory.vib_s3.push(v.s3_rms_g !== null ? v.s3_rms_g : 0);

  chartHistory.tilt_s1.push(t.s1_pitch_deg !== null ? t.s1_pitch_deg : 0);
  chartHistory.tilt_s2.push(t.s2_pitch_deg !== null ? t.s2_pitch_deg : 0);
  chartHistory.tilt_diff.push(t.structure_settlement_deg !== null ? t.structure_settlement_deg : 0);

  chartHistory.disp_mm.push(d.deflection_mm !== null ? d.deflection_mm : 0);
  chartHistory.flex_pct.push(c.flex_percent !== null ? c.flex_percent : 0);
  chartHistory.load_kg.push(data.load_kg !== null ? data.load_kg : 0);
  chartHistory.vbat_v.push(p.vbat_v !== null ? p.vbat_v : 0);
  chartHistory.vsol_v.push(p.vsol_v !== null ? p.vsol_v : 0);
  chartHistory.loss_pct.push(l.packet_loss_pct || 0);

  if (chartHistory.timestamps.length > MAX_HIST_POINTS) {
    for (let k in chartHistory) {
      chartHistory[k].shift();
    }
  }
}

function renderAllCharts(data, isDisconnected) {
  // 1. Vibration
  renderVibrationChart(charts['canvas-vib'], data, isDisconnected);

  // 2. FFT Spectrum
  renderFFTChart(charts['canvas-fft'], data, isDisconnected);

  // 3. Tilt
  renderSeriesChart(charts['canvas-tilt'], [
    { name: 'S1 Tilt', data: chartHistory.tilt_s1, color: COLORS.blue },
    { name: 'S2 Tilt', data: chartHistory.tilt_s2, color: COLORS.nodata },
    { name: 'Settle', data: chartHistory.tilt_diff, color: COLORS.amber }
  ], isDisconnected, '\u00B0');

  // 4. Displacement
  renderSeriesChart(charts['canvas-disp'], [
    { name: 'Deflection', data: chartHistory.disp_mm, color: COLORS.safe }
  ], isDisconnected, 'mm', [
    { val: 1.0, color: COLORS.inspect, label: '1.0mm' },
    { val: 2.0, color: COLORS.unsafe, label: '2.0mm' }
  ]);

  // 5. Flex
  renderSeriesChart(charts['canvas-flex'], [
    { name: 'Flex %', data: chartHistory.flex_pct, color: COLORS.watch }
  ], isDisconnected, '%', [
    { val: 40.0, color: COLORS.inspect, label: '40%' },
    { val: 60.0, color: COLORS.unsafe, label: '60%' }
  ]);

  // 6. Load
  renderSeriesChart(charts['canvas-load'], [
    { name: 'Load', data: chartHistory.load_kg, color: COLORS.blue }
  ], isDisconnected, 'kg');

  // 7. Power
  renderSeriesChart(charts['canvas-power'], [
    { name: 'Battery', data: chartHistory.vbat_v, color: COLORS.blue },
    { name: 'Solar', data: chartHistory.vsol_v, color: COLORS.amber }
  ], isDisconnected, 'V', [
    { val: 3.3, color: COLORS.unsafe, label: '3.3V' }
  ]);

  // 8. Link
  renderSeriesChart(charts['canvas-link'], [
    { name: 'Loss %', data: chartHistory.loss_pct, color: COLORS.safe }
  ], isDisconnected, '%', [
    { val: 15.0, color: COLORS.inspect, label: '15%' }
  ]);
}

function renderVibrationChart(obj, data, isDisconnected) {
  if (!obj) return;
  const { canvas, ctx } = obj;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.width;
  const h = canvas.height;

  ctx.clearRect(0, 0, w, h);
  drawGrid(ctx, w, h);

  if (isDisconnected || !data.waveform_latest || data.waveform_latest.s1.length === 0) {
    drawEmptyState(ctx, w, h, 'NO VIBRATION TELEMETRY');
    return;
  }

  // Draw real raw batch waveform
  const s1 = data.waveform_latest.s1;
  const s2 = data.waveform_latest.s2;
  const s3 = data.waveform_latest.s3;
  const n = s1.length;

  const maxVal = Math.max(0.1, ...s1, ...(s2 || []), ...(s3 || []));

  // Trace S2 Ground (Blue)
  if (s2 && s2.length > 0) drawWaveTrace(ctx, s2, w, h, maxVal, '#0ea5e9', 1.0 * dpr);
  // Trace S3 Qtr (Purple)
  if (s3 && s3.length > 0) drawWaveTrace(ctx, s3, w, h, maxVal, '#c084fc', 1.0 * dpr);
  // Trace S1 Mid (Green)
  drawWaveTrace(ctx, s1, w, h, maxVal, '#00e676', 2.0 * dpr);

  // Overlay RMS thresholds if calibrated
  const th = data.rms_thresholds || {};
  if (th.yellow) drawHLine(ctx, w, h, th.yellow, maxVal, COLORS.watch, '3\u03C3 Watch');
  if (th.red) drawHLine(ctx, w, h, th.red, maxVal, COLORS.unsafe, '10\u03C3 Unsafe');
}

function renderFFTChart(obj, data, isDisconnected) {
  if (!obj) return;
  const { canvas, ctx } = obj;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.width;
  const h = canvas.height;

  ctx.clearRect(0, 0, w, h);
  drawGrid(ctx, w, h);

  const vib = data.vibration || {};
  const freqs = vib.spectrum_freqs || [];
  const amps = vib.spectrum_amps || [];

  if (isDisconnected || freqs.length === 0) {
    drawEmptyState(ctx, w, h, 'NO SPECTRAL DATA');
    return;
  }

  const maxAmp = Math.max(0.01, ...amps);
  const paddingBottom = 24 * dpr;
  const chartHeight = h - paddingBottom;

  // Draw spectrum bars / filled area
  ctx.beginPath();
  ctx.moveTo(0, chartHeight);
  for (let i = 0; i < freqs.length; i++) {
    const x = (i / (freqs.length - 1)) * w;
    const y = chartHeight - (amps[i] / maxAmp) * (chartHeight - 20 * dpr);
    ctx.lineTo(x, y);
  }
  ctx.lineTo(w, chartHeight);
  ctx.fillStyle = 'rgba(0, 210, 255, 0.2)';
  ctx.fill();

  ctx.beginPath();
  for (let i = 0; i < freqs.length; i++) {
    const x = (i / (freqs.length - 1)) * w;
    const y = chartHeight - (amps[i] / maxAmp) * (chartHeight - 20 * dpr);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.strokeStyle = COLORS.cyan;
  ctx.lineWidth = 2 * dpr;
  ctx.stroke();

  // Peak marker f0
  if (vib.f0_hz) {
    const idx = freqs.findIndex(f => f >= vib.f0_hz);
    if (idx >= 0) {
      const peakX = (idx / (freqs.length - 1)) * w;
      const peakY = chartHeight - (amps[idx] / maxAmp) * (chartHeight - 20 * dpr);

      ctx.beginPath();
      ctx.arc(peakX, peakY, 5 * dpr, 0, Math.PI * 2);
      ctx.fillStyle = COLORS.safe;
      ctx.fill();

      ctx.fillStyle = '#fff';
      ctx.font = `bold ${11 * dpr}px sans-serif`;
      ctx.fillText(`f0: ${vib.f0_hz.toFixed(1)} Hz`, peakX + 8 * dpr, peakY - 4 * dpr);
    }
  }

  // Baseline f0 marker
  const base = data.baseline || {};
  if (base.f0_hz) {
    const baseIdx = freqs.findIndex(f => f >= base.f0_hz);
    if (baseIdx >= 0) {
      const bx = (baseIdx / (freqs.length - 1)) * w;
      ctx.beginPath();
      ctx.setLineDash([4 * dpr, 4 * dpr]);
      ctx.moveTo(bx, 0);
      ctx.lineTo(bx, chartHeight);
      ctx.strokeStyle = COLORS.watch;
      ctx.lineWidth = 1.5 * dpr;
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = COLORS.watch;
      ctx.font = `${10 * dpr}px sans-serif`;
      ctx.fillText(`Base: ${base.f0_hz.toFixed(1)} Hz`, bx + 4 * dpr, 14 * dpr);
    }
  }

  // X Axis Freq labels
  ctx.fillStyle = COLORS.text;
  ctx.font = `${9 * dpr}px monospace`;
  ctx.fillText('0 Hz', 4 * dpr, h - 6 * dpr);
  ctx.fillText('50 Hz', w * 0.5 - 12 * dpr, h - 6 * dpr);
  ctx.fillText('100 Hz', w - 40 * dpr, h - 6 * dpr);
}

function renderSeriesChart(obj, seriesList, isDisconnected, unit, thresholdLines) {
  if (!obj) return;
  const { canvas, ctx } = obj;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.width;
  const h = canvas.height;

  ctx.clearRect(0, 0, w, h);
  drawGrid(ctx, w, h);

  if (isDisconnected || chartHistory.timestamps.length < 2) {
    drawEmptyState(ctx, w, h, 'AWAITING TELEMETRY');
    return;
  }

  // Compute scale
  let minVal = 0;
  let maxVal = 1;
  seriesList.forEach(s => {
    if (s.data.length > 0) {
      const sMin = Math.min(...s.data);
      const sMax = Math.max(...s.data);
      if (sMin < minVal) minVal = sMin;
      if (sMax > maxVal) maxVal = sMax;
    }
  });

  if (thresholdLines) {
    thresholdLines.forEach(th => {
      if (th.val > maxVal) maxVal = th.val * 1.1;
    });
  }

  const range = (maxVal - minVal) || 1;
  const padBottom = 20 * dpr;
  const chartH = h - padBottom - 10 * dpr;

  // Draw threshold lines
  if (thresholdLines) {
    thresholdLines.forEach(th => {
      const y = chartH - ((th.val - minVal) / range) * chartH + 10 * dpr;
      if (y >= 0 && y <= h) {
        ctx.beginPath();
        ctx.setLineDash([3 * dpr, 3 * dpr]);
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.strokeStyle = th.color;
        ctx.lineWidth = 1 * dpr;
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = th.color;
        ctx.font = `${9 * dpr}px monospace`;
        ctx.fillText(th.label, w - 50 * dpr, y - 3 * dpr);
      }
    });
  }

  // Draw series lines
  seriesList.forEach(s => {
    const pts = s.data;
    if (pts.length < 2) return;
    ctx.beginPath();
    for (let i = 0; i < pts.length; i++) {
      const x = (i / (MAX_HIST_POINTS - 1)) * w;
      const y = chartH - ((pts[i] - minVal) / range) * chartH + 10 * dpr;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 1.8 * dpr;
    ctx.stroke();
  });

  // Latest value label
  const lastVal = seriesList[0].data[seriesList[0].data.length - 1];
  ctx.fillStyle = '#fff';
  ctx.font = `bold ${11 * dpr}px monospace`;
  ctx.fillText(`${lastVal.toFixed(2)} ${unit || ''}`, 6 * dpr, 16 * dpr);
}

function drawWaveTrace(ctx, arr, w, h, maxVal, color, lineWidth) {
  const dpr = window.devicePixelRatio || 1;
  const n = arr.length;
  if (n < 2) return;
  const midY = h * 0.5;

  ctx.beginPath();
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * w;
    const y = midY - (arr[i] / maxVal) * (midY - 10 * dpr);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.stroke();
}

function drawGrid(ctx, w, h) {
  const dpr = window.devicePixelRatio || 1;
  ctx.strokeStyle = '#1e293b';
  ctx.lineWidth = 1 * dpr;
  // 4 horizontal lines
  for (let i = 1; i <= 3; i++) {
    const y = (h / 4) * i;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }
}

function drawHLine(ctx, w, h, val, maxVal, color, label) {
  const dpr = window.devicePixelRatio || 1;
  const midY = h * 0.5;
  const y = midY - (val / maxVal) * (midY - 10 * dpr);
  if (y < 0 || y > h) return;

  ctx.beginPath();
  ctx.setLineDash([4 * dpr, 4 * dpr]);
  ctx.moveTo(0, y);
  ctx.lineTo(w, y);
  ctx.strokeStyle = color;
  ctx.lineWidth = 1 * dpr;
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.fillStyle = color;
  ctx.font = `${9 * dpr}px monospace`;
  ctx.fillText(label, w - 70 * dpr, y - 3 * dpr);
}

function drawEmptyState(ctx, w, h, text) {
  const dpr = window.devicePixelRatio || 1;
  ctx.fillStyle = COLORS.nodata;
  ctx.font = `bold ${12 * dpr}px monospace`;
  ctx.textAlign = 'center';
  ctx.fillText(text, w * 0.5, h * 0.5);
  ctx.textAlign = 'left';
}

// -----------------------------------------------------------------------------
// 6. Calibration UI Handling
// -----------------------------------------------------------------------------
function renderCalibration(cal) {
  const btn = document.getElementById('btn-calibrate');
  const txt = document.getElementById('btn-cal-text');
  if (!cal || !btn || !txt) return;

  if (cal.active) {
    isCalibrating = true;
    btn.disabled = true;
    txt.textContent = `Calibrating: ${cal.remaining_sec}s (${cal.progress_pct}%)`;
    btn.style.background = '#f59e0b';
  } else {
    if (isCalibrating) {
      // Just finished
      isCalibrating = false;
      btn.disabled = false;
      txt.textContent = 'Calibrate (30s)';
      btn.style.background = '';
      alert('30-second empirical baseline calibration completed and saved to disk.');
    }
  }
}

// -----------------------------------------------------------------------------
// 7. Event Log Table
// -----------------------------------------------------------------------------
function renderEventLog(events) {
  const tbody = document.getElementById('log-table-body');
  if (!tbody || !events) return;

  if (events.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 14px;">No threshold crossings logged.</td></tr>`;
    return;
  }

  tbody.innerHTML = events.map(e => `
    <tr>
      <td>${e.timestamp}</td>
      <td class="log-level-${e.level}">${e.level}</td>
      <td>${e.source}</td>
      <td>${e.message}</td>
      <td style="color:#64748b;">${e.raw_value || ''}</td>
    </tr>
  `).join('');
}

// -----------------------------------------------------------------------------
// 8. User Interaction & Event Listeners
// -----------------------------------------------------------------------------
function setupEventListeners() {
  // Calibrate button
  document.getElementById('btn-calibrate').addEventListener('click', () => {
    if (confirm('Keep the bridge model completely still for 30 seconds to record empirical baseline statistics. Proceed?')) {
      fetch('/api/calibrate', { method: 'POST' });
    }
  });

  // Record button
  document.getElementById('btn-record').addEventListener('click', toggleRecording);

  // Mark event
  document.getElementById('btn-mark-event').addEventListener('click', submitEventNote);
  document.getElementById('input-event-note').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') submitEventNote();
  });

  // Replay toggle
  document.getElementById('btn-replay-toggle').addEventListener('click', toggleReplay);

  // Serial connect
  document.getElementById('btn-port-connect').addEventListener('click', () => {
    const sel = document.getElementById('select-serial-port');
    const port = sel.value;
    fetch('/api/connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ port })
    }).then(() => alert(`Target port set to ${port}`));
  });

  // Presentation Mode Toggle
  document.getElementById('btn-fullscreen').addEventListener('click', () => {
    document.body.classList.toggle('presentation-mode');
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  });

  // Export Report
  document.getElementById('btn-export-report').addEventListener('click', () => {
    window.open('/api/report/export', '_blank');
  });

  // Settings Modal
  const modal = document.getElementById('settings-modal');
  document.getElementById('btn-settings-open').addEventListener('click', openSettingsModal);
  document.getElementById('btn-settings-close').addEventListener('click', () => modal.style.display = 'none');
  document.getElementById('btn-settings-cancel').addEventListener('click', () => modal.style.display = 'none');
  document.getElementById('btn-settings-save').addEventListener('click', saveSettings);
}

function submitEventNote() {
  const input = document.getElementById('input-event-note');
  const note = input.value.trim();
  if (!note) return;

  fetch('/api/mark_event', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ note, level: 'NOTE' })
  }).then(() => {
    input.value = '';
  });
}

function toggleRecording() {
  const btn = document.getElementById('btn-record');
  const txt = document.getElementById('btn-rec-text');
  if (isRecording) {
    fetch('/api/recording/stop', { method: 'POST' }).then(() => {
      isRecording = false;
      txt.textContent = 'Start Recording';
      btn.className = '';
      fetchRecordingsList();
    });
  } else {
    fetch('/api/recording/start', { method: 'POST' }).then(() => {
      isRecording = true;
      txt.textContent = 'Stop Recording';
      btn.className = 'danger';
    });
  }
}

function toggleReplay() {
  const btn = document.getElementById('btn-replay-toggle');
  const sel = document.getElementById('select-replay-file');
  if (isReplaying) {
    fetch('/api/replay/stop', { method: 'POST' }).then(() => {
      btn.textContent = 'Replay';
      btn.className = '';
    });
  } else {
    const filename = sel.value;
    if (!filename) {
      alert('Please select a recorded real session file to replay.');
      return;
    }
    fetch('/api/replay/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename })
    }).then(r => {
      if (r.ok) {
        btn.textContent = 'Stop';
        btn.className = 'danger';
      } else {
        alert('Could not start replay.');
      }
    });
  }
}

function updateButtonStates(data) {
  const btnRec = document.getElementById('btn-record');
  const txtRec = document.getElementById('btn-rec-text');
  if (data.is_recording) {
    txtRec.textContent = `Recording (${data.recording_filename})`;
    btnRec.className = 'danger';
  } else {
    txtRec.textContent = 'Start Recording';
    btnRec.className = '';
  }

  const btnRep = document.getElementById('btn-replay-toggle');
  if (data.is_replay) {
    btnRep.textContent = 'Stop';
    btnRep.className = 'danger';
  } else {
    btnRep.textContent = 'Replay';
    btnRep.className = '';
  }
}

function fetchSerialPorts() {
  fetch('/api/ports')
    .then(r => r.json())
    .then(data => {
      const sel = document.getElementById('select-serial-port');
      sel.innerHTML = `<option value="AUTO">AUTO (Auto-detect ESP32)</option>`;
      (data.ports || []).forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.device;
        opt.textContent = `${p.device} (${p.description})`;
        sel.appendChild(opt);
      });
      if (data.configured_port) {
        sel.value = data.configured_port;
      }
    })
    .catch(() => {});
}

function fetchRecordingsList() {
  fetch('/api/recordings')
    .then(r => r.json())
    .then(data => {
      const sel = document.getElementById('select-replay-file');
      sel.innerHTML = '';
      if (!data.recordings || data.recordings.length === 0) {
        sel.innerHTML = '<option value="">No recorded sessions available</option>';
        return;
      }
      data.recordings.forEach(rec => {
        const opt = document.createElement('option');
        opt.value = rec.filename;
        opt.textContent = `${rec.filename} (${rec.size_kb} KB, ${rec.date})`;
        sel.appendChild(opt);
      });
    })
    .catch(() => {});
}

// -----------------------------------------------------------------------------
// Settings Modal
// -----------------------------------------------------------------------------
function openSettingsModal() {
  fetch('/api/config')
    .then(r => r.json())
    .then(cfg => {
      configCache = cfg;
      const th = cfg.thresholds || {};
      const vib = th.vibration_rms_sigma || {};
      const tilt = th.tilt_delta_deg || {};
      const disp = th.residual_disp_mm || {};

      document.getElementById('cfg-vib-yellow').value = vib.yellow || 3.0;
      document.getElementById('cfg-vib-orange').value = vib.orange || 6.0;
      document.getElementById('cfg-vib-red').value = vib.red || 10.0;

      document.getElementById('cfg-tilt-yellow').value = tilt.yellow || 0.3;
      document.getElementById('cfg-tilt-orange').value = tilt.orange || 0.6;
      document.getElementById('cfg-tilt-red').value = tilt.red || 1.0;

      document.getElementById('cfg-disp-yellow').value = disp.yellow || 0.5;
      document.getElementById('cfg-disp-orange').value = disp.orange || 1.0;
      document.getElementById('cfg-disp-red').value = disp.red || 2.0;

      document.getElementById('cfg-hx711-scale').value = cfg.calibration ? cfg.calibration.hx711_scale : 420.0;

      document.getElementById('settings-modal').style.display = 'flex';
    });
}

function saveSettings() {
  if (!configCache) return;
  const cfg = configCache;
  cfg.thresholds = cfg.thresholds || {};

  cfg.thresholds.vibration_rms_sigma = {
    yellow: parseFloat(document.getElementById('cfg-vib-yellow').value),
    orange: parseFloat(document.getElementById('cfg-vib-orange').value),
    red: parseFloat(document.getElementById('cfg-vib-red').value)
  };

  cfg.thresholds.tilt_delta_deg = {
    yellow: parseFloat(document.getElementById('cfg-tilt-yellow').value),
    orange: parseFloat(document.getElementById('cfg-tilt-orange').value),
    red: parseFloat(document.getElementById('cfg-tilt-red').value)
  };

  cfg.thresholds.residual_disp_mm = {
    yellow: parseFloat(document.getElementById('cfg-disp-yellow').value),
    orange: parseFloat(document.getElementById('cfg-disp-orange').value),
    red: parseFloat(document.getElementById('cfg-disp-red').value)
  };

  if (cfg.calibration) {
    cfg.calibration.hx711_scale = parseFloat(document.getElementById('cfg-hx711-scale').value);
  }

  fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg)
  }).then(() => {
    document.getElementById('settings-modal').style.display = 'none';
    alert('Configuration saved and active immediately.');
  });
}

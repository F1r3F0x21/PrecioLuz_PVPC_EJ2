const dateInput = document.querySelector('#selected-date');
const chart = document.querySelector('#price-chart');
const chartContext = chart.getContext('2d');
const chartEmpty = document.querySelector('#chart-empty');
const priceRows = document.querySelector('#price-rows');
const statusText = document.querySelector('#status-text');
const statusDot = document.querySelector('#status-dot');
const notice = document.querySelector('#notice');
const errorMessage = document.querySelector('#error-message');
const chartTooltip = document.querySelector('#chart-tooltip');
let report = null;
let chartLayout = null;

const localDate = value => {
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
};
const formatPrice = value => Number(value).toLocaleString('es-ES', { minimumFractionDigits: 5, maximumFractionDigits: 5 });
const formatDay = value => new Intl.DateTimeFormat('es-ES', { day: '2-digit', month: 'short' }).format(new Date(`${value}T12:00:00`));
const formatDateLong = value => new Intl.DateTimeFormat('es-ES', { dateStyle: 'full' }).format(new Date(`${value}T12:00:00`));

function drawChart(points, extremes) {
  const width = chart.clientWidth;
  const height = chart.clientHeight;
  const scale = window.devicePixelRatio || 1;
  chart.width = Math.round(width * scale);
  chart.height = Math.round(height * scale);
  chartContext.setTransform(scale, 0, 0, scale, 0, 0);
  chartContext.clearRect(0, 0, width, height);
  chartEmpty.hidden = points.length > 0;
  chartLayout = null;
  if (!points.length) return;

  const padding = { top: 12, right: 12, bottom: 27, left: 58 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const values = points.map(point => point.price);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const days = [...new Set(points.map(point => point.day))].sort();
  const dayIndex = new Map(days.map((day, index) => [day, index]));
  const cellWidth = plotWidth / 24;
  const rowHeight = plotHeight / days.length;
  const pointByCell = new Map();
  points.forEach(point => {
    const hour = Number.parseInt(point.hour.slice(0, 2), 10);
    pointByCell.set(`${point.day}|${hour}`, point);
  });
  chartLayout = { padding, cellWidth, rowHeight, days, pointByCell };

  chartContext.font = '10px "IBM Plex Mono", monospace';
  chartContext.textAlign = 'right';
  chartContext.textBaseline = 'middle';
  days.forEach((day, index) => {
    const y = padding.top + index * rowHeight + rowHeight / 2;
    chartContext.fillStyle = day === dateInput.value ? '#173b37' : '#627670';
    chartContext.fillText(formatDay(day), padding.left - 9, y);
  });

  const stops = [
    { position: 0, color: [24, 169, 135] },
    { position: 0.5, color: [239, 209, 91] },
    { position: 1, color: [223, 96, 79] },
  ];
  const colorAt = price => {
    const ratio = maximum === minimum ? 0.5 : (price - minimum) / (maximum - minimum);
    const segment = ratio <= 0.5 ? [stops[0], stops[1]] : [stops[1], stops[2]];
    const amount = ratio <= 0.5 ? ratio * 2 : (ratio - 0.5) * 2;
    const rgb = segment[0].color.map((channel, index) =>
      Math.round(channel + (segment[1].color[index] - channel) * amount)
    );
    return `rgb(${rgb.join(',')})`;
  };

  for (let hour = 0; hour < 24; hour += 6) {
    const x = padding.left + hour * cellWidth + cellWidth / 2;
    chartContext.textAlign = 'center';
    chartContext.textBaseline = 'alphabetic';
    chartContext.fillStyle = '#627670';
    chartContext.fillText(String(hour).padStart(2, '0'), x, height - 5);
  }

  points.forEach(point => {
    const hour = Number.parseInt(point.hour.slice(0, 2), 10);
    const row = dayIndex.get(point.day);
    if (!Number.isInteger(hour) || hour < 0 || hour > 23 || row === undefined) return;
    const x = padding.left + hour * cellWidth;
    const y = padding.top + row * rowHeight;
    chartContext.fillStyle = colorAt(point.price);
    chartContext.fillRect(x + 1, y + 1, Math.max(cellWidth - 2, 1), Math.max(rowHeight - 2, 1));
    const isExtreme = [extremes.minimum, extremes.maximum].some(extreme =>
      extreme?.day === point.day && extreme?.hour === point.hour
    );
    if (isExtreme) {
      chartContext.strokeStyle = '#173b37';
      chartContext.lineWidth = 2;
      chartContext.strokeRect(x + 1.5, y + 1.5, Math.max(cellWidth - 3, 1), Math.max(rowHeight - 3, 1));
    }
  });
}

chart.addEventListener('pointermove', event => {
  if (!chartLayout) return;
  const bounds = chart.getBoundingClientRect();
  const x = event.clientX - bounds.left - chartLayout.padding.left;
  const y = event.clientY - bounds.top - chartLayout.padding.top;
  const hour = Math.floor(x / chartLayout.cellWidth);
  const row = Math.floor(y / chartLayout.rowHeight);
  const day = chartLayout.days[row];
  const point = day && hour >= 0 && hour < 24
    ? chartLayout.pointByCell.get(`${day}|${hour}`)
    : null;
  if (!point) {
    chartTooltip.hidden = true;
    return;
  }
  chartTooltip.textContent = `${formatDateLong(point.day)} · ${point.hour} · ${formatPrice(point.price)} €/kWh`;
  chartTooltip.hidden = false;
  const left = Math.min(Math.max(x + chartLayout.padding.left + 10, 4), bounds.width - chartTooltip.offsetWidth - 4);
  chartTooltip.style.left = `${left}px`;
  chartTooltip.style.top = `${Math.max(y + chartLayout.padding.top - chartTooltip.offsetHeight - 8, 2)}px`;
});
chart.addEventListener('pointerleave', () => { chartTooltip.hidden = true; });

function showExtremes(extremes) {
  for (const [kind, item] of Object.entries(extremes)) {
    const label = kind === 'minimum' ? 'minimum' : 'maximum';
    document.querySelector(`#week-${label}`).textContent = item ? `${formatPrice(item.price)} €/kWh` : '--';
    document.querySelector(`#week-${label}-at`).textContent = item ? `${formatDateLong(item.day)} · ${item.hour}` : 'Sin datos';
  }
}

function showDay(prices, extremes) {
  const values = prices.map(item => item.price);
  const average = values.reduce((sum, value) => sum + value, 0) / values.length;
  document.querySelector('#summary-title').textContent = formatDateLong(dateInput.value);
  document.querySelector('#day-average').textContent = formatPrice(average);
  document.querySelector('#day-minimum').textContent = formatPrice(Math.min(...values));
  document.querySelector('#day-maximum').textContent = formatPrice(Math.max(...values));

  priceRows.replaceChildren();
  prices.forEach(item => {
    const isMinimum = extremes.minimum?.day === item.day && extremes.minimum?.hour === item.hour;
    const isMaximum = extremes.maximum?.day === item.day && extremes.maximum?.hour === item.hour;
    const row = document.createElement('tr');
    row.className = isMinimum ? 'hour-low' : isMaximum ? 'hour-high' : '';
    const hour = document.createElement('td');
    hour.textContent = item.hour;
    const price = document.createElement('td');
    price.textContent = `${formatPrice(item.price)} €/kWh`;
    const reference = document.createElement('td');
    reference.textContent = isMinimum ? 'Mínimo de 7 días' : isMaximum ? 'Máximo de 7 días' : '—';
    row.append(hour, price, reference);
    priceRows.append(row);
  });
}

async function loadPrices() {
  if (!dateInput.value) return;
  statusText.textContent = 'Cargando';
  statusDot.className = 'status-dot';
  errorMessage.hidden = true;
  notice.hidden = true;
  try {
    const response = await fetch(`/api/prices?date=${encodeURIComponent(dateInput.value)}`, { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'No se pudieron cargar los precios.');
    report = data;
    showDay(data.prices, data.extremes);
    showExtremes(data.extremes);
    drawChart(data.week, data.extremes);
    document.querySelector('#source-note').textContent = `Fuente: ${data.source}`;
    statusText.textContent = data.cache_hit ? 'Datos guardados localmente' : 'Precios actualizados';
    statusDot.className = 'status-dot ready';
    if (data.missing_days.length) {
      notice.textContent = `No se pudieron consultar ${data.missing_days.length} día(s) de la semana. Los extremos se calculan con los datos disponibles.`;
      notice.hidden = false;
    }
    if (!data.prices.length) throw new Error('No hay precios horarios para la fecha seleccionada.');
  } catch (error) {
    statusText.textContent = 'Sin datos';
    statusDot.className = 'status-dot failed';
    errorMessage.textContent = error.message;
    errorMessage.hidden = false;
    if (!report || report.date !== dateInput.value) {
      priceRows.innerHTML = '<tr><td colspan="3" class="table-empty">No se han podido cargar los precios.</td></tr>';
      chartContext.clearRect(0, 0, chart.width, chart.height);
      chartEmpty.hidden = false;
    }
  }
}

dateInput.max = localDate(new Date());
dateInput.value = dateInput.max;
dateInput.addEventListener('change', loadPrices);
document.querySelector('#today-button').addEventListener('click', () => {
  dateInput.value = localDate(new Date());
  loadPrices();
});
document.querySelector('#export-button').addEventListener('click', () => {
  if (report?.prices.length) window.location.href = `/api/export.pdf?date=${encodeURIComponent(dateInput.value)}`;
});
window.addEventListener('resize', () => {
  if (report) drawChart(report.week, report.extremes);
});
loadPrices();
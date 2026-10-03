// Draws the health and crisis trend from the canvas's data attribute (no inline scripts: CSP),
// in the app's dark palette.
(function () {
  const canvas = document.getElementById("trend");
  if (!canvas || !window.Chart) {
    return;
  }
  const points = JSON.parse(canvas.dataset.trend);
  const day = {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata",
  };
  const ink = "#8a94a6";
  const grid = "#1d2431";
  const line = (label, values, colour, fill) => ({
    label,
    data: values,
    borderColor: colour,
    backgroundColor: fill,
    fill: Boolean(fill),
    tension: 0.35,
    borderWidth: 2,
    pointRadius: 3,
    pointBackgroundColor: colour,
  });
  new window.Chart(canvas, {
    type: "line",
    data: {
      labels: points.map((p) => new Date(p.at).toLocaleString("en-IN", day)),
      datasets: [
        line("Health", points.map((p) => p.health), "#4f8cff", "rgba(79, 140, 255, 0.12)"),
        line("Crisis", points.map((p) => p.crisis), "#f87171", null),
      ],
    },
    options: {
      animation: false,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: ink, boxWidth: 12, usePointStyle: true } } },
      scales: {
        x: { ticks: { color: ink, maxRotation: 0, autoSkip: true }, grid: { color: grid } },
        y: { min: 0, max: 100, ticks: { color: ink }, grid: { color: grid } },
      },
    },
  });
})();

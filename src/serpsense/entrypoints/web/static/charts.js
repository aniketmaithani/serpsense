// Draws the health and crisis trend from the canvas's data attribute (no inline scripts: CSP).
(function () {
  const canvas = document.getElementById("trend");
  if (!canvas || !window.Chart) {
    return;
  }
  const points = JSON.parse(canvas.dataset.trend);
  const day = {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata",
  };
  new window.Chart(canvas, {
    type: "line",
    data: {
      labels: points.map((p) => new Date(p.at).toLocaleString("en-IN", day)),
      datasets: [
        { label: "Health", data: points.map((p) => p.health), borderColor: "#2357d9", tension: 0.2 },
        { label: "Crisis", data: points.map((p) => p.crisis), borderColor: "#b42318", tension: 0.2 },
      ],
    },
    options: { animation: false, scales: { y: { min: 0, max: 100 } } },
  });
})();

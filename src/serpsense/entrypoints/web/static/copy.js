// Copy a draft's text: a button with data-copy="<textarea id>" copies that textarea.
document.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-copy]");
  if (!button) return;
  const source = document.getElementById(button.dataset.copy);
  if (!source) return;
  try {
    await navigator.clipboard.writeText(source.value);
    button.textContent = "Copied";
  } catch {
    source.select();
    button.textContent = "Press Ctrl/Cmd+C";
  }
  setTimeout(() => { button.textContent = "Copy"; }, 2000);
});

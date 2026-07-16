const transcriptButtons = document.querySelectorAll("[data-toggle-transcript]");

for (const button of transcriptButtons) {
  button.addEventListener("click", () => {
    const panel = document.querySelector(button.dataset.toggleTranscript);
    if (!panel) return;
    const isOpen = panel.toggleAttribute("data-open");
    button.setAttribute("aria-expanded", String(isOpen));
  });
}

for (const marker of document.querySelectorAll("[data-marker]")) {
  marker.addEventListener("click", () => {
    for (const other of document.querySelectorAll("[data-marker]")) {
      other.removeAttribute("data-selected");
    }
    marker.setAttribute("data-selected", "true");
  });
}

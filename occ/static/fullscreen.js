"use strict";
(() => {
  const canvas = document.querySelector(".map-canvas-wrap");
  const button = document.querySelector("#toggle-map-fullscreen");
  if (!canvas || !button) return;

  const background = [...document.querySelectorAll(
    ".app-header, .sidebar, .mobile-dock, .map-topline, .map-footer, #profile-dialog"
  )].map((element) => ({element, inert: element.inert}));
  const toast = document.querySelector("#toast");
  const toastParent = toast?.parentElement;
  let expanded = false;
  let nativeFullscreen = false;

  function resizeMap() {
    // Leaflet listens to window resize. Wait until the new layout is applied.
    window.requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
  }

  function setExpanded(value) {
    expanded = value;
    canvas.classList.toggle("is-expanded", value);
    document.body.classList.toggle("map-expanded", value);
    button.setAttribute("aria-pressed", String(value));
    const label = value ? "Salir de pantalla completa" : "Ver mapa en pantalla completa";
    button.setAttribute("aria-label", label);
    button.title = label;
    button.querySelector("use").setAttribute("href", value ? "#i-collapse" : "#i-expand");
    for (const {element, inert} of background) element.inert = value || inert;
    // Native fullscreen only shows descendants of the fullscreen element.
    // Keep GPS/connection notices visible while the rest of the app is hidden.
    if (toast && toastParent) (value ? canvas : toastParent).append(toast);
    resizeMap();
  }

  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      if (expanded) {
        if (document.fullscreenElement === canvas) await document.exitFullscreen();
        setExpanded(false);
      } else {
        setExpanded(true);
        if (document.fullscreenEnabled && typeof canvas.requestFullscreen === "function" &&
            typeof document.exitFullscreen === "function") {
          try {
            await canvas.requestFullscreen();
          } catch (_) {
            // Keep the viewport-sized map when native fullscreen isn't allowed.
          }
        }
      }
    } catch (_) {
      // A denied native exit leaves the exit control available; Esc still works.
    } finally {
      button.disabled = false;
      button.focus({preventScroll: true});
    }
  });

  document.addEventListener("fullscreenchange", () => {
    const active = document.fullscreenElement === canvas;
    if (active || nativeFullscreen) setExpanded(active);
    nativeFullscreen = active;
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && expanded && document.fullscreenElement !== canvas) {
      event.preventDefault();
      setExpanded(false);
      button.focus({preventScroll: true});
    }
  });
})();

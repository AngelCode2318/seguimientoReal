"use strict";
const form = document.querySelector("#auth-form");
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = form.querySelector("button");
  const error = document.querySelector("#form-error");
  button.disabled = true;
  error.hidden = true;
  button.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(`/api/${form.dataset.mode}`, {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content},
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "No se pudo iniciar sesión.");
    window.location.assign("/");
  } catch (reason) {
    error.textContent = reason instanceof TypeError ? "Sin conexión. Revisa tu red e intenta de nuevo." : reason.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
});

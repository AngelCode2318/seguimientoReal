"use strict";
(() => {
  const $ = (selector) => document.querySelector(selector);
  const myId = Number($("#app-data").dataset.userId);
  const csrf = $('meta[name="csrf-token"]').content;
  const people = new Map();
  const markers = new Map();
  let clockOffset = 0;
  let wanted = false;
  let ready = false;
  let busy = false;
  let generation = 0;
  let watcher = null;
  let gpsPoll = null;
  let sendTimer = null;
  let pending = null;
  let lastSentAt = 0;
  let lastCaptured = 0;
  let wakeLock = null;
  let toastTimer;
  let initiallyFitted = false;

  function toast(message) {
    $("#toast").textContent = message;
    $("#toast").hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 6500);
  }
  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.classList.add("icon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
  }
  function text(tag, content, className = "") {
    const element = document.createElement(tag);
    element.textContent = content;
    if (className) element.className = className;
    return element;
  }
  function ageOf(person) { return Math.max(0, (Date.now() + clockOffset) / 1000 - person.updated_at); }
  function isStale(person) { return !person.connected || ageOf(person) >= 30; }
  function ageLabel(person) {
    if (!person.connected) return "Sin señal";
    if (ageOf(person) >= 30) return "Sin actualizar";
    return ageOf(person) < 5 ? "Ahora" : `Hace ${Math.floor(ageOf(person))} s`;
  }
  function carLabel(person) { return [person.car, person.year, person.color].filter(Boolean).join(" · "); }

  let map = null;
  if (typeof L !== "undefined") {
    map = L.map("map", {zoomControl: false}).setView([23.6, -102.5], 5);
    L.control.zoom({position: "bottomright"}).addTo(map);
    const tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
    let tileErrors = 0;
    tiles.on("tileerror", () => { if (++tileErrors >= 3) $("#map-unavailable").hidden = false; });
    tiles.on("tileload", () => { tileErrors = 0; $("#map-unavailable").hidden = true; });
  } else {
    $("#map-unavailable").hidden = false;
  }

  function markerIcon(person) {
    return L.divIcon({
      className: "car-map-icon", iconSize: [39, 47], iconAnchor: [20, 44], popupAnchor: [0, -40],
      html: `<span class="car-pin ${person.user_id === myId ? "my-pin" : ""} ${isStale(person) ? "stale-pin" : ""}"><svg class="icon" aria-hidden="true"><use href="#i-car"/></svg></span>`,
    });
  }
  function popup(person) {
    const element = text("div", "", "car-popup");
    element.append(text("strong", person.alias + (person.user_id === myId ? " (tú)" : "")),
      text("p", carLabel(person)), text("small", `${ageLabel(person)} · Precisión ±${Math.round(person.accuracy)} m`));
    return element;
  }
  function render() {
    for (const [id, person] of people) {
      if (ageOf(person) >= 60) people.delete(id);
    }
    for (const [id, marker] of markers) {
      if (!people.has(id)) { marker.remove(); markers.delete(id); }
    }
    const list = $("#participant-list");
    // Avoid replacing focused controls every second; update rows in place.
    const existing = new Map([...list.children].map((row) => [Number(row.dataset.id), row]));
    const sorted = [...people.values()].sort((a, b) => a.alias.localeCompare(b.alias, "es"));
    for (const person of sorted) {
      const id = person.user_id;
      let row = existing.get(id);
      if (!row) {
        row = document.createElement("button");
        row.type = "button";
        row.dataset.id = String(id);
        const car = text("span", "", "participant-car"); car.append(icon("car"));
        const info = text("span", "", "participant-info");
        info.append(text("span", "", "participant-name"), text("span", "", "participant-detail"));
        row.append(car, info, text("span", "", "participant-time"));
        row.addEventListener("click", () => focusPerson(id));
        list.append(row);
      }
      row.className = `participant-row ${isStale(person) ? "is-stale" : ""}`;
      row.querySelector(".participant-name").textContent = person.alias + (id === myId ? " (tú)" : "");
      row.querySelector(".participant-detail").textContent = carLabel(person);
      row.querySelector(".participant-time").textContent = ageLabel(person);
      row.setAttribute("aria-label", `Ver a ${person.alias}, ${carLabel(person)}, ${ageLabel(person)}`);
      existing.delete(id);
      if (map) {
        let marker = markers.get(id);
        if (!marker) {
          marker = L.marker([person.latitude, person.longitude], {icon: markerIcon(person), title: person.alias}).addTo(map);
          marker.bindPopup(popup(person));
          markers.set(id, marker);
        } else {
          marker.setLatLng([person.latitude, person.longitude]);
          const key = `${isStale(person)}:${person.user_id === myId}`;
          if (marker._clubState !== key) marker.setIcon(markerIcon(person));
          marker.setPopupContent(popup(person));
        }
        marker._clubState = `${isStale(person)}:${person.user_id === myId}`;
      }
    }
    existing.forEach((row) => row.remove());
    $("#participant-count").textContent = String(people.size);
    $("#empty-roster").hidden = people.size > 0;
    $("#map-counter").textContent = people.size ? `${people.size} ${people.size === 1 ? "AUTO EN EL MAPA" : "AUTOS EN EL MAPA"}` : "ESPERANDO A LA RODADA";
    if (people.size && !initiallyFitted) { fitGroup(); initiallyFitted = true; }
  }
  function focusPerson(id) {
    const person = people.get(id);
    if (person && map) {
      map.setView([person.latitude, person.longitude], Math.max(map.getZoom(), 15));
      markers.get(id)?.openPopup();
      if (window.innerWidth <= 700) $("#map").scrollIntoView({block: "center", behavior: "smooth"});
    }
  }
  function fitGroup() {
    if (!map) return;
    const points = [...people.values()].map((person) => [person.latitude, person.longitude]);
    if (points.length) map.fitBounds(L.latLngBounds(points), {padding: [65, 65], maxZoom: 15});
    else toast("Todavía no hay autos compartiendo ubicación.");
  }
  $("#fit-group").addEventListener("click", fitGroup);

  async function api(path, method = "GET", data) {
    const response = await fetch(path, {
      method, credentials: "same-origin", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
      ...(data !== undefined ? {body: JSON.stringify(data)} : {}),
    });
    const result = await response.json();
    if (response.status === 401) { window.location.assign("/"); throw new Error("Vuelve a entrar al mapa."); }
    if (!response.ok) throw new Error(result.error || "No se pudo completar la solicitud.");
    return result;
  }
  const socket = typeof io !== "undefined" ? io({auth: {csrf_token: csrf}, reconnectionDelayMax: 5000}) : null;
  function emitAck(event, data = {}) {
    return new Promise((resolve, reject) => {
      if (!socket?.connected) { reject(new Error("Sin conexión con el club. Espera a reconectar.")); return; }
      socket.timeout(8000).emit(event, data, (error, result) => {
        if (error) reject(new Error("El servidor no respondió. Revisa tu conexión."));
        else if (!result?.ok) reject(Object.assign(new Error(result?.error || "No se pudo completar el envío."), {code: result?.code}));
        else resolve(result);
      });
    });
  }
  function updateSharing() {
    document.querySelectorAll(".share-button").forEach((button) => {
      button.disabled = busy;
      button.classList.toggle("is-sharing", wanted);
      button.querySelector("span").textContent = wanted ? "Dejar de compartir" : "Compartir mi ubicación";
      button.querySelector("use").setAttribute("href", wanted ? "#i-stop" : "#i-pin");
      button.setAttribute("aria-pressed", String(wanted));
    });
    $("#share-title").textContent = wanted ? "Eres parte de la rodada." : "Tú eliges cuándo.";
    $("#share-description").textContent = wanted
      ? (ready ? "Compartiendo desde este dispositivo." : "Esperando conexión y GPS…")
      : "Tu ubicación está desactivada.";
  }
  async function keepAwake() {
    if (!wanted || document.visibilityState !== "visible") return;
    const status = $("#wake-status");
    status.hidden = false;
    try {
      if (!("wakeLock" in navigator)) throw new Error("unsupported");
      if (!wakeLock || wakeLock.released) wakeLock = await navigator.wakeLock.request("screen");
      if (!wanted) { await wakeLock.release(); wakeLock = null; return; }
      status.textContent = "Pantalla encendida durante la rodada.";
      wakeLock.addEventListener("release", () => {
        if (wanted) status.textContent = "Mantén la pantalla encendida para compartir.";
      });
    } catch (_) { status.textContent = "Mantén la pantalla encendida para compartir."; }
  }
  function clearGPS() {
    if (watcher !== null) navigator.geolocation.clearWatch(watcher);
    watcher = null;
    clearInterval(gpsPoll); gpsPoll = null;
    clearTimeout(sendTimer); sendTimer = null;
    pending = null;
  }
  function geoMessage(error) {
    if (error.code === 1) return "Permite el acceso a tu ubicación en los ajustes del navegador para compartir.";
    if (error.code === 2) return "No encontramos tu ubicación. Activa el GPS y espera una señal mejor.";
    return "El GPS está tardando. Esperaremos una ubicación nueva.";
  }
  function gpsError(error, epoch) {
    if (!wanted || epoch !== generation) return;
    if (error.code === 1) stopSharing(false);
    toast(geoMessage(error));
    $("#share-description").textContent = error.code === 1 ? "El permiso de ubicación está desactivado." : "Esperando una señal nueva del GPS…";
  }
  function onPosition(position, epoch) {
    if (!wanted || !ready || epoch !== generation || !socket?.connected) return;
    const captured = position.timestamp / 1000;
    const age = Date.now() / 1000 - captured;
    if (age > 10 || age < -5 || captured <= lastCaptured) return;
    pending = {latitude: position.coords.latitude, longitude: position.coords.longitude,
      accuracy: position.coords.accuracy, captured_at: captured};
    if (!sendTimer) sendNext(epoch);
  }
  function sendNext(epoch) {
    sendTimer = null;
    if (!wanted || !ready || epoch !== generation || !pending || !socket?.connected) return;
    const delay = 5100 - (Date.now() - lastSentAt);
    if (delay > 0) { sendTimer = setTimeout(() => sendNext(epoch), delay); return; }
    const sample = pending; pending = null;
    if (Date.now() / 1000 - sample.captured_at > 14) return;
    lastSentAt = Date.now(); lastCaptured = sample.captured_at;
    emitAck("position:update", sample).then(() => {
      if (wanted && generation === epoch) $("#share-description").textContent = "Tu auto está visible para el club.";
    }).catch((error) => {
      if (!wanted || epoch !== generation) return;
      if (error.code === "not_owner") { stopSharing(false, false); toast(error.message); }
      else if (error.code !== "rate_limited") $("#share-description").textContent = error.message;
    });
  }
  function startGPS(epoch) {
    clearGPS(); lastSentAt = 0; lastCaptured = 0;
    const options = {enableHighAccuracy: true, maximumAge: 0, timeout: 15000};
    const success = (position) => onPosition(position, epoch);
    const failure = (error) => gpsError(error, epoch);
    watcher = navigator.geolocation.watchPosition(success, failure, options);
    // Request fresh fixes even when a parked car doesn't trigger watchPosition.
    let polling = false;
    gpsPoll = setInterval(() => {
      if (polling || !wanted || !ready || !socket?.connected || document.visibilityState !== "visible") return;
      polling = true;
      navigator.geolocation.getCurrentPosition((position) => { polling = false; success(position); },
        (error) => { polling = false; failure(error); }, options);
    }, 10000);
  }
  async function acquireSharing() {
    const epoch = ++generation;
    ready = false; busy = true; updateSharing();
    try {
      await emitAck("share:start");
      if (!wanted || epoch !== generation) return;
      ready = true; startGPS(epoch); keepAwake();
    } catch (error) {
      if (epoch === generation) {
        ready = false;
        if (socket?.connected) { wanted = false; clearGPS(); }
        toast(error.message);
      }
    } finally { if (epoch === generation) { busy = false; updateSharing(); } }
  }
  async function stopSharing(notify = true, send = true) {
    const stopEpoch = ++generation;
    wanted = false; ready = false; busy = Boolean(send && socket?.connected);
    clearGPS();
    if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
    $("#wake-status").hidden = true;
    updateSharing();
    if (send && socket?.connected) {
      try { await emitAck("share:stop"); }
      catch (error) { toast(error.message); return; }
      finally { if (generation === stopEpoch) { busy = false; updateSharing(); } }
    }
    if (notify) toast(socket?.connected ? "Dejaste de compartir tu ubicación." : "Envío detenido. Tu marcador caducará en un máximo de 60 segundos si no hay conexión.");
  }
  document.querySelectorAll(".share-button").forEach((button) => button.addEventListener("click", () => {
    if (wanted) { stopSharing(); return; }
    if (!window.isSecureContext || !navigator.geolocation) { toast("La ubicación requiere HTTPS o localhost y un navegador compatible."); return; }
    if (!socket?.connected) { toast("Espera a que se restablezca la conexión con el club."); return; }
    wanted = true; acquireSharing();
  }));
  $("#locate-me").addEventListener("click", () => {
    if (people.has(myId)) { focusPerson(myId); return; }
    if (!navigator.geolocation || !window.isSecureContext) { toast("Abre la app mediante HTTPS para usar tu ubicación."); return; }
    navigator.geolocation.getCurrentPosition((position) => {
      if (map) map.setView([position.coords.latitude, position.coords.longitude], 15);
      toast("Te localizamos en tu pantalla. Esto no activa compartir.");
    }, (error) => toast(geoMessage(error)), {enableHighAccuracy: true, timeout: 15000, maximumAge: 0});
  });
  if (socket) {
    socket.on("participants:snapshot", (data) => {
      clockOffset = data.server_time * 1000 - Date.now();
      people.clear(); data.participants.forEach((person) => people.set(person.user_id, person)); render();
    });
    socket.on("participant:updated", (person) => { people.set(person.user_id, person); render(); });
    socket.on("participant:removed", ({user_id: id}) => { people.delete(id); render(); });
    socket.on("connect", () => {
      $("#connection-status").textContent = "Conectado";
      $("#connection-dot").classList.remove("offline");
      // Never replay queued samples: consent resumes with a brand-new GPS fix.
      if (wanted) acquireSharing();
      else emitAck("share:stop").catch(() => {});
    });
    socket.on("disconnect", () => {
      ready = false; busy = false; generation++; clearGPS(); updateSharing();
      $("#connection-status").textContent = "Sin conexión · reconectando";
      $("#connection-dot").classList.add("offline");
      // This browser cannot know whether the other cars are still updating.
      for (const person of people.values()) person.connected = false;
      render();
    });
    socket.on("connect_error", async () => {
      $("#connection-status").textContent = "No se pudo conectar · reintentando";
      $("#connection-dot").classList.add("offline");
      try { await api("/api/me"); } catch (_) { /* api redirects expired sessions */ }
    });
    socket.on("share:replaced", (data) => { stopSharing(false, false); toast(data.message); });
    socket.on("session:expired", () => { stopSharing(false, false); window.location.assign("/"); });
  } else {
    $("#connection-status").textContent = "No se pudo cargar la conexión";
    $("#connection-dot").classList.add("offline");
  }
  document.addEventListener("visibilitychange", () => { if (wanted && document.visibilityState === "visible") keepAwake(); });
  window.addEventListener("pagehide", () => {
    // The socket disconnect and the server TTL handle tabs that close abruptly.
    wanted = false; clearGPS();
    if (socket?.connected) socket.emit("share:stop");
    socket?.disconnect();
  });
  window.addEventListener("pageshow", (event) => { if (event.persisted) window.location.reload(); });
  const dialog = $("#profile-dialog");
  $("#profile-open").addEventListener("click", () => dialog.showModal());
  $("#profile-close").addEventListener("click", () => dialog.close());
  $("#profile-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.target.querySelector('button[type="submit"]');
    button.disabled = true; $("#profile-error").hidden = true;
    try {
      const data = await api("/api/me", "PATCH", Object.fromEntries(new FormData(event.target)));
      $("#header-alias").textContent = data.user.alias;
      $(".avatar").textContent = data.user.alias.slice(0, 1).toUpperCase();
      dialog.close(); toast("Tu perfil está actualizado.");
    } catch (error) { $("#profile-error").textContent = error.message; $("#profile-error").hidden = false; }
    finally { button.disabled = false; }
  });
  $("#logout").addEventListener("click", async () => {
    try { await api("/api/logout", "POST"); window.location.assign("/"); }
    catch (error) { toast(error.message); }
  });
  setInterval(render, 1000);
  updateSharing(); render();
})();

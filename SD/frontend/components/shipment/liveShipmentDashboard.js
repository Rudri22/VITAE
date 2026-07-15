(function () {
  function renderLiveShipmentDashboard(state, options = {}) {
    const title = options.title || "Real-Time Shipment Monitoring";
    if (!state || state.status === "loading") {
      return `<section class="live-shipments"><header><h2>${escapeHtmlLocal(title)}</h2></header><div class="shipment-loading">Loading live shipment data...</div></section>`;
    }
    if (state.status === "error") {
      return `<section class="live-shipments"><header><h2>${escapeHtmlLocal(title)}</h2></header><div class="shipment-error">${escapeHtmlLocal(state.error || "Unable to load live shipments.")}</div></section>`;
    }

    const shipments = state.shipments || [];
    if (!shipments.length) {
      return `<section class="live-shipments"><header><h2>${escapeHtmlLocal(title)}</h2></header><div class="shipment-empty">No active live shipments right now.</div></section>`;
    }

    return `
      <section class="live-shipments">
        <header>
          <h2>${escapeHtmlLocal(title)}</h2>
          <span>${shipments.length} active · updates every 5s · checked ${escapeHtmlLocal(formatCheckedAt(state.lastFetchedAt))}</span>
        </header>
        <div class="live-shipment-grid">
          ${shipments.map((shipment, index) => renderLiveShipmentCard(shipment, index)).join("")}
        </div>
      </section>
    `;
  }

  function renderLiveShipmentCard(shipment, index) {
    return `
      <article class="live-shipment-card ${toneForAlert(shipment.alertLevel)}">
        <header>
          <div>
            <strong>${escapeHtmlLocal(shipment.shipmentId || "Shipment")}</strong>
            <span>${escapeHtmlLocal(shipment.hospitalName || shipment.hospitalId || "Hospital")}</span>
          </div>
          <span class="alert-chip">${escapeHtmlLocal(shipment.alertLevel || "low")}</span>
        </header>
        <dl>
          <div><dt>Shipment location</dt><dd>${escapeHtmlLocal(shipment.currentLocation || "Unavailable")}</dd></div>
          <div><dt>Container temperature</dt><dd>${formatTemperature(shipment.containerTemperature)}</dd></div>
          <div><dt>Cooling unit battery</dt><dd>${formatBattery(shipment.coolingBatteryHealth)}</dd></div>
          <div><dt>Shipment status</dt><dd>${escapeHtmlLocal(statusLabelLocal(shipment.shipmentStatus))}</dd></div>
          <div><dt>Last updated</dt><dd>${escapeHtmlLocal(formatUpdated(shipment.lastUpdated))}</dd></div>
        </dl>
        <button class="map-icon-button" data-live-map-index="${index}" type="button" aria-label="Open route map for ${escapeHtmlLocal(shipment.shipmentId || "shipment")}">
          <span aria-hidden="true">Map</span>
          <strong>View route map</strong>
        </button>
      </article>
    `;
  }

  function formatTemperature(value) {
    return typeof value === "number" ? `${value.toFixed(1)}°C` : "N/A";
  }

  function formatBattery(value) {
    return typeof value === "number" ? `${Math.round(value)}%` : "N/A";
  }

  function formatUpdated(value) {
    if (!value) return "Recently";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function formatCheckedAt(value) {
    if (!value) return "now";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "now";
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function statusLabelLocal(value) {
    return String(value || "in_transit").replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
  }

  function toneForAlert(value) {
    const alert = String(value || "").toLowerCase();
    if (["critical", "high"].includes(alert)) return "critical";
    if (["warning", "medium"].includes(alert)) return "warning";
    return "normal";
  }

  function escapeHtmlLocal(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  window.liveShipmentDashboard = { render: renderLiveShipmentDashboard };
})();

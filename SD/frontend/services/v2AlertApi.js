(function () {
  function scopePath(lotTripId, alertId = null) {
    const lot = requiredId(lotTripId, "lot trip ID");
    const base = `/api/v2/alerts/${encodeURIComponent(lot)}`;
    return alertId == null
      ? base
      : `${base}/${encodeURIComponent(requiredId(alertId, "alert ID"))}`;
  }

  async function listAlerts(lotTripId) {
    return window.VitaeAuth.api(scopePath(lotTripId));
  }

  async function getAlert(lotTripId, alertId) {
    return window.VitaeAuth.api(scopePath(lotTripId, alertId));
  }

  async function acknowledge(lotTripId, alertId) {
    return command(lotTripId, alertId, "acknowledge", {});
  }

  async function recordAction(lotTripId, alertId, description) {
    return command(lotTripId, alertId, "actions", {
      description: requiredText(description, "Action description"),
    });
  }

  async function resolve(lotTripId, alertId, resolutionNote) {
    return command(lotTripId, alertId, "resolve", {
      resolutionNote: requiredText(resolutionNote, "Resolution note"),
    });
  }

  async function command(lotTripId, alertId, action, body) {
    return window.VitaeAuth.api(`${scopePath(lotTripId, alertId)}/${action}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  function requiredId(value, label) {
    const normalized = String(value || "").trim();
    if (!normalized) throw new Error(`A ${label} is required.`);
    return normalized;
  }

  function requiredText(value, label) {
    const normalized = String(value || "").trim();
    if (!normalized) throw new Error(`${label} is required.`);
    return normalized;
  }

  window.VitaeV2AlertApi = {
    listAlerts,
    getAlert,
    acknowledge,
    recordAction,
    resolve,
  };
})();

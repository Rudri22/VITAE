(function () {
  async function fetchLiveShipments() {
    const token = localStorage.getItem("vitaeAuthToken");
    const apiBase = window.location.protocol === "file:" ? "http://127.0.0.1:8000" : "";
    const response = await fetch(`${apiBase}/api/shipments/live`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      credentials: "same-origin",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Live shipments failed to load");
    return payload;
  }

  window.shipmentApi = { fetchLiveShipments };
})();

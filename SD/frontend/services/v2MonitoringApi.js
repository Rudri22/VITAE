(function () {
  const DEFAULT_POLL_INTERVAL_MS = 5000;

  async function fetchLive(lotTripId) {
    const normalizedId = String(lotTripId || "").trim();
    if (!normalizedId) throw new Error("A lot trip ID is required for monitoring.");
    return window.VitaeAuth.api(
      `/api/v2/monitor/live/${encodeURIComponent(normalizedId)}`,
    );
  }

  function startPolling(lotTripId, { onData, onError, intervalMs = DEFAULT_POLL_INTERVAL_MS }) {
    let stopped = false;
    let requestInFlight = false;

    const poll = async () => {
      if (stopped || requestInFlight) return;
      requestInFlight = true;
      try {
        const payload = await fetchLive(lotTripId);
        if (!stopped) onData(payload);
      } catch (error) {
        if (!stopped) onError(error);
      } finally {
        requestInFlight = false;
      }
    };

    const timer = window.setInterval(poll, intervalMs);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }

  window.VitaeV2MonitoringApi = { fetchLive, startPolling };
})();

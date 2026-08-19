(function () {
  async function fetchProductContexts() {
    const payload = await window.VitaeAuth.api(
      "/api/v2/catalog/product-contexts",
    );
    return Array.isArray(payload.productContexts) ? payload.productContexts : [];
  }

  async function fetchOrganizationSensors() {
    const payload = await window.VitaeAuth.api("/api/organization/sensors");
    return Array.isArray(payload.sensors) ? payload.sensors : [];
  }

  async function loadCreationOptions() {
    const [productContexts, sensors] = await Promise.all([
      fetchProductContexts(),
      fetchOrganizationSensors(),
    ]);
    return { productContexts, sensors };
  }

  window.VitaeV2ShipmentSetupApi = {
    fetchProductContexts,
    fetchOrganizationSensors,
    loadCreationOptions,
  };
})();

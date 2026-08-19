const assert = require("node:assert/strict");

global.window = {
  VitaeUI: {
    badge: (value) => String(value),
    empty: (value) => String(value),
    escape: (value) => String(value ?? ""),
    humanize: (value) => String(value ?? "").replaceAll("-", " ").replaceAll("_", " "),
    pageHeader: () => "",
    shell: ({ content }) => content,
  },
};
global.crypto = { randomUUID: () => "next-submission" };
global.FormData = class {
  constructor(form) { this.form = form; }
  entries() { return Object.entries(this.form.values); }
};

require("./roles/organization.js");

const context = {
  productId: "catalog-product-id",
  productName: "Catalog Product Name",
  presentation: "catalog-presentation",
  state: "catalog-state",
  productRuleVersion: "catalog-version",
};
const sensor = {
  sensorId: "sensor-from-api",
  status: "healthy",
  connectionStatus: "online",
  batteryLevel: 95,
};

function state(v2Enabled = false) {
  return {
    user: {},
    data: {
      organization: { name: "Test Organization" },
      facilities: [
        { facilityId: "origin", name: "Origin" },
        { facilityId: "destination", name: "Destination" },
      ],
      drivers: [{ driverId: "driver", name: "Driver", status: "available" }],
      shipments: [],
    },
    organization: {
      draft: {
        submissionId: "submission-test",
        productCategory: "Vaccines",
        v2Enabled,
        v2ContextIndex: v2Enabled ? "0" : undefined,
        v2DeviceId: v2Enabled ? sensor.sensorId : undefined,
        v2LotId: v2Enabled ? "lot-from-form" : undefined,
      },
      filters: {},
      saving: false,
    },
    v2ShipmentOptions: {
      status: "ready",
      productContexts: [context],
      sensors: [
        sensor,
        { sensorId: "sensor-offline", status: "offline", connectionStatus: "offline" },
        { sensorId: "sensor-assigned", status: "healthy", connectionStatus: "online", shipmentId: "existing-shipment" },
      ],
    },
    live: { shipments: [] },
  };
}

function form(values, checked) {
  const button = { disabled: false, textContent: "Send Request to Driver" };
  const feedback = { textContent: "" };
  return {
    dataset: { orgForm: "quick-request" },
    values,
    elements: { v2Enabled: { checked } },
    querySelector: (selector) => selector.includes("button") ? button : feedback,
  };
}

function baseValues() {
  return {
    submissionId: "submission-test",
    productCategory: "Vaccines",
    productName: "Legacy Product",
    quantity: "10",
    unit: "boxes",
    originFacilityId: "origin",
    destinationFacilityId: "destination",
    driverId: "driver",
    safeTemperatureMin: "2",
    safeTemperatureMax: "8",
    departureAt: "2026-08-20T08:00",
    expectedArrival: "2026-08-20T10:00",
  };
}

async function submit(testState, testForm) {
  let captured;
  window.VitaeAuth = {
    api: async (url, options) => {
      if (url === "/api/organization/shipments") captured = JSON.parse(options.body);
      return { created: true };
    },
  };
  const actions = {
    render() {},
    reload: async () => {},
    reloadV2Options: async () => {},
    refreshLive: async () => {},
    notify() {},
  };
  await window.VitaeOrganization.handleSubmit(
    { target: testForm, preventDefault() {} },
    testState,
    actions,
  );
  return captured;
}

async function main() {
  const disabledHtml = window.VitaeOrganization.render(state(false), "create");
  assert.match(disabledHtml, /name="v2Enabled"/);
  assert.doesNotMatch(disabledHtml, /name="v2LotId"/);

  const enabledHtml = window.VitaeOrganization.render(state(true), "create");
  assert.match(enabledHtml, /Catalog Product Name/);
  assert.match(enabledHtml, /catalog-version/);
  assert.match(enabledHtml, /sensor-from-api/);
  assert.doesNotMatch(enabledHtml, /sensor-offline/);
  assert.doesNotMatch(enabledHtml, /sensor-assigned/);

  const disabledPayload = await submit(state(false), form(baseValues(), false));
  assert.equal(disabledPayload.v2Monitoring, undefined);

  const enabledValues = {
    ...baseValues(),
    v2Enabled: "true",
    v2ContextIndex: "0",
    v2LotId: "lot-from-form",
    v2DeviceId: sensor.sensorId,
  };
  const enabledPayload = await submit(state(true), form(enabledValues, true));
  assert.equal(enabledPayload.productName, context.productName);
  assert.equal(enabledPayload.sensorId, sensor.sensorId);
  assert.deepEqual(enabledPayload.v2Monitoring, {
    enabled: true,
    productId: context.productId,
    presentation: context.presentation,
    state: context.state,
    lotId: "lot-from-form",
    deviceId: sensor.sensorId,
  });
  assert.equal(enabledPayload.productRuleVersion, undefined);
  assert.equal(enabledPayload.v2ContextIndex, undefined);
  console.log("V2 shipment form contract tests passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

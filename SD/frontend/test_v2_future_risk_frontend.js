const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

global.window = {
  VitaeUI: {
    badge: (value, tone) => `<b data-tone="${tone || ""}">${String(value)}</b>`,
    empty: (value) => String(value),
    escape: (value) => String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;"),
    humanize: (value) => String(value ?? "").replaceAll("_", " "),
    pageHeader: () => "",
    shell: ({ content }) => content,
  },
};
global.crypto = { randomUUID: () => "test-id" };

require("./roles/organization.js");
require("./services/v2MonitoringApi.js");

function state(futureRisk, status = "SAFE") {
  const shipment = {
    shipmentId: "shipment-v2",
    lotTripId: "lot-trip-v2",
    status: "in_transit",
  };
  const data = {
    tripIdentity: {
      lotTripId: shipment.lotTripId,
      productId: "gardasil-9",
      origin: "Origin",
      destination: "Destination",
      status: "ACTIVE",
    },
    liveState: { status, latestTemperature: 6.1, lastUpdated: "2026-08-21T11:32:00Z" },
    latestAlert: null,
  };
  if (futureRisk !== undefined) data.futureRisk = futureRisk;
  return {
    user: {},
    data: {
      organization: { name: "Organization" },
      shipments: [shipment],
      activeShipments: [],
      summary: {},
    },
    v2Monitoring: { status: "ready", shipmentId: shipment.shipmentId, data },
  };
}

function render(futureRisk, status) {
  return window.VitaeOrganization.render(state(futureRisk, status), "dashboard");
}

function predicted(overrides = {}) {
  return {
    state: "PREDICTED",
    adverseEventProbability: 0.08421,
    predictionHorizonMinutes: 30,
    cutoffAt: "2026-08-21T11:32:00Z",
    trainingSourceKind: "APPROVED_SIMULATOR",
    limitations: ["Backend-only diagnostic text"],
    riskPolicy: { mediumThreshold: 0.1, highThreshold: 0.2 },
    riskBand: "HIGH",
    ...overrides,
  };
}

function assertNoBands(html) {
  assert.doesNotMatch(html, />LOW</i);
  assert.doesNotMatch(html, />MEDIUM</i);
  assert.doesNotMatch(html, />HIGH</i);
  assert.doesNotMatch(html, /Predicted status/i);
}

async function main() {
  const predictedHtml = render(predicted(), "SAFE");
  assert.match(predictedHtml, /Current status[\s\S]*SAFE[\s\S]*Deterministic ProductRules/i);
  assert.match(predictedHtml, /30-min future risk[\s\S]*8\.4%/i);
  assert.match(predictedHtml, /next 30 minutes/i);
  assert.match(predictedHtml, /Data through/i);
  assert.match(predictedHtml, /Trained on simulated VITAE trips/i);
  assert.match(predictedHtml, /Real-device performance not yet validated/i);
  assert.match(predictedHtml, /No approved risk-band policy/i);
  assert.doesNotMatch(predictedHtml, /Backend-only diagnostic text/);
  assertNoBands(predictedHtml);

  const absentHtml = render(undefined);
  assert.match(absentHtml, /data-future-risk-state="NOT_CONFIGURED"/);
  assert.match(absentHtml, /Not configured/);
  assert.doesNotMatch(absentHtml, /0\.0%/);

  const notConfiguredHtml = render({ state: "NOT_CONFIGURED" });
  assert.match(notConfiguredHtml, /Not configured/);

  const reasons = {
    NO_ACCEPTED_TELEMETRY: "Waiting for telemetry",
    CURRENT_STATUS_NOT_ELIGIBLE: "Forecast not applicable for current status",
    TRIP_NOT_ACTIVE: "Forecast unavailable for inactive trip",
    HISTORY_NOT_COHERENT: "Forecast temporarily unavailable",
    CONCURRENT_UPDATE: "Updating telemetry - try again shortly",
    INFERENCE_UNAVAILABLE: "Forecast temporarily unavailable",
  };
  for (const [reasonCode, message] of Object.entries(reasons)) {
    const html = render({ state: "NOT_PREDICTED", reasonCode, detail: "unsafe raw detail" });
    assert.match(html, new RegExp(message.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    assert.doesNotMatch(html, /unsafe raw detail/);
    assert.doesNotMatch(html, /0\.0%/);
  }

  for (const malformed of [
    predicted({ adverseEventProbability: Number.NaN }),
    predicted({ adverseEventProbability: Number.POSITIVE_INFINITY }),
    predicted({ adverseEventProbability: -0.1 }),
    predicted({ adverseEventProbability: 1.1 }),
    predicted({ adverseEventProbability: "0.5" }),
    predicted({ predictionHorizonMinutes: 60 }),
    predicted({ cutoffAt: "not-a-date" }),
  ]) {
    const html = render(malformed, "CRITICAL");
    assert.match(html, /Forecast unavailable/);
    assert.match(html, /Current status[\s\S]*CRITICAL/i);
    assert.doesNotMatch(html, /NaN%|Infinity%|undefined%/);
  }

  const source = fs.readFileSync(path.join(__dirname, "roles", "organization.js"), "utf8");
  assert.doesNotMatch(source, /adverseEventProbability\s*[><]=?\s*0\./);
  assert.doesNotMatch(source, /fetch\([^)]*future|model.*artifact|joblib/i);

  const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
  assert.match(css, /--vitae-space-1:\s*8px/);
  assert.match(css, /\.org-monitor-signal-grid\s*\{[^}]*display:\s*grid/);
  assert.match(css, /@media \(max-width: 820px\)[\s\S]*\.org-monitor-signal-grid\s*\{\s*grid-template-columns:\s*1fr/);

  const api = fs.readFileSync(path.join(__dirname, "services", "v2MonitoringApi.js"), "utf8");
  assert.equal((api.match(/\/api\/v2\/monitor\/live\//g) || []).length, 1);

  const monitoringResponse = {
    liveState: { status: "SAFE" },
    futureRisk: predicted(),
  };
  const requests = [];
  window.VitaeAuth = {
    api: async (url) => {
      requests.push(url);
      return monitoringResponse;
    },
  };
  const fetched = await window.VitaeV2MonitoringApi.fetchLive("lot-trip-v2");
  assert.equal(fetched, monitoringResponse);
  assert.deepEqual(requests, ["/api/v2/monitor/live/lot-trip-v2"]);
  console.log("V2 future-risk frontend contract tests passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

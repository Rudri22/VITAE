const assert = require("node:assert/strict");

global.window = {
  VitaeUI: {
    badge: (value) => `<b>${String(value)}</b>`,
    empty: (value) => String(value),
    escape: (value) => String(value ?? ""),
    humanize: (value) => String(value ?? "").replaceAll("_", " "),
    pageHeader: () => "",
    shell: ({ nav, content }) => `${nav.map((item) => item[1]).join("|")}${content}`,
  },
};

require("./roles/admin.js");

function state() {
  return {
    user: { name: "Admin" },
    data: { localDemoControlsEnabled: true },
    localDemo: {
      status: "ready",
      demo: {
        lotTripId: "demo-lot-trip",
        currentStep: 1,
        totalSteps: 9,
        complete: false,
        lastResult: {
          stepNumber: 1,
          step: { id: "safe", label: "Healthy reading", kind: "TELEMETRY" },
          telemetryResponse: { telemetryAccepted: true },
          acceptedSampleCount: 1,
        },
        nextStep: { id: "monitor", label: "Permitted excursion begins", kind: "TELEMETRY", temperature: 9 },
        steps: [
          { id: "safe", label: "Healthy reading", kind: "TELEMETRY" },
          { id: "monitor", label: "Permitted excursion begins", kind: "TELEMETRY" },
        ],
      },
      monitoring: {
        liveState: { status: "SAFE", latestTemperature: 6, revision: 1 },
        openAlertCount: 0,
        tripIdentity: { status: "ACTIVE" },
        futureRisk30m: { state: "PREDICTED", adverseEventProbability: 0.0842 },
        operationalDecision: { recommendedAction: "CONTINUE", reason: "Current deterministic controls are safe." },
      },
    },
  };
}

async function main() {
  const html = window.VitaeAdmin.render(state(), "simulation");
  assert.match(html, /Demo Flow/);
  assert.match(html, /One shipment, real state transitions/i);
  assert.match(html, /Current condition[\s\S]*SAFE/i);
  assert.match(html, /Predicted 30-minute risk[\s\S]*8\.42% predicted adverse-event probability/i);
  assert.match(html, /Recommended action[\s\S]*CONTINUE/i);
  assert.match(html, /Why[\s\S]*Current deterministic controls are safe/i);
  assert.match(html, /1 accepted telemetry sample/);
  assert.match(html, /Authoritative revision[\s\S]*1/i);
  assert.match(html, /Cause and effect/i);
  assert.match(html, /Next state/);
  assert.match(html, /Permitted excursion begins/);
  assert.match(html, /cannot run against DynamoDB or production/i);

  const compared = state();
  compared.localDemo.demo.heroComparison = {
    baseline: {
      label: "A", currentCondition: "MONITOR", adverseEventProbability: 0.224981971896,
      recommendedAction: "MONITOR", revision: 12, acceptedSamples: 12,
      excursionMinutes: 1050, excursionUtilization: 0.2430555556,
    },
    intervene: {
      label: "B", currentCondition: "MONITOR", adverseEventProbability: 0.501959657040,
      recommendedAction: "INTERVENE", revision: 13, acceptedSamples: 13,
      excursionMinutes: 1155, excursionUtilization: 0.2673611111,
    },
  };
  const comparisonHtml = window.VitaeAdmin.render(compared, "simulation");
  assert.match(comparisonHtml, /Same current condition, different accumulated history/);
  assert.match(comparisonHtml, /Comparison A[\s\S]*MONITOR[\s\S]*22\.498%/);
  assert.match(comparisonHtml, /Comparison B[\s\S]*MONITOR[\s\S]*50\.196%/);
  assert.match(comparisonHtml, /Action[\s\S]*MONITOR[\s\S]*Action[\s\S]*INTERVENE/);
  assert.match(comparisonHtml, /existing 50% engineering threshold/);

  let advances = 0;
  const button = { dataset: { adminAction: "local-demo-next" } };
  await window.VitaeAdmin.handleClick(
    { target: { closest: () => button } },
    state(),
    { advanceLocalDemo: async () => { advances += 1; }, notify() {} },
  );
  assert.equal(advances, 1);

  const hidden = state();
  hidden.data.localDemoControlsEnabled = false;
  assert.doesNotMatch(window.VitaeAdmin.render(hidden, "dashboard"), /Demo Flow/);

  const waiting = state();
  waiting.localDemo.monitoring.liveState = null;
  assert.match(window.VitaeAdmin.render(waiting, "simulation"), /Recommended action[\s\S]*Waiting for telemetry/i);
  console.log("Local demo frontend contract tests passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

class FakeNode {
  constructor(nodeName, attributes = {}, children = []) {
    this.nodeType = nodeName === "#text" ? 3 : nodeName === "#fragment" ? 11 : 1;
    this.nodeName = this.nodeType === 1 ? nodeName.toUpperCase() : nodeName;
    this._attributes = { ...attributes };
    this.childNodes = [];
    this.parentNode = null;
    this.nodeValue = this.nodeType === 3 ? attributes.value || "" : null;
    this.open = false;
    this.scrollLeft = 0;
    this.scrollTop = 0;
    this.value = attributes.value || "";
    this.checked = Object.prototype.hasOwnProperty.call(attributes, "checked");
    this.selectionStart = 0;
    this.selectionEnd = 0;
    children.forEach((child) => this.appendChild(child));
  }

  get attributes() {
    return Object.entries(this._attributes).map(([name, value]) => ({ name, value: String(value) }));
  }

  get dataset() {
    return {
      uiStateKey: this._attributes["data-ui-state-key"],
      uiScrollKey: this._attributes["data-ui-scroll-key"],
    };
  }

  get lastChild() {
    return this.childNodes[this.childNodes.length - 1] || null;
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this._attributes, name)
      ? String(this._attributes[name])
      : null;
  }

  setAttribute(name, value) {
    this._attributes[name] = String(value);
  }

  removeAttribute(name) {
    delete this._attributes[name];
  }

  matches(selector) {
    return selector.split(",").some((part) => {
      const candidate = part.trim();
      if (candidate === "input") return this.nodeName === "INPUT";
      if (candidate === "textarea") return this.nodeName === "TEXTAREA";
      if (candidate === "select") return this.nodeName === "SELECT";
      if (candidate === "[contenteditable='true']") return this.getAttribute("contenteditable") === "true";
      if (candidate === "input[type='checkbox']") return this.nodeName === "INPUT" && this.getAttribute("type") === "checkbox";
      if (candidate === "input[type='radio']") return this.nodeName === "INPUT" && this.getAttribute("type") === "radio";
      return false;
    });
  }

  appendChild(node) {
    return this.insertBefore(node, null);
  }

  insertBefore(node, reference) {
    if (node.parentNode) {
      const oldIndex = node.parentNode.childNodes.indexOf(node);
      node.parentNode.childNodes.splice(oldIndex, 1);
    }
    const index = reference ? this.childNodes.indexOf(reference) : this.childNodes.length;
    this.childNodes.splice(index < 0 ? this.childNodes.length : index, 0, node);
    node.parentNode = this;
    return node;
  }

  remove() {
    if (!this.parentNode) return;
    const index = this.parentNode.childNodes.indexOf(this);
    this.parentNode.childNodes.splice(index, 1);
    this.parentNode = null;
  }

  cloneNode(deep = false) {
    const clone = new FakeNode(this.nodeName === "#text" ? "#text" : this.nodeName.toLowerCase(), this.nodeType === 3 ? { value: this.nodeValue } : this._attributes);
    clone.open = this.open;
    clone.value = this.value;
    clone.checked = this.checked;
    if (deep) this.childNodes.forEach((child) => clone.appendChild(child.cloneNode(true)));
    return clone;
  }
}

const textNode = (value) => new FakeNode("#text", { value });

function keyedElement(key, properties = {}) {
  return {
    dataset: { uiStateKey: key, uiScrollKey: key },
    open: false,
    scrollLeft: 0,
    scrollTop: 0,
    ...properties,
  };
}

function root(details, scrollRegions = []) {
  return {
    querySelectorAll(selector) {
      if (selector === "details[data-ui-state-key]") return details;
      if (selector === "[data-ui-scroll-key]") return scrollRegions;
      throw new Error(`Unexpected selector: ${selector}`);
    },
  };
}

const page = { scrollLeft: 7, scrollTop: 320 };
const sandbox = {
  document: { scrollingElement: page },
  window: {},
};
vm.createContext(sandbox);
vm.runInContext(
  fs.readFileSync(path.join(__dirname, "core", "ui.js"), "utf8"),
  sandbox,
);

const monitoring = keyedElement("shipment:monitoring", { open: true });
const history = keyedElement("shipment:history", { open: true });
const lifecycle = keyedElement("shipment:lifecycle", { open: false });
const dialog = keyedElement("shipment:dialog", { scrollLeft: 2, scrollTop: 180 });
const captured = sandbox.window.VitaeUI.captureInteractionState(
  root([monitoring, history, lifecycle], [dialog]),
);

const refreshedMonitoring = keyedElement("shipment:monitoring", {
  open: false,
  textContent: "Temperature 8 C",
});
const refreshedHistory = keyedElement("shipment:history", { open: false });
const refreshedLifecycle = keyedElement("shipment:lifecycle", { open: true });
const refreshedDialog = keyedElement("shipment:dialog");
page.scrollLeft = 0;
page.scrollTop = 0;

sandbox.window.VitaeUI.restoreInteractionState(
  root(
    [refreshedMonitoring, refreshedHistory, refreshedLifecycle],
    [refreshedDialog],
  ),
  captured,
);

assert.strictEqual(refreshedMonitoring.open, true);
assert.strictEqual(refreshedHistory.open, true);
assert.strictEqual(refreshedLifecycle.open, false);
assert.strictEqual(refreshedDialog.scrollLeft, 2);
assert.strictEqual(refreshedDialog.scrollTop, 180);
assert.strictEqual(page.scrollLeft, 7);
assert.strictEqual(page.scrollTop, 320);
assert.strictEqual(refreshedMonitoring.textContent, "Temperature 8 C");

refreshedHistory.open = false;
const manuallyClosed = sandbox.window.VitaeUI.captureInteractionState(
  root([refreshedMonitoring, refreshedHistory, refreshedLifecycle]),
);
const nextMonitoring = keyedElement("shipment:monitoring");
const nextHistory = keyedElement("shipment:history", { open: true });
const nextLifecycle = keyedElement("shipment:lifecycle", { open: true });
sandbox.window.VitaeUI.restoreInteractionState(
  root([nextMonitoring, nextHistory, nextLifecycle]),
  manuallyClosed,
);

assert.strictEqual(nextMonitoring.open, true);
assert.strictEqual(nextHistory.open, false);
assert.strictEqual(nextLifecycle.open, false);

const currentSelect = new FakeNode("select", { "data-org-filter": "status" }, [
  new FakeNode("option", { value: "" }, [textNode("All statuses")]),
  new FakeNode("option", { value: "active" }, [textNode("Active")]),
]);
const currentTemperature = new FakeNode("strong", {}, [textNode("6 C")]);
const currentDetails = new FakeNode("details", { "data-ui-state-key": "shipment:monitoring" }, [
  new FakeNode("summary", {}, [textNode("Monitoring")]),
  currentTemperature,
]);
currentDetails.open = true;
const currentShell = new FakeNode("div", {}, [currentSelect, currentDetails]);
const currentRoot = new FakeNode("#fragment", {}, [currentShell]);

const nextSelect = new FakeNode("select", { "data-org-filter": "status" }, [
  new FakeNode("option", { value: "" }, [textNode("All statuses")]),
  new FakeNode("option", { value: "active" }, [textNode("Active")]),
  new FakeNode("option", { value: "completed" }, [textNode("Completed")]),
]);
const nextDetails = new FakeNode("details", { "data-ui-state-key": "shipment:monitoring" }, [
  new FakeNode("summary", {}, [textNode("Monitoring")]),
  new FakeNode("strong", {}, [textNode("8 C")]),
]);
const nextRoot = new FakeNode("#fragment", {}, [new FakeNode("div", {}, [nextSelect, nextDetails])]);

sandbox.window.VitaeUI.reconcileDom(currentRoot, nextRoot, currentSelect);

assert.strictEqual(currentRoot.childNodes[0], currentShell);
assert.strictEqual(currentShell.childNodes[0], currentSelect);
assert.strictEqual(currentSelect.childNodes.length, 2);
assert.strictEqual(currentShell.childNodes[1], currentDetails);
assert.strictEqual(currentDetails.open, true);
assert.strictEqual(currentDetails.childNodes[1].childNodes[0].nodeValue, "8 C");

const searchInput = new FakeNode("input", { "data-org-filter": "search", value: "" });
searchInput.value = "vacc";
searchInput.selectionStart = 4;
searchInput.selectionEnd = 4;
const searchRoot = new FakeNode("#fragment", {}, [searchInput, new FakeNode("span", {}, [textNode("6 C")])]);
const nextSearchRoot = new FakeNode("#fragment", {}, [
  new FakeNode("input", { "data-org-filter": "search", value: "" }),
  new FakeNode("span", {}, [textNode("9 C")]),
]);

sandbox.window.VitaeUI.reconcileDom(searchRoot, nextSearchRoot, searchInput);

assert.strictEqual(searchRoot.childNodes[0], searchInput);
assert.strictEqual(searchInput.value, "vacc");
assert.strictEqual(searchInput.selectionStart, 4);
assert.strictEqual(searchInput.selectionEnd, 4);
assert.strictEqual(searchRoot.childNodes[1].childNodes[0].nodeValue, "9 C");

console.log("UI interaction-state preservation tests passed");

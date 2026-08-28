(function () {
  function escape(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  function humanize(value) {
    return String(value || "").replace(/_/g, " ").replace(/\b\w/g, (character) => character.toUpperCase());
  }

  function badge(value, tone) {
    const normalized = String(tone || value || "neutral").toLowerCase();
    const safeTone = ["safe", "healthy", "delivered", "resolved", "online", "low"].includes(normalized)
      ? "safe"
      : ["warning", "monitor", "medium", "in_progress", "waiting_for_response", "low_battery", "delayed", "offline"].includes(normalized)
        ? "warning"
        : ["high", "at_risk"].includes(normalized)
          ? "elevated"
          : ["critical", "rule_violation"].includes(normalized) ? "critical" : "neutral";
    return `<span class="foundation-badge ${safeTone}">${escape(humanize(value))}</span>`;
  }

  function summary(label, value) {
    return `<article class="foundation-summary-item"><span>${escape(label)}</span><strong>${escape(value)}</strong></article>`;
  }

  function empty(message) {
    return `<div class="foundation-empty">${escape(message)}</div>`;
  }

  function shell({ roleClass, roleLabel, user, nav, active = "dashboard", header, content, mobile = false }) {
    return `
      <div class="vitae-shell ${escape(roleClass)} ${mobile ? "mobile-role-shell" : ""}">
        <aside class="vitae-sidebar">
          <div class="vitae-brand"><span class="vitae-brand-mark" aria-hidden="true">V</span><div><strong>VITAE</strong><span>${escape(roleLabel)}</span></div></div>
          <nav aria-label="${escape(roleLabel)} navigation">${nav.map(([id, label]) => `<button class="${id === active ? "active" : ""}" ${id === active ? 'aria-current="page"' : ""} data-role-page="${escape(id)}" type="button">${escape(label)}</button>`).join("")}</nav>
          <div class="vitae-user"><span>${escape(user.name || user.username)}</span><small>${escape(roleLabel)}</small><button data-logout type="button">Log out</button></div>
        </aside>
        <section class="vitae-workspace">
          ${header}
          <div class="vitae-page-content">${content}</div>
        </section>
      </div>`;
  }

  function pageHeader(eyebrow, title, subtitle, action = "") {
    return `<header class="vitae-page-header"><div><span class="foundation-eyebrow">${escape(eyebrow)}</span><h1>${escape(title)}</h1><p>${escape(subtitle)}</p></div>${action}</header>`;
  }

  function simplePage(title, message) {
    return `<section class="foundation-panel"><h2>${escape(title)}</h2><p>${escape(message)}</p></section>`;
  }

  function captureInteractionState(root) {
    const details = {};
    const scrollRegions = {};
    root.querySelectorAll("details[data-ui-state-key]").forEach((element) => {
      details[element.dataset.uiStateKey] = element.open;
    });
    root.querySelectorAll("[data-ui-scroll-key]").forEach((element) => {
      scrollRegions[element.dataset.uiScrollKey] = {
        left: element.scrollLeft,
        top: element.scrollTop,
      };
    });
    const page = document.scrollingElement;
    return {
      details,
      scrollRegions,
      pageScroll: page ? { left: page.scrollLeft, top: page.scrollTop } : null,
    };
  }

  function restoreInteractionState(root, state) {
    if (!state) return;
    root.querySelectorAll("details[data-ui-state-key]").forEach((element) => {
      const key = element.dataset.uiStateKey;
      if (Object.prototype.hasOwnProperty.call(state.details, key)) {
        element.open = state.details[key];
      }
    });
    root.querySelectorAll("[data-ui-scroll-key]").forEach((element) => {
      const position = state.scrollRegions[element.dataset.uiScrollKey];
      if (!position) return;
      element.scrollLeft = position.left;
      element.scrollTop = position.top;
    });
    const page = document.scrollingElement;
    if (page && state.pageScroll) {
      page.scrollLeft = state.pageScroll.left;
      page.scrollTop = state.pageScroll.top;
    }
  }

  function nodeKey(node) {
    if (node.nodeType !== 1) return null;
    const attributes = [
      "id",
      "data-ui-state-key",
      "data-ui-scroll-key",
      "data-v2-alert-id",
      "data-filter-table",
      "data-org-filter",
      "data-admin-filter",
      "data-simulation-control",
      "name",
    ];
    for (const name of attributes) {
      const value = node.getAttribute(name);
      if (value) return `${node.nodeName}:${name}:${value}`;
    }
    return null;
  }

  function canReconcile(current, next) {
    if (!current || current.nodeType !== next.nodeType) return false;
    if (current.nodeType === 1 && current.nodeName !== next.nodeName) return false;
    const currentKey = nodeKey(current);
    const nextKey = nodeKey(next);
    return (!currentKey && !nextKey) || currentKey === nextKey;
  }

  function focusedControl(element, activeElement) {
    return element === activeElement
      && element.matches("input, textarea, select, [contenteditable='true']");
  }

  function syncAttributes(current, next, preserveControl) {
    const nextNames = new Set([...next.attributes].map((attribute) => attribute.name));
    [...current.attributes].forEach((attribute) => {
      if (attribute.name === "open" && current.nodeName === "DETAILS") return;
      if (preserveControl && ["value", "checked", "selected"].includes(attribute.name)) return;
      if (!nextNames.has(attribute.name)) current.removeAttribute(attribute.name);
    });
    [...next.attributes].forEach((attribute) => {
      if (attribute.name === "open" && current.nodeName === "DETAILS") return;
      if (preserveControl && ["value", "checked", "selected"].includes(attribute.name)) return;
      if (current.getAttribute(attribute.name) !== attribute.value) {
        current.setAttribute(attribute.name, attribute.value);
      }
    });
  }

  function reconcileNode(current, next, activeElement) {
    if (current.nodeType !== 1) {
      if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue;
      return;
    }

    const preserveControl = focusedControl(current, activeElement);
    syncAttributes(current, next, preserveControl);

    if (preserveControl && current.matches("select, textarea, [contenteditable='true']")) return;
    reconcileChildren(current, next, activeElement);

    if (!preserveControl && current.matches("input, textarea, select")) {
      current.value = next.value;
      if (current.matches("input[type='checkbox'], input[type='radio']")) {
        current.checked = next.checked;
      }
    }
  }

  function reconcileChildren(currentParent, nextParent, activeElement) {
    const desired = [...nextParent.childNodes];
    desired.forEach((next, index) => {
      const children = [...currentParent.childNodes];
      const nextKey = nodeKey(next);
      let current = children[index];
      let match = null;

      if (nextKey) {
        match = children.slice(index).find((candidate) => nodeKey(candidate) === nextKey) || null;
      } else if (current && !nodeKey(current) && canReconcile(current, next)) {
        match = current;
      } else {
        match = children.slice(index).find((candidate) => !nodeKey(candidate) && canReconcile(candidate, next)) || null;
      }

      if (match && match !== current) {
        currentParent.insertBefore(match, current || null);
        current = match;
      }

      if (!current || !canReconcile(current, next)) {
        const replacement = next.cloneNode(true);
        currentParent.insertBefore(replacement, current || null);
        current = replacement;
      } else {
        reconcileNode(current, next, activeElement);
      }
    });

    while (currentParent.childNodes.length > desired.length) {
      currentParent.lastChild.remove();
    }
  }

  function reconcileDom(root, nextRoot, activeElement = document.activeElement) {
    reconcileChildren(root, nextRoot, activeElement);
  }

  function reconcileHtml(root, html) {
    const template = document.createElement("template");
    template.innerHTML = html;
    reconcileDom(root, template.content);
  }

  window.VitaeUI = { badge, captureInteractionState, empty, escape, humanize, pageHeader, reconcileDom, reconcileHtml, restoreInteractionState, shell, simplePage, summary };
})();

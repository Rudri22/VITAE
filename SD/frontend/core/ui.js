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
      : ["warning", "medium", "high", "in_progress", "waiting_for_response", "low_battery", "delayed", "offline", "at_risk"].includes(normalized)
        ? "warning"
        : normalized === "critical" ? "critical" : "neutral";
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
          <nav aria-label="${escape(roleLabel)} navigation">${nav.map(([id, label]) => `<button class="${id === active ? "active" : ""}" data-role-page="${escape(id)}" type="button">${escape(label)}</button>`).join("")}</nav>
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

  window.VitaeUI = { badge, empty, escape, humanize, pageHeader, shell, simplePage, summary };
})();

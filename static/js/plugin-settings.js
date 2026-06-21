(function () {
  "use strict";

  async function fetchPlugins() {
    const resp = await fetch("/api/plugins");
    if (!resp.ok) return [];
    return resp.json();
  }

  async function setPluginEnabled(name, enabled) {
    const action = enabled ? "enable" : "disable";
    await fetch(`/api/plugins/${encodeURIComponent(name)}/${action}`, {
      method: "POST",
    });
  }

  function buildPluginRow(plugin) {
    const row = document.createElement("div");
    row.className = "plugin-row";
    row.style.cssText =
      "display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border,#333)";

    const info = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = plugin.name + " v" + plugin.version;
    const desc = document.createElement("div");
    desc.style.cssText = "font-size:0.85em;opacity:0.7;margin-top:2px";
    desc.textContent = plugin.description || "";
    if (plugin.author) {
      desc.textContent += " — by " + plugin.author;
    }
    info.appendChild(title);
    info.appendChild(desc);

    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.checked = plugin.enabled;
    toggle.style.cssText = "width:20px;height:20px;cursor:pointer;margin-left:16px";
    toggle.addEventListener("change", async () => {
      await setPluginEnabled(plugin.name, toggle.checked);
    });

    row.appendChild(info);
    row.appendChild(toggle);
    return row;
  }

  async function renderPluginPanel(container) {
    container.replaceChildren();
    const heading = document.createElement("h3");
    heading.textContent = "Plugins";
    heading.style.marginBottom = "12px";
    container.appendChild(heading);

    const plugins = await fetchPlugins();
    if (plugins.length === 0) {
      const empty = document.createElement("p");
      empty.style.opacity = "0.6";
      empty.textContent = "No plugins installed. Add a plugin directory under plugins/.";
      container.appendChild(empty);
      return;
    }
    plugins.forEach((p) => container.appendChild(buildPluginRow(p)));
  }

  function mount() {
    const existing = document.querySelector('[data-section="plugins"]');
    if (existing) {
      renderPluginPanel(existing);
      return;
    }
    const settingsContainer = document.getElementById("settings-content");
    if (!settingsContainer) return;
    const section = document.createElement("div");
    section.setAttribute("data-section", "plugins");
    section.style.cssText = "margin-top:24px;padding-top:16px;border-top:1px solid var(--border,#333)";
    settingsContainer.appendChild(section);
    renderPluginPanel(section);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }

  document.addEventListener("settings:open", mount);
})();

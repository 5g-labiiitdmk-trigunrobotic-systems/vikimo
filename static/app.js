(() => {
  "use strict";

  const API = "/api";
  const PAGE_SIZE = 20;
  const TITLES = {
    dashboard: "Overview",
    catalogue: "Parts catalogue",
    inventory: "Inventory",
    dealers: "Dealers",
    orders: "Orders",
    assistant: "AI dealer assistant",
    forecast: "Forecast & reorder",
  };
  const state = {
    view: "dashboard",
    products: [],
    dealers: [],
    reorder: [],
    cataloguePage: 1,
    inventoryPage: 1,
    dealerPage: 1,
    ordersPage: 1,
    catalogueCount: 0,
    inventoryCount: 0,
    dealerCount: 0,
    ordersCount: 0,
    productQuery: "",
    inventoryQuery: "",
    dealerQuery: "",
    orderStatus: "",
    forecastSku: "",
    assistantDealer: "",
    chatBusy: false,
    openOrder: null,
  };

  const byId = (id) => document.getElementById(id);
  const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
  const money = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed)
      ? new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(parsed)
      : "—";
  };
  const dateLabel = (value) => {
    if (!value) return "—";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
      ? escapeHTML(value)
      : parsed.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
  };

  class ApiError extends Error {
    constructor(message, status, code, details) {
      super(message);
      this.status = status;
      this.code = code;
      this.details = details;
    }
  }

  async function request(path, options = {}) {
    let response;
    try {
      response = await fetch(path.startsWith("http") ? path : `${API}${path}`, {
        ...options,
        headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers },
      });
    } catch (error) {
      if (error.name === "AbortError") throw new ApiError("The request timed out. Please try again.", 0);
      throw new ApiError("Could not reach DealerDesk. Check that the Django server is running.", 0);
    }
    let payload;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      const error = payload?.error;
      const describe = (item) => (item && typeof item === "object"
        ? ("sku" in item ? `${item.sku}: requested ${item.requested}, available ${item.available}` : JSON.stringify(item))
        : String(item));
      const detailText = Array.isArray(error?.details) ? error.details.map(describe).join("; ") : "";
      const apiError = new ApiError(error?.message || `Request failed (${response.status}).`, response.status, error?.code, detailText);
      apiError.rawDetails = Array.isArray(error?.details) ? error.details : [];
      throw apiError;
    }
    return payload;
  }

  async function getAll(path) {
    const results = [];
    let next = path;
    while (next) {
      const page = await request(next);
      if (!Array.isArray(page.results)) throw new ApiError("The API returned an unexpected list response.");
      results.push(...page.results);
      next = page.next;
    }
    return results;
  }

  function toast(message, isError = false) {
    const item = document.createElement("div");
    item.className = `toast${isError ? " error" : ""}`;
    item.textContent = message;
    byId("toast-region").append(item);
    window.setTimeout(() => item.remove(), 4400);
  }

  function showNotice(message, isError = true) {
    const notice = byId("global-notice");
    notice.textContent = message;
    notice.classList.toggle("success", !isError);
    notice.classList.remove("hidden");
  }

  function hideNotice() {
    byId("global-notice").classList.add("hidden");
  }

  function errorDetail(error) {
    const details = [
      error?.message || "An unexpected error occurred.",
      error?.code ? `Code: ${error.code}` : "",
      error?.status ? `HTTP ${error.status}` : "",
      error?.details ? `Details: ${error.details}` : "",
    ].filter(Boolean);
    return details.join(" · ");
  }

  function showTableError(container, error, retryView = state.view) {
    container.innerHTML = `<div class="empty-state"><div class="empty-icon"><svg class="icon"><use href="#i-refresh"/></svg></div><strong>Couldn’t load this data</strong><span>${escapeHTML(errorDetail(error))}</span><button class="text-button retry-button" data-retry="${escapeHTML(retryView)}">${retryView === "dashboard" ? "Retry dashboard data" : "Try again"}</button></div>`;
  }

  function emptyTable(colspan, message) {
    return `<tr><td colspan="${colspan}"><div class="empty-state compact-empty"><div class="empty-icon"><svg class="icon"><use href="#i-box"/></svg></div><strong>No records found</strong><span>${escapeHTML(message)}</span></div></td></tr>`;
  }

  function renderPagination(containerId, page, count, reload) {
    const container = byId(containerId);
    const pages = Math.max(1, Math.ceil(count / PAGE_SIZE));
    if (pages <= 1) {
      container.innerHTML = count ? `<span>${count} records</span>` : "";
      return;
    }
    container.innerHTML = `<span>Page ${page} of ${pages} · ${count} records</span><button class="page-button" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""} aria-label="Previous page">‹</button><button class="page-button" data-page="${page + 1}" ${page >= pages ? "disabled" : ""} aria-label="Next page">›</button>`;
    container.querySelectorAll("[data-page]").forEach((button) => {
      button.addEventListener("click", () => reload(Number(button.dataset.page)));
    });
  }

  function statusBadge(status) {
    const value = String(status || "UNKNOWN").toUpperCase();
    return `<span class="status-badge ${escapeHTML(value.toLowerCase())}">${escapeHTML(value)}</span>`;
  }

  function stockStatus(stock) {
    const amount = Number(stock);
    const cls = amount === 0 ? "out" : amount < 10 ? "low" : "";
    const label = amount === 0 ? "Out of stock" : amount < 10 ? "Low stock" : "In stock";
    return `<span class="stock-label ${cls}"><i class="stock-mark"></i>${label}</span>`;
  }

  function queryString(params) {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== "" && value !== null && value !== undefined) query.set(key, value);
    });
    const result = query.toString();
    return result ? `?${result}` : "";
  }

  function productOptions(selected = "") {
    const options = state.products.map((product) => (
      `<option value="${escapeHTML(product.sku)}" ${product.sku === selected ? "selected" : ""}>${escapeHTML(product.sku)} · ${escapeHTML(product.name)} · ${money(product.price)}</option>`
    ));
    return `<option value="">Select a catalogue part</option>${options.join("")}`;
  }

  function dealerOptions(selected = "") {
    return `<option value="">Select a dealer account</option>${state.dealers.map((dealer) => (
      `<option value="${dealer.id}" ${String(dealer.id) === String(selected) ? "selected" : ""}>${escapeHTML(dealer.dealer_code)} · ${escapeHTML(dealer.name)}</option>`
    )).join("")}`;
  }

  function setView(view) {
    if (!TITLES[view]) return;
    state.view = view;
    document.querySelectorAll(".view").forEach((section) => section.classList.toggle("active", section.id === `view-${view}`));
    document.querySelectorAll(".nav-link").forEach((link) => link.classList.toggle("active", link.dataset.view === view));
    byId("crumb-current").textContent = TITLES[view];
    byId("sidebar").classList.remove("open");
    hideNotice();
    if (view === "catalogue") loadCatalogue();
    if (view === "inventory") loadInventory();
    if (view === "dealers") loadDealers();
    if (view === "orders") loadOrders();
    if (view === "assistant") renderChat();
    if (view === "forecast") loadForecast();
    if (view === "dashboard") loadDashboard();
  }

  async function loadProductsCache() {
    if (state.products.length) return state.products;
    const result = await getAll("/products/?page_size=100");
    state.products = result;
    return result;
  }

  async function loadDealersCache() {
    if (state.dealers.length) return state.dealers;
    state.dealers = await getAll("/dealers/?page_size=100");
    return state.dealers;
  }

  async function loadReorderCache() {
    const payload = await request("/reorder-suggestions/");
    if (!Array.isArray(payload.results)) throw new ApiError("The reorder API returned an unexpected response.");
    state.reorder = payload.results;
    return state.reorder;
  }

  async function loadDashboard() {
    const ordersTarget = byId("dashboard-orders");
    const reorderTarget = byId("dashboard-reorders");
    const inventoryTarget = byId("inventory-health");
    const results = await Promise.allSettled([
      loadProductsCache(),
      loadDealersCache(),
      request("/orders/?page_size=5"),
      request("/orders/?status=DRAFT&page_size=1"),
      loadReorderCache(),
    ]);
    const [productsResult, dealersResult, ordersResult, draftsResult, reorderResult] = results;

    if (productsResult.status === "fulfilled") {
      const products = productsResult.value;
      byId("metric-products").textContent = new Intl.NumberFormat("en-IN").format(products.length);
      try {
        renderHealth(inventoryTarget, products);
      } catch (error) {
        showTableError(inventoryTarget, error, "dashboard");
      }
    } else {
      byId("metric-products").textContent = "—";
      showTableError(inventoryTarget, productsResult.reason, "dashboard");
    }

    if (dealersResult.status === "fulfilled") {
      byId("metric-dealers").textContent = new Intl.NumberFormat("en-IN").format(dealersResult.value.length);
    } else {
      byId("metric-dealers").textContent = "—";
    }

    if (draftsResult.status === "fulfilled") {
      byId("metric-drafts").textContent = new Intl.NumberFormat("en-IN").format(draftsResult.value.count);
    } else {
      byId("metric-drafts").textContent = "—";
    }

    if (ordersResult.status === "fulfilled") {
      const orders = ordersResult.value;
      if (!Array.isArray(orders.results)) {
        showTableError(ordersTarget, new ApiError("The orders API returned an unexpected list response."), "dashboard");
      } else {
        renderOrderTable(ordersTarget, orders.results, true);
      }
    } else {
      showTableError(ordersTarget, ordersResult.reason, "dashboard");
    }

    if (reorderResult.status === "fulfilled") {
      const needsOrder = reorderResult.value.filter((item) => Number(item.reorder_qty) > 0);
      byId("metric-reorders").textContent = new Intl.NumberFormat("en-IN").format(needsOrder.length);
      try {
        renderReorders(reorderTarget, needsOrder.slice(0, 6), "No parts currently need replenishment.");
      } catch (error) {
        showTableError(reorderTarget, error, "dashboard");
      }
    } else {
      byId("metric-reorders").textContent = "—";
      showTableError(reorderTarget, reorderResult.reason, "dashboard");
    }
  }

  function renderHealth(target, products) {
    if (!products.length) {
      target.innerHTML = '<div class="empty-state compact-empty"><strong>No catalogue parts</strong><span>Seed products to see inventory health.</span></div>';
      return;
    }
    const out = products.filter((item) => Number(item.stock) === 0).length;
    const low = products.filter((item) => Number(item.stock) > 0 && Number(item.stock) < 10).length;
    const healthy = products.length - out - low;
    const rows = [
      { label: "Healthy stock", count: healthy, cls: "" },
      { label: "Low stock · under 10 units", count: low, cls: "warning" },
      { label: "Out of stock", count: out, cls: "danger" },
    ];
    target.innerHTML = rows.map((row) => {
      const percentage = Math.round((row.count / products.length) * 100);
      return `<div><div class="health-row"><span>${row.label}</span><strong>${row.count} <small>(${percentage}%)</small></strong></div><div class="health-bar ${row.cls}"><span style="width:${percentage}%"></span></div></div>`;
    }).join("");
  }

  function renderReorders(target, rows, emptyMessage) {
    if (!rows.length) {
      target.innerHTML = `<div class="empty-state compact-empty"><div class="empty-icon"><svg class="icon"><use href="#i-chart"/></svg></div><strong>Inventory is in good shape</strong><span>${escapeHTML(emptyMessage)}</span></div>`;
      return;
    }
    target.innerHTML = rows.map((item) => {
      const product = state.products.find((entry) => entry.sku === item.sku);
      return `<div class="reorder-item"><div><strong>${escapeHTML(item.sku)}${product ? ` · ${escapeHTML(product.name)}` : ""}</strong><small>${Number(item.current_stock)} available · reorder point ${Math.ceil(Number(item.reorder_point))}</small></div><span class="reorder-qty">+${Math.ceil(Number(item.reorder_qty))} units</span></div>`;
    }).join("");
  }

  function renderOrderTable(target, orders, compact = false) {
    if (!orders.length) {
      target.innerHTML = `<table><tbody>${emptyTable(compact ? 3 : 6, "Drafts and orders will appear here once created.")}</tbody></table>`;
      return;
    }
    const rows = orders.map((order) => {
      const dealer = state.dealers.find((item) => Number(item.id) === Number(order.dealer_id));
      return `<tr class="order-row" data-order="${escapeHTML(order.order_number)}" tabindex="0"><td><span class="primary-cell">${escapeHTML(order.order_number)}</span><span class="secondary-cell">${dateLabel(order.created_at)}</span></td>${compact ? "" : `<td>${dealer ? escapeHTML(dealer.name) : `Dealer #${escapeHTML(order.dealer_id)}`}</td>`}<td>${statusBadge(order.status)}</td><td class="money">${money(order.grand_total)}</td>${compact ? "" : `<td>${Array.isArray(order.items) ? order.items.length : 0} line${order.items?.length === 1 ? "" : "s"}</td><td><button class="text-button view-order" type="button" data-order="${escapeHTML(order.order_number)}">View</button></td>`}</tr>`;
    }).join("");
    target.innerHTML = `<table><thead><tr><th>ORDER</th>${compact ? "" : "<th>DEALER</th>"}<th>STATUS</th><th>TOTAL</th>${compact ? "" : "<th>ITEMS</th><th></th>"}</tr></thead><tbody>${rows}</tbody></table>`;
  }

  async function loadCatalogue(page = state.cataloguePage) {
    const target = byId("catalogue-table");
    target.innerHTML = '<div class="loading-line">Loading catalogue…</div>';
    const search = byId("product-search").value.trim();
    const category = byId("product-category").value.trim();
    const vehicle = byId("product-vehicle").value.trim();
    try {
      const payload = await request(`/products/${queryString({ page, page_size: PAGE_SIZE, search, category, vehicle })}`);
      state.cataloguePage = page;
      state.catalogueCount = payload.count;
      byId("catalogue-count").textContent = `${payload.count} parts`;
      const rows = payload.results || [];
      target.innerHTML = `<table><thead><tr><th>PART</th><th>SKU</th><th>CATEGORY</th><th>BRAND</th><th>FITMENT</th><th>PRICE</th><th>AVAILABLE</th></tr></thead><tbody>${rows.length ? rows.map((product) => `<tr><td class="primary-cell">${escapeHTML(product.name)}<span class="secondary-cell">${escapeHTML(product.description || "No description")}</span></td><td><span class="sku-chip">${escapeHTML(product.sku)}</span></td><td>${escapeHTML(product.category)}</td><td>${escapeHTML(product.brand)}</td><td>${escapeHTML(product.vehicle_fitment)}</td><td class="money">${money(product.price)}</td><td>${Number(product.stock)} <span class="secondary-cell">${stockStatus(product.stock)}</span></td></tr>`).join("") : emptyTable(7, "Try another SKU, category or fitment.")}</tbody></table>`;
      renderPagination("catalogue-pagination", page, payload.count, loadCatalogue);
    } catch (error) {
      showTableError(target, error, "catalogue");
    }
  }

  async function loadInventory(page = state.inventoryPage) {
    const target = byId("inventory-table");
    target.innerHTML = '<div class="loading-line">Loading inventory…</div>';
    try {
      const payload = await request(`/products/${queryString({ page, page_size: PAGE_SIZE, search: byId("inventory-search").value.trim() })}`);
      state.inventoryPage = page;
      state.inventoryCount = payload.count;
      byId("inventory-count").textContent = `${payload.count} parts`;
      const rows = payload.results || [];
      target.innerHTML = `<table><thead><tr><th>PART</th><th>SKU</th><th>AVAILABLE</th><th>STOCK STATUS</th><th>UPDATE AVAILABLE UNITS</th></tr></thead><tbody>${rows.length ? rows.map((product) => `<tr><td class="primary-cell">${escapeHTML(product.name)}<span class="secondary-cell">${escapeHTML(product.category)} · ${escapeHTML(product.brand)}</span></td><td><span class="sku-chip">${escapeHTML(product.sku)}</span></td><td class="money">${Number(product.stock)}</td><td>${stockStatus(product.stock)}</td><td><form class="inline-stock-form" data-stock-sku="${escapeHTML(product.sku)}"><input name="quantity" type="number" min="0" step="1" value="${Number(product.stock)}" aria-label="New stock quantity for ${escapeHTML(product.sku)}"><button type="submit">Save</button></form></td></tr>`).join("") : emptyTable(5, "No matching inventory records.")}</tbody></table>`;
      renderPagination("inventory-pagination", page, payload.count, loadInventory);
    } catch (error) {
      showTableError(target, error, "inventory");
    }
  }

  async function loadDealers(page = state.dealerPage) {
    const target = byId("dealers-table");
    target.innerHTML = '<div class="loading-line">Loading dealer network…</div>';
    try {
      await loadDealersCache();
      const term = byId("dealer-search").value.trim().toLowerCase();
      const filtered = state.dealers.filter((dealer) => [dealer.name, dealer.dealer_code, dealer.city, dealer.email].some((value) => String(value || "").toLowerCase().includes(term)));
      state.dealerCount = filtered.length;
      byId("dealer-count").textContent = `${filtered.length} dealers`;
      const start = (page - 1) * PAGE_SIZE;
      const rows = filtered.slice(start, start + PAGE_SIZE);
      state.dealerPage = page;
      target.innerHTML = `<table><thead><tr><th>DEALER</th><th>CODE</th><th>CITY</th><th>CONTACT</th><th>CREDIT LIMIT</th></tr></thead><tbody>${rows.length ? rows.map((dealer) => `<tr><td class="primary-cell">${escapeHTML(dealer.name)}<span class="secondary-cell">${escapeHTML(dealer.gstin || "GSTIN not provided")}</span></td><td><span class="sku-chip">${escapeHTML(dealer.dealer_code)}</span></td><td>${escapeHTML(dealer.city || "—")}</td><td>${escapeHTML(dealer.phone || dealer.email || "—")}<span class="secondary-cell">${dealer.phone && dealer.email ? escapeHTML(dealer.email) : ""}</span></td><td class="money">${money(dealer.credit_limit)}</td></tr>`).join("") : emptyTable(5, term ? "No dealer matched your search." : "Create your first dealer account.")}</tbody></table>`;
      renderPagination("dealers-pagination", page, filtered.length, loadDealers);
    } catch (error) {
      showTableError(target, error, "dealers");
    }
  }

  async function createDealer(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const body = {
      dealer_code: String(data.get("dealer_code")).trim(),
      name: String(data.get("name")).trim(),
      city: String(data.get("city") || "").trim(),
      phone: String(data.get("phone") || "").trim(),
      email: String(data.get("email") || "").trim(),
      gstin: String(data.get("gstin") || "").trim(),
      credit_limit: String(data.get("credit_limit") || "0"),
    };
    try {
      await request("/dealers/", { method: "POST", body: JSON.stringify(body) });
      state.dealers = [];
      form.reset();
      byId("dealer-form-panel").classList.add("hidden");
      toast("Dealer account created.");
      loadDealers(1);
      loadAssistantDealerOptions();
      loadOrderDealerOptions();
    } catch (error) {
      toast(formatError(error), true);
    }
  }

  async function saveInventory(event) {
    const form = event.target.closest("[data-stock-sku]");
    if (!form) return;
    event.preventDefault();
    const quantityInput = form.elements.quantity;
    const quantity = Number(quantityInput.value);
    if (!Number.isInteger(quantity) || quantity < 0) {
      toast("Enter a whole number of units, zero or more.", true);
      return;
    }
    const sku = form.dataset.stockSku;
    const submit = form.querySelector("button");
    submit.disabled = true;
    try {
      const result = await request(`/inventory/${encodeURIComponent(sku)}/`, {
        method: "PUT",
        body: JSON.stringify({ quantity, reason: "Dashboard stock correction" }),
      });
      const cached = state.products.find((product) => product.sku === sku);
      if (cached) cached.stock = result.quantity;
      toast(`${sku} stock updated to ${result.quantity}.`);
      loadInventory(state.inventoryPage);
    } catch (error) {
      toast(formatError(error), true);
      submit.disabled = false;
    }
  }

  async function loadOrderDealerOptions() {
    try {
      await loadDealersCache();
      const selected = byId("order-dealer").value;
      byId("order-dealer").innerHTML = dealerOptions(selected);
    } catch (error) {
      byId("order-dealer").innerHTML = '<option value="">Dealer list unavailable</option>';
      toast(error.message, true);
    }
  }

  async function loadAssistantDealerOptions() {
    try {
      await loadDealersCache();
      const select = byId("assistant-dealer");
      const selected = state.assistantDealer || localStorage.getItem("vikmo.assistantDealer") || "";
      select.innerHTML = dealerOptions(selected);
      if (selected && state.dealers.some((dealer) => String(dealer.id) === selected)) {
        state.assistantDealer = selected;
        select.value = selected;
        updateSessionLabel();
      }
    } catch (error) {
      byId("assistant-dealer").innerHTML = '<option value="">Dealer list unavailable</option>';
      toast(error.message, true);
    }
  }

  function addOrderLine(sku = "") {
    const row = document.createElement("div");
    row.className = "order-line";
    row.innerHTML = `<select name="sku" required aria-label="Product">${productOptions(sku)}</select><input name="quantity" type="number" min="1" step="1" value="1" required aria-label="Quantity"><button class="remove-line" type="button" aria-label="Remove line"><svg class="icon"><use href="#i-close"/></svg></button>`;
    row.querySelector(".remove-line").addEventListener("click", () => {
      if (byId("order-lines").children.length > 1) row.remove();
      else {
        row.querySelector("select").value = "";
        row.querySelector("input").value = "1";
      }
    });
    byId("order-lines").append(row);
  }

  async function startOrderForm() {
    try {
      await Promise.all([loadProductsCache(), loadDealersCache()]);
      byId("order-dealer").innerHTML = dealerOptions();
      byId("order-lines").innerHTML = "";
      addOrderLine();
    } catch (error) {
      showNotice(error.message);
    }
  }

  function formatOrder(order) {
    const lines = (order.items || []).map((item) => `${item.sku} × ${item.quantity}`).join(", ");
    return `${order.order_number} · ${lines} · Total ${money(order.grand_total)} · ${order.status}`;
  }

  function formatError(error) {
    const details = error.details ? ` ${error.details}` : "";
    return `${error.message}${details}`;
  }

  async function submitOrder(event) {
    event.preventDefault();
    const dealerId = Number(byId("order-dealer").value);
    const items = Array.from(byId("order-lines").querySelectorAll(".order-line")).map((line) => ({
      sku: line.querySelector('[name="sku"]').value,
      quantity: Number(line.querySelector('[name="quantity"]').value),
    }));
    if (!Number.isInteger(dealerId) || dealerId < 1 || items.some((item) => !item.sku || !Number.isInteger(item.quantity) || item.quantity < 1)) {
      showOrderFeedback("Choose a dealer and provide a catalogue SKU with a whole quantity for every line.", true);
      return;
    }
    const submit = event.currentTarget.querySelector('button[type="submit"]');
    submit.disabled = true;
    showOrderFeedback("Creating your draft order…", false);
    try {
      const order = await request("/orders/", { method: "POST", body: JSON.stringify({ dealer_id: dealerId, items }) });
      showOrderFeedback(formatOrder(order), false);
      toast("Draft order created. It has not been confirmed.");
      loadOrders(1);
      loadDashboard();
      openOrder(order.order_number);
    } catch (error) {
      showOrderFeedback(formatError(error), true);
    } finally {
      submit.disabled = false;
    }
  }

  function showOrderFeedback(message, isError) {
    const element = byId("order-feedback");
    element.textContent = message;
    element.classList.toggle("error", isError);
    element.classList.remove("hidden");
  }

  const NEXT_ACTIONS = {
    DRAFT: [
      { action: "confirm", label: "Confirm order", primary: true, hint: "Checks stock, deducts it and locks prices." },
      { action: "cancel", label: "Cancel draft", danger: true },
    ],
    CONFIRMED: [
      { action: "dispatch", label: "Mark dispatched", primary: true },
      { action: "cancel", label: "Cancel order", danger: true, hint: "Returns the reserved units to stock." },
    ],
    DISPATCHED: [{ action: "deliver", label: "Mark delivered", primary: true }],
    DELIVERED: [],
    CANCELLED: [],
  };
  const FLOW = ["DRAFT", "CONFIRMED", "DISPATCHED", "DELIVERED"];

  function openDrawerShell(title) {
    byId("drawer-title").textContent = title;
    byId("order-drawer").classList.remove("hidden");
    byId("order-drawer-backdrop").classList.remove("hidden");
  }

  function closeOrder() {
    state.openOrder = null;
    byId("order-drawer").classList.add("hidden");
    byId("order-drawer-backdrop").classList.add("hidden");
  }

  async function openOrder(orderNumber) {
    state.openOrder = orderNumber;
    openDrawerShell(orderNumber);
    byId("drawer-body").innerHTML = '<div class="loading-line">Loading order…</div>';
    try {
      await loadDealersCache();
      renderOrderDetail(await request(`/orders/${encodeURIComponent(orderNumber)}/`));
    } catch (error) {
      byId("drawer-body").innerHTML = `<div class="form-feedback error">${escapeHTML(errorDetail(error))}</div>`;
    }
  }

  function renderOrderDetail(order) {
    if (state.openOrder !== order.order_number) return;
    const dealer = state.dealers.find((item) => Number(item.id) === Number(order.dealer_id));
    const lines = (order.items || []).map((item) => `<tr><td><span class="primary-cell">${escapeHTML(item.sku)}</span><span class="secondary-cell">${escapeHTML(item.name)}</span></td><td>${Number(item.quantity)}</td><td class="money">${money(item.unit_price)}</td><td class="money">${money(item.line_subtotal)}</td><td class="money">${money(item.line_tax)}</td><td class="money">${money(item.line_total)}</td></tr>`).join("");
    const cancelled = order.status === "CANCELLED";
    const reached = FLOW.indexOf(order.status);
    const steps = cancelled
      ? '<div class="flow-cancelled">This order was cancelled.</div>'
      : `<ol class="flow">${FLOW.map((step, index) => `<li class="${index < reached ? "done" : index === reached ? "current" : ""}"><i></i>${step.charAt(0) + step.slice(1).toLowerCase()}</li>`).join("")}</ol>`;
    const actions = NEXT_ACTIONS[order.status] || [];
    const buttons = actions.map((item) => `<button type="button" class="button ${item.primary ? "button-primary" : item.danger ? "button-danger" : "button-secondary"}" data-action="${item.action}" data-label="${escapeHTML(item.label)}">${escapeHTML(item.label)}</button>`).join("");
    const hints = actions.filter((item) => item.hint).map((item) => `<p class="action-hint"><strong>${escapeHTML(item.label)}:</strong> ${escapeHTML(item.hint)}</p>`).join("");
    const priceNote = order.status === "DRAFT"
      ? "Draft prices follow the current catalogue and are locked when the order is confirmed."
      : cancelled ? "Prices were locked or the order was cancelled before confirmation." : "Prices were locked when the order was confirmed.";
    byId("drawer-title").textContent = order.order_number;
    byId("drawer-body").innerHTML = `
      <div class="drawer-meta">${statusBadge(order.status)}<span>${dealer ? escapeHTML(dealer.name) : `Dealer #${escapeHTML(order.dealer_id)}`}</span><span>Created ${dateLabel(order.created_at)}</span></div>
      ${steps}
      <div class="table-wrap"><table class="detail-table"><thead><tr><th>PART</th><th>QTY</th><th>UNIT PRICE</th><th>SUBTOTAL</th><th>GST 18%</th><th>TOTAL</th></tr></thead><tbody>${lines}</tbody></table></div>
      <dl class="totals"><div><dt>Subtotal</dt><dd id="detail-subtotal">${money(order.subtotal)}</dd></div><div><dt>GST (18%)</dt><dd id="detail-tax">${money(order.tax_total)}</dd></div><div class="grand"><dt>Grand total</dt><dd id="detail-grand">${money(order.grand_total)}</dd></div></dl>
      <p class="data-note">${priceNote}</p>
      <div id="drawer-error" class="form-feedback error hidden" role="alert"></div>
      ${actions.length ? `<div class="drawer-actions">${buttons}</div>${hints}` : '<p class="data-note">No further actions are available for this order.</p>'}`;
  }

  function showDrawerError(error) {
    const box = byId("drawer-error");
    if (!box) return;
    const short = (error.rawDetails || []).filter((item) => item && typeof item === "object" && "sku" in item);
    const list = short.length
      ? `<ul>${short.map((item) => `<li><strong>${escapeHTML(item.sku)}</strong>: requested ${Number(item.requested)}, only ${Number(item.available)} available</li>`).join("")}</ul><span>Nothing was changed. Lower the quantities or restock, then try again.</span>`
      : error.details ? `<span>${escapeHTML(error.details)}</span>` : "";
    box.innerHTML = `<strong>${escapeHTML(error.message)}</strong>${list}`;
    box.classList.remove("hidden");
  }

  async function runOrderAction(button) {
    const orderNumber = state.openOrder;
    const action = button.dataset.action;
    if (action === "cancel" && button.dataset.armed !== "1") {
      button.dataset.armed = "1";
      button.textContent = "Click again to cancel";
      window.setTimeout(() => {
        if (button.isConnected && button.dataset.armed === "1") {
          button.dataset.armed = "";
          button.textContent = button.dataset.label;
        }
      }, 4000);
      return;
    }
    const buttons = byId("drawer-body").querySelectorAll("[data-action]");
    buttons.forEach((item) => { item.disabled = true; });
    try {
      const order = await request(`/orders/${encodeURIComponent(orderNumber)}/${action}/`, { method: "POST" });
      state.products = [];
      renderOrderDetail(order);
      toast(`${order.order_number} is now ${order.status.toLowerCase()}.`);
      refreshAfterOrderChange();
    } catch (error) {
      buttons.forEach((item) => { item.disabled = false; });
      showDrawerError(error);
      if (error.code === "INVALID_TRANSITION" || error.code === "NOT_FOUND") {
        try { renderOrderDetail(await request(`/orders/${encodeURIComponent(orderNumber)}/`)); showDrawerError(error); } catch { /* keep the message */ }
        refreshAfterOrderChange();
      }
    }
  }

  function refreshAfterOrderChange() {
    if (state.view === "orders") loadOrders(state.ordersPage);
    if (state.view === "dashboard") loadDashboard();
  }

  async function loadOrders(page = state.ordersPage) {
    const target = byId("orders-table");
    target.innerHTML = '<div class="loading-line">Loading orders…</div>';
    try {
      await loadDealersCache();
      const payload = await request(`/orders/${queryString({ page, page_size: PAGE_SIZE, status: state.orderStatus })}`);
      state.ordersPage = page;
      state.ordersCount = payload.count;
      renderOrderTable(target, payload.results || [], false);
      renderPagination("orders-pagination", page, payload.count, loadOrders);
    } catch (error) {
      showTableError(target, error, "orders");
    }
  }

  function chatStoreKey(sessionId) {
    return `vikmo.chat.${sessionId}`;
  }

  function sessionForDealer(dealerId) {
    const key = `vikmo.session.${dealerId}`;
    let session = localStorage.getItem(key);
    if (!session) {
      session = `dealer-${dealerId}-${crypto.randomUUID()}`;
      localStorage.setItem(key, session);
    }
    return session;
  }

  function currentSession() {
    return state.assistantDealer ? sessionForDealer(state.assistantDealer) : "";
  }

  function updateSessionLabel() {
    const id = currentSession();
    byId("session-label").textContent = id ? `${id.slice(0, 24)}${id.length > 24 ? "…" : ""}` : "Choose a dealer to begin";
  }

  function renderChat() {
    updateSessionLabel();
    const target = byId("chat-messages");
    if (!state.assistantDealer) {
      target.innerHTML = '<div class="chat-welcome"><div class="welcome-orb"><svg class="icon"><use href="#i-users"/></svg></div><h2>Select a dealer account</h2><p>Assistant requests are scoped to the dealer you choose above.</p></div>';
      return;
    }
    const messages = JSON.parse(localStorage.getItem(chatStoreKey(currentSession())) || "[]");
    if (!messages.length) {
      target.innerHTML = '<div class="chat-welcome"><div class="welcome-orb"><svg class="icon"><use href="#i-spark"/></svg></div><h2>How can I help today?</h2><p>Ask for a part, check current stock, or request a draft order.</p><div class="suggestion-chips"><button type="button" data-prompt="Find brake pads for a Bajaj Pulsar 150">Find compatible parts</button><button type="button" data-prompt="Check stock for ACC-1001">Check live stock</button></div></div>';
      return;
    }
    target.innerHTML = messages.map((message) => renderMessage(message.role, message.text, message.meta)).join("");
    target.scrollTop = target.scrollHeight;
  }

  function renderMessage(role, text, meta = "") {
    return `<div class="chat-message ${role === "user" ? "user" : "assistant"}"><div><div class="message-bubble">${escapeHTML(text)}</div>${meta ? `<div class="message-meta">${escapeHTML(meta)}</div>` : ""}</div></div>`;
  }

  function saveChatMessage(role, text, meta = "") {
    const key = chatStoreKey(currentSession());
    const messages = JSON.parse(localStorage.getItem(key) || "[]");
    messages.push({ role, text, meta });
    localStorage.setItem(key, JSON.stringify(messages.slice(-40)));
  }

  async function sendChat(event) {
    event.preventDefault();
    const input = byId("chat-input");
    const message = input.value.trim();
    if (!message || !state.assistantDealer || state.chatBusy) {
      if (!state.assistantDealer) toast("Select a dealer account before chatting.", true);
      return;
    }
    state.chatBusy = true;
    input.value = "";
    const submit = byId("chat-form").querySelector("button");
    submit.disabled = true;
    saveChatMessage("user", message);
    renderChat();
    const pending = document.createElement("div");
    pending.className = "chat-message assistant typing";
    pending.innerHTML = '<div class="message-bubble">Checking DealerDesk…</div>';
    byId("chat-messages").append(pending);
    byId("chat-messages").scrollTop = byId("chat-messages").scrollHeight;
    try {
      const result = await request("/assistant/chat/", {
        method: "POST",
        body: JSON.stringify({ session_id: currentSession(), dealer_id: Number(state.assistantDealer), message }),
      });
      pending.remove();
      const metadata = result.actions?.length
        ? result.actions.map((action) => `${action.type.replaceAll("_", " ")} · ${action.order_number}`).join(" | ")
        : "";
      saveChatMessage("assistant", result.reply || "No reply was returned.", metadata);
      renderChat();
    } catch (error) {
      pending.remove();
      saveChatMessage("assistant", `I couldn't complete that request. ${formatError(error)}`, "Request failed");
      renderChat();
      toast("Assistant request failed.", true);
    } finally {
      state.chatBusy = false;
      submit.disabled = false;
      input.focus();
    }
  }

  async function loadForecast() {
    const table = byId("reorder-table");
    table.innerHTML = '<div class="loading-line">Loading reorder plan…</div>';
    try {
      const [rows, products] = await Promise.all([loadReorderCache(), loadProductsCache()]);
      const actionable = rows.filter((item) => Number(item.reorder_qty) > 0);
      table.innerHTML = `<table><thead><tr><th>PART</th><th>LIVE STOCK</th><th>LEAD TIME</th><th>LEAD-TIME DEMAND</th><th>SAFETY STOCK</th><th>REORDER POINT</th><th>ORDER QTY</th></tr></thead><tbody>${rows.length ? rows.map((item) => {
        const product = products.find((entry) => entry.sku === item.sku);
        return `<tr><td class="primary-cell">${escapeHTML(item.sku)}<span class="secondary-cell">${escapeHTML(product?.name || "Catalogue part")}</span></td><td>${Number(item.current_stock)} ${stockStatus(item.current_stock)}</td><td>${Number(item.lead_time_weeks)} wk</td><td>${Number(item.lead_time_demand).toFixed(2)}</td><td>${Number(item.safety_stock)}</td><td>${Number(item.reorder_point).toFixed(2)}</td><td>${Number(item.reorder_qty) > 0 ? `<span class="reorder-qty">+${Math.ceil(Number(item.reorder_qty))}</span>` : '<span class="stock-label">No reorder</span>'}</td></tr>`;
      }).join("") : emptyTable(7, "No reorder rows were returned.")}</tbody></table>`;
      byId("metric-reorders").textContent = actionable.length;
      const skuSelect = byId("forecast-sku");
      const previous = state.forecastSku || skuSelect.value;
      skuSelect.innerHTML = `<option value="">Select a SKU</option>${rows.map((item) => `<option value="${escapeHTML(item.sku)}">${escapeHTML(item.sku)}${products.find((product) => product.sku === item.sku) ? ` · ${escapeHTML(products.find((product) => product.sku === item.sku).name)}` : ""}</option>`).join("")}`;
      if (previous && rows.some((item) => item.sku === previous)) {
        skuSelect.value = previous;
        loadForecastDetail(previous);
      }
    } catch (error) {
      showTableError(table, error, "forecast");
      showNotice(error.message);
    }
  }

  async function loadForecastDetail(sku) {
    state.forecastSku = sku;
    const target = byId("forecast-detail");
    if (!sku) {
      target.innerHTML = '<div class="empty-state compact-empty"><div class="empty-icon"><svg class="icon"><use href="#i-chart"/></svg></div><strong>Select a part</strong><span>Its four forecast weeks will appear here.</span></div>';
      return;
    }
    target.innerHTML = '<div class="loading-line">Loading SKU forecast…</div>';
    try {
      const payload = await request(`/forecast/${encodeURIComponent(sku)}/`);
      if (!Array.isArray(payload.forecast)) throw new ApiError("Forecast response has an unexpected shape.");
      const max = Math.max(1, ...payload.forecast.map((item) => Number(item.forecast_units)));
      target.innerHTML = payload.forecast.map((item) => {
        const value = Number(item.forecast_units);
        return `<div class="forecast-week"><span>${dateLabel(item.week_start)}</span><span class="week-track"><span style="width:${Math.max(2, Math.round(value / max * 100))}%"></span></span><span class="week-units">${Number.isFinite(value) ? value.toLocaleString("en-IN") : "—"} u</span></div>`;
      }).join("");
    } catch (error) {
      showTableError(target, error, "forecast");
    }
  }

  function debounce(fn, wait = 300) {
    let timer;
    return (...args) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => fn(...args), wait);
    };
  }

  function bindEvents() {
    document.querySelectorAll(".nav-link").forEach((link) => link.addEventListener("click", () => setView(link.dataset.view)));
    document.querySelectorAll("[data-go]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.go)));
    byId("menu-toggle").addEventListener("click", () => byId("sidebar").classList.toggle("open"));
    byId("refresh-button").addEventListener("click", () => setView(state.view));
    byId("product-search").addEventListener("input", debounce(() => loadCatalogue(1)));
    byId("product-category").addEventListener("input", debounce(() => loadCatalogue(1)));
    byId("product-vehicle").addEventListener("input", debounce(() => loadCatalogue(1)));
    byId("product-search-clear").addEventListener("click", () => {
      byId("product-search").value = "";
      byId("product-category").value = "";
      byId("product-vehicle").value = "";
      loadCatalogue(1);
    });
    byId("inventory-search").addEventListener("input", debounce(() => loadInventory(1)));
    byId("inventory-table").addEventListener("submit", saveInventory);
    byId("dealer-search").addEventListener("input", debounce(() => loadDealers(1)));
    byId("open-dealer-form").addEventListener("click", () => byId("dealer-form-panel").classList.remove("hidden"));
    byId("close-dealer-form").addEventListener("click", () => byId("dealer-form-panel").classList.add("hidden"));
    byId("dealer-form").addEventListener("submit", createDealer);
    byId("add-order-item").addEventListener("click", () => addOrderLine());
    byId("order-form").addEventListener("submit", submitOrder);
    byId("order-status-filter").addEventListener("change", (event) => {
      state.orderStatus = event.target.value;
      loadOrders(1);
    });
    byId("assistant-dealer").addEventListener("change", (event) => {
      state.assistantDealer = event.target.value;
      if (state.assistantDealer) localStorage.setItem("vikmo.assistantDealer", state.assistantDealer);
      else localStorage.removeItem("vikmo.assistantDealer");
      renderChat();
    });
    byId("chat-form").addEventListener("submit", sendChat);
    byId("chat-messages").addEventListener("click", (event) => {
      const chip = event.target.closest("[data-prompt]");
      if (!chip) return;
      byId("chat-input").value = chip.dataset.prompt;
      byId("chat-input").focus();
    });
    byId("new-chat").addEventListener("click", () => {
      if (!state.assistantDealer) return;
      localStorage.setItem(`vikmo.session.${state.assistantDealer}`, `dealer-${state.assistantDealer}-${crypto.randomUUID()}`);
      renderChat();
      toast("Started a new assistant conversation.");
    });
    byId("drawer-close").addEventListener("click", closeOrder);
    byId("order-drawer-backdrop").addEventListener("click", closeOrder);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.openOrder) closeOrder();
      if (event.key === "Enter" && event.target.matches?.("tr[data-order]")) openOrder(event.target.dataset.order);
    });
    byId("drawer-body").addEventListener("click", (event) => {
      const button = event.target.closest("[data-action]");
      if (button && !button.disabled) runOrderAction(button);
    });
    document.querySelector(".page-content").addEventListener("click", (event) => {
      const row = event.target.closest("[data-order]");
      if (row) openOrder(row.dataset.order);
    });
    byId("forecast-sku").addEventListener("change", (event) => loadForecastDetail(event.target.value));
    document.body.addEventListener("click", (event) => {
      const retry = event.target.closest("[data-retry]");
      if (retry) setView(retry.dataset.retry);
    });
  }

  async function boot() {
    byId("today-date").textContent = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium" }).format(new Date());
    bindEvents();
    try {
      const [products, dealers] = await Promise.all([loadProductsCache(), loadDealersCache()]);
      if (!products.length || !dealers.length) showNotice("Catalogue or dealer data is empty. Run the seed_data command to load the provided records.");
    } catch (error) {
      showNotice(error.message);
    }
    await Promise.all([loadOrderDealerOptions(), loadAssistantDealerOptions()]);
    await startOrderForm();
    await loadDashboard();
  }

  boot();
})();

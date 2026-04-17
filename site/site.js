const byId = (id) => document.getElementById(id);

const els = {
  year: byId("year"),
  form: byId("checkout-form"),
  planSelect: byId("plan-select"),
  providerSelect: byId("provider-select"),
  contactInput: byId("contact-input"),
  renewSubscriptionInput: byId("renew-subscription-input"),
  checkoutError: byId("checkout-error"),
  orderId: byId("order-id"),
  orderStatus: byId("order-status"),
  payLink: byId("pay-link"),
  checkBtn: byId("check-btn"),
  orderMessage: byId("order-message"),
  tgBindBox: byId("tg-bind-box"),
  tgBindLink: byId("tg-bind-link"),
  deliveryBox: byId("delivery-box"),
  subUrl: byId("sub-url"),
  copySubUrl: byId("copy-sub-url"),
  copyMsg: byId("copy-msg"),
  navBurger: byId("nav-burger"),
  mainNav: document.querySelector(".main-nav"),
};

const state = {
  orderId: "",
  planKeys: [],
};

const PLAN_KEY_HINTS = {
  starter: ["starter", "start", "m1"],
  optimal: ["optimal", "optimum", "m3"],
  annual: ["annual", "yearly", "y1"],
};

if (els.year) {
  els.year.textContent = String(new Date().getFullYear());
}

function setText(el, text) {
  if (!el) return;
  el.textContent = text;
}

function setError(text) {
  setText(els.checkoutError, text || "");
}

function setOrderMessage(text, isError = false) {
  if (!els.orderMessage) return;
  els.orderMessage.classList.toggle("msg-error", Boolean(isError));
  els.orderMessage.classList.toggle("msg-ok", !isError && Boolean(text));
  els.orderMessage.textContent = text || "";
}

function setBusy(button, busyText) {
  if (!button) return () => {};
  const original = button.textContent;
  button.disabled = true;
  if (busyText) button.textContent = busyText;
  return () => {
    button.disabled = false;
    button.textContent = original;
  };
}

function setCheckoutEnabled(enabled) {
  const submitBtn = els.form?.querySelector("button[type='submit']");
  if (submitBtn) {
    submitBtn.disabled = !enabled;
  }
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(path, {
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      ...options,
    });

    let payload;
    try {
      payload = await response.json();
    } catch {
      payload = { ok: false, error: `HTTP ${response.status}` };
    }

    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    return payload;
  } catch (err) {
    if (err?.name === "AbortError") {
      throw new Error("Запрос превысил время ожидания. Проверьте соединение.");
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

function normalizePlanKey(value) {
  return String(value || "").trim().toLowerCase();
}

function syncPlanCardButtons(plans) {
  const planKeys = plans
    .map((plan) => normalizePlanKey(plan?.key))
    .filter(Boolean);
  state.planKeys = planKeys;

  const buttons = [...document.querySelectorAll(".plan-select-btn")];
  buttons.forEach((btn, index) => {
    const currentKey = normalizePlanKey(btn.dataset.planKey);
    let resolved = "";

    if (planKeys.includes(currentKey)) {
      resolved = currentKey;
    }

    if (!resolved && PLAN_KEY_HINTS[currentKey]) {
      resolved = PLAN_KEY_HINTS[currentKey].find((hint) => planKeys.includes(hint)) || "";
    }

    if (!resolved) {
      resolved = planKeys[index] || planKeys[0] || currentKey;
    }

    if (resolved) {
      btn.dataset.planKey = resolved;
    }
  });
}

function bindPlanCardButtons() {
  document.querySelectorAll(".plan-select-btn").forEach((btn) => {
    if (btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", () => {
      const key = normalizePlanKey(btn.dataset.planKey);
      if (!els.planSelect || !key) return;
      els.planSelect.value = key;
      setError("");
    });
  });
}

function renderPlans(plans) {
  if (!els.planSelect) return;
  els.planSelect.innerHTML = "";
  for (const plan of plans) {
    const option = document.createElement("option");
    const daysLabel = `${plan.days} дн.`;
    const gbLabel = Number(plan.gb) > 0 ? `${plan.gb} GB` : "безлимит";
    option.value = String(plan.key);
    option.textContent = `${plan.title} — ${plan.rub} ₽ (${daysLabel}, ${gbLabel})`;
    els.planSelect.appendChild(option);
  }
}

function renderProviders(providers) {
  if (!els.providerSelect) return;
  const labelMap = {
    card: "Карта",
    crypto: "Crypto",
  };
  els.providerSelect.innerHTML = "";
  for (const provider of providers) {
    const option = document.createElement("option");
    option.value = provider;
    option.textContent = labelMap[provider] || provider;
    els.providerSelect.appendChild(option);
  }
}

function setTelegramBind(bindUrl = "") {
  if (!els.tgBindBox || !els.tgBindLink) return;
  const cleanUrl = String(bindUrl || "").trim();
  if (!cleanUrl) {
    els.tgBindBox.hidden = true;
    els.tgBindLink.href = "#";
    return;
  }
  els.tgBindLink.href = cleanUrl;
  els.tgBindBox.hidden = false;
}

function setOrderData({ orderId = "", status = "—", paymentUrl = "" }) {
  state.orderId = orderId;
  setText(els.orderId, orderId || "—");
  setText(els.orderStatus, status || "—");
  setTelegramBind("");

  if (els.payLink) {
    if (paymentUrl) {
      els.payLink.hidden = false;
      els.payLink.href = paymentUrl;
    } else {
      els.payLink.hidden = true;
      els.payLink.href = "#";
    }
  }

  if (els.checkBtn) {
    els.checkBtn.disabled = !orderId;
  }
}

function showDelivery(subscriptionUrl) {
  if (!els.deliveryBox || !els.subUrl) return;
  els.deliveryBox.hidden = false;
  els.subUrl.value = subscriptionUrl || "";
}

function closeMobileNav() {
  if (!els.mainNav || !els.navBurger) return;
  els.mainNav.classList.remove("open");
  els.navBurger.setAttribute("aria-expanded", "false");
}

function setupMobileNav() {
  if (!els.navBurger || !els.mainNav) return;
  els.navBurger.addEventListener("click", () => {
    const open = els.mainNav.classList.toggle("open");
    els.navBurger.setAttribute("aria-expanded", String(open));
  });
  els.mainNav.querySelectorAll("a").forEach((anchor) => {
    anchor.addEventListener("click", () => {
      closeMobileNav();
    });
  });
  window.addEventListener("resize", () => {
    if (window.innerWidth > 980) {
      closeMobileNav();
    }
  });
}

async function loadPlansAndProviders() {
  const payload = await api("/api/plans", { method: "GET" });
  const plans = Array.isArray(payload.plans) ? payload.plans : [];
  if (plans.length === 0) {
    if (els.planSelect) {
      els.planSelect.innerHTML = "";
      els.planSelect.disabled = true;
    }
    setCheckoutEnabled(false);
    setError("Тарифы временно недоступны. Попробуйте позже или напишите в поддержку.");
    return;
  }

  renderPlans(plans);
  if (els.planSelect) {
    els.planSelect.disabled = false;
  }
  syncPlanCardButtons(plans);
  bindPlanCardButtons();

  const providers = Array.isArray(payload.providers) ? payload.providers : [];
  renderProviders(providers);

  if (providers.length === 0) {
    if (els.providerSelect) {
      els.providerSelect.disabled = true;
    }
    setCheckoutEnabled(false);
    setError("Сейчас нет доступных способов оплаты");
    return;
  }

  if (els.providerSelect) {
    els.providerSelect.disabled = false;
  }
  setCheckoutEnabled(true);
}

async function createCheckoutOrder(event) {
  event.preventDefault();
  setError("");
  setOrderMessage("");

  const planKey = els.planSelect?.value || "";
  const provider = els.providerSelect?.value || "";
  const contact = (els.contactInput?.value || "").trim();
  const renewSubscriptionUrl = (els.renewSubscriptionInput?.value || "").trim();

  if (!planKey || !provider) {
    setError("Выберите тариф и способ оплаты");
    return;
  }

  const release = setBusy(event.submitter || els.form?.querySelector("button[type='submit']"), "Создаем заказ...");
  try {
    const payload = await api("/api/checkout", {
      method: "POST",
      body: JSON.stringify({
        plan_key: planKey,
        provider,
        contact,
        renew_subscription_url: renewSubscriptionUrl,
      }),
    });

    setOrderData({
      orderId: payload.order_id,
      status: payload.status || "pending",
      paymentUrl: payload.payment_url || "",
    });
    if (payload.renewal) {
      setOrderMessage("Заказ на продление создан. Откройте оплату, затем нажмите «Проверить оплату».", false);
    } else {
      setOrderMessage("Заказ создан. Откройте оплату, затем нажмите «Проверить оплату».", false);
    }

    const url = new URL(window.location.href);
    url.searchParams.set("order", payload.order_id);
    window.history.replaceState({}, "", url.toString());
  } catch (err) {
    const message = String(err?.message || "");
    if (message.includes("404")) {
      setError("Ошибка API (404). Проверьте Caddy: /api/* должен проксироваться на 127.0.0.1:8011.");
    } else {
      setError(message || "Не удалось создать заказ");
    }
  } finally {
    release();
  }
}

async function checkOrderStatus() {
  if (!state.orderId) {
    setOrderMessage("Сначала создайте заказ.", true);
    return;
  }

  setOrderMessage("Проверяем оплату...");

  try {
    const payload = await api(`/api/order/${encodeURIComponent(state.orderId)}`, { method: "GET" });
    const status = payload.status || "unknown";
    setOrderData({
      orderId: payload.order_id || state.orderId,
      status,
      paymentUrl: payload.payment_url || "",
    });

    if (status === "paid_applied") {
      const subUrl = payload.subscription_url || "";
      if (!subUrl) {
        setOrderMessage("Оплата подтверждена, но не удалось получить ссылку подписки. Напишите в поддержку.", true);
        return;
      }
      showDelivery(subUrl);
      setTelegramBind(payload.tg_bind_url || "");
      if (payload.renewal) {
        setOrderMessage("Оплата подтверждена. Доступ продлен, ссылка подписки остается рабочей.", false);
      } else {
        setOrderMessage("Оплата подтверждена. Скопируйте ссылку подписки и импортируйте ее в клиент.", false);
      }
      return;
    }

    const humanStatus = {
      pending: "Ожидаем оплату",
      processing: "Платеж обрабатывается",
      canceled: "Платеж отменен",
      expired: "Платеж просрочен",
      failed: "Платеж не прошел",
      succeeded: "Оплата принята, выдача доступа...",
      paid: "Оплата принята, выдача доступа...",
    };
    setOrderMessage(humanStatus[status] || `Текущий статус: ${status}`);
  } catch (err) {
    setOrderMessage(String(err?.message || "Ошибка проверки статуса"), true);
  }
}

async function copySubscriptionUrl() {
  const value = (els.subUrl?.value || "").trim();
  if (!value) {
    setText(els.copyMsg, "Сначала получите ссылку подписки");
    return;
  }

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
    } else {
      const helper = document.createElement("textarea");
      helper.value = value;
      helper.setAttribute("readonly", "");
      helper.style.position = "fixed";
      helper.style.opacity = "0";
      document.body.appendChild(helper);
      helper.focus();
      helper.select();
      const copied = document.execCommand("copy");
      document.body.removeChild(helper);
      if (!copied) {
        throw new Error("copy_failed");
      }
    }
    setText(els.copyMsg, "Скопировано");
  } catch {
    if (els.subUrl) {
      els.subUrl.focus();
      els.subUrl.select();
    }
    setText(els.copyMsg, "Скопируйте вручную через Ctrl+C");
  }
}

async function init() {
  setupMobileNav();
  bindPlanCardButtons();

  try {
    await loadPlansAndProviders();
  } catch (err) {
    setCheckoutEnabled(false);
    setError(String(err?.message || "Не удалось загрузить тарифы"));
  }

  if (els.form) {
    els.form.addEventListener("submit", createCheckoutOrder);
  }
  if (els.checkBtn) {
    els.checkBtn.addEventListener("click", checkOrderStatus);
  }
  if (els.copySubUrl) {
    els.copySubUrl.addEventListener("click", copySubscriptionUrl);
  }

  const params = new URLSearchParams(window.location.search);
  const orderFromUrl = (params.get("order") || "").trim();
  if (orderFromUrl) {
    setOrderData({ orderId: orderFromUrl, status: "pending" });
    await checkOrderStatus();
  }
}

void init();

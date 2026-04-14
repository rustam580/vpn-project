const byId = (id) => document.getElementById(id);

const els = {
  year: byId("year"),
  form: byId("checkout-form"),
  planSelect: byId("plan-select"),
  providerSelect: byId("provider-select"),
  contactInput: byId("contact-input"),
  checkoutError: byId("checkout-error"),
  orderId: byId("order-id"),
  orderStatus: byId("order-status"),
  payLink: byId("pay-link"),
  checkBtn: byId("check-btn"),
  orderMessage: byId("order-message"),
  deliveryBox: byId("delivery-box"),
  subUrl: byId("sub-url"),
  copySubUrl: byId("copy-sub-url"),
  copyMsg: byId("copy-msg"),
};

const state = {
  orderId: "",
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

async function api(path, options = {}) {
  const response = await fetch(path, {
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

function setOrderData({ orderId = "", status = "—", paymentUrl = "" }) {
  state.orderId = orderId;
  setText(els.orderId, orderId || "—");
  setText(els.orderStatus, status || "—");

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

function showDelivery(subscriptionUrl, directLinks = []) {
  if (!els.deliveryBox || !els.subUrl) return;
  els.deliveryBox.hidden = false;
  els.subUrl.value = subscriptionUrl || "";

  if (directLinks.length > 0) {
    const directInfo = `\n\nРезервные прямые ссылки:\n${directLinks.join("\n")}`;
    els.subUrl.value += directInfo;
  }
}

async function loadPlansAndProviders() {
  const payload = await api("/api/plans", { method: "GET" });
  renderPlans(payload.plans || []);
  renderProviders(payload.providers || []);

  if (!payload.providers || payload.providers.length === 0) {
    throw new Error("Сейчас нет доступных способов оплаты");
  }
}

async function createCheckoutOrder(event) {
  event.preventDefault();
  setError("");
  setOrderMessage("");

  const planKey = els.planSelect?.value || "";
  const provider = els.providerSelect?.value || "";
  const contact = (els.contactInput?.value || "").trim();

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
      }),
    });

    setOrderData({
      orderId: payload.order_id,
      status: payload.status || "pending",
      paymentUrl: payload.payment_url || "",
    });
    setOrderMessage("Заказ создан. Откройте оплату, затем нажмите «Проверить оплату».", false);

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
      showDelivery(subUrl, payload.direct_links || []);
      const bindHint = payload.tg_bind_url
        ? `\n\n????????? ????? ? Telegram: ${payload.tg_bind_url}`
        : "";
      setOrderMessage(`?????? ????????????. ?????????? ?????? ???????? ? ???????????? ?? ? ??????.${bindHint}`, false);
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
    setOrderMessage(err.message || "Ошибка проверки статуса", true);
  }
}

async function copySubscriptionUrl() {
  const value = (els.subUrl?.value || "").trim();
  if (!value) {
    setText(els.copyMsg, "Сначала получите ссылку подписки");
    return;
  }

  try {
    await navigator.clipboard.writeText(value);
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
  try {
    await loadPlansAndProviders();
  } catch (err) {
    setError(err.message || "Не удалось загрузить тарифы");
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

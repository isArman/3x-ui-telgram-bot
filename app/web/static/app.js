/* Telegram Mini App — VPN subscription shop. */
(function () {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) { tg.ready(); tg.expand(); tg.setHeaderColor("#0f1216"); tg.setBackgroundColor("#0f1216"); }

  const state = {
    me: null,
    tab: "home",
    plans: [],
    pricing: { per_day: 0, per_gb: 0 },
    orders: [],
    wallet: null,
    referral: null,
    admin: { dashboard: null, pending: [], plans: [], promos: [], users: [] },
    quote: null,
    adminTab: "dashboard",
  };

  const $ = (sel, root) => (root || document).querySelector(sel);
  const view = $("#view");

  /* ------------------------------------------------------------------ api */
  async function api(path, options) {
    const opts = options || {};
    const headers = Object.assign({ "X-Telegram-Init-Data": tg ? tg.initData : "" }, opts.headers || {});
    let body = opts.body;
    if (body && !(body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }
    const res = await fetch(path, { method: opts.method || (body ? "POST" : "GET"), headers, body });
    let data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    if (!res.ok || data.ok === false) throw new Error(data.error || "خطا در ارتباط با سرور");
    return data;
  }

  function toast(message, kind) {
    const el = $("#toast");
    el.textContent = message;
    el.className = "toast" + (kind ? " " + kind : "");
    el.hidden = false;
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.hidden = true; }, 3200);
  }

  function haptic(kind) { if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred(kind || "light"); }

  const money = (n) => Number(n || 0).toLocaleString("fa-IR");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));

  /* --------------------------------------------------------------- sheets */
  function openSheet(title, html) {
    $("#sheet-title").textContent = title;
    $("#sheet-body").innerHTML = html;
    $("#sheet").hidden = false;
    $("#sheet-backdrop").hidden = false;
  }
  function closeSheet() {
    $("#sheet").hidden = true;
    $("#sheet-backdrop").hidden = true;
  }
  $("#sheet-close").addEventListener("click", closeSheet);
  $("#sheet-backdrop").addEventListener("click", closeSheet);

  /* -------------------------------------------------------------- routing */
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  async function switchTab(tab) {
    state.tab = tab;
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    await render();
    if (tg) tg.BackButton.hide();
  }

  async function render() {
    view.innerHTML = '<div class="loader"><div class="spinner"></div></div>';
    try {
      if (state.tab === "home") return renderHome();
      if (state.tab === "plans") return renderPlans();
      if (state.tab === "wallet") return renderWallet();
      if (state.tab === "orders") return renderOrders();
      if (state.tab === "referral") return renderReferral();
      if (state.tab === "admin") return renderAdmin();
    } catch (err) {
      view.innerHTML = '<div class="empty">' + esc(err.message) + "</div>";
    }
  }

  /* ----------------------------------------------------------------- home */
  async function renderHome() {
    const me = state.me || (state.me = await api("/api/me"));
    $("#brand-sub").textContent = me.user.first_name ? "خوش آمدید، " + me.user.first_name : "خوش آمدید";
    $("#balance-value").textContent = money(me.balance);
    document.querySelector('[data-tab="admin"]').hidden = !me.is_admin;

    const acc = me.account;
    let html = "";

    if (me.maintenance) {
      html += '<div class="banner danger">فروش موقتاً متوقف است. کمی بعد دوباره تلاش کنید.</div>';
    }

    if (acc) {
      const usedPct = acc.traffic_gb ? Math.min(100, Math.round((acc.used_gb / acc.traffic_gb) * 100)) : 0;
      html += '<div class="card"><h2>اشتراک فعال</h2>' +
        '<div class="row"><span class="k">سفارش</span><span class="v">#' + acc.order_id + "</span></div>" +
        '<div class="row"><span class="k">انقضا</span><span class="v">' + esc(acc.expires_at) + " (" + acc.days_left + " روز)</span></div>" +
        '<div class="row"><span class="k">حجم</span><span class="v">' + acc.used_gb + " از " + acc.traffic_gb + " GB</span></div>" +
        '<div class="row row-total"><span class="k">باقی‌مانده</span><span class="v">' +
        (acc.traffic_gb - acc.used_gb).toFixed(2) + " GB</span></div>" +
        '<div class="btns" style="margin-top:12px">' +
        '<button class="btn ghost" data-act="qr">📱 کد QR</button>' +
        '<button class="btn" data-act="buy">🛒 تمدید / خرید</button>' +
        "</div></div>";
    } else {
      html += '<div class="card center"><h2>هنوز اشتراکی ندارید</h2>' +
        '<p class="muted small">یک پلن انتخاب کنید تا همین حالا فعال شود.</p>' +
        '<button class="btn" data-act="buy">مشاهده پلن‌ها</button></div>';
    }

    if (me.trial.available) {
      html += '<div class="card"><h2>🎁 تست رایگان</h2>' +
        '<p class="muted small">' + me.trial.days + " روز و " + me.trial.traffic + " گیگابایت، رایگان.</p>" +
        '<button class="btn success" data-act="trial">دریافت تست رایگان</button></div>';
    } else if (me.trial.reason) {
      html += '<div class="card small muted">' + esc(me.trial.reason) + "</div>";
    }

    html += '<div class="card"><h2>کیف پول</h2>' +
      '<div class="row"><span class="k">موجودی</span><span class="v">' + money(me.balance) + " تومان</span></div>" +
      '<button class="btn ghost" style="margin-top:10px" data-act="wallet">مدیریت کیف پول</button></div>';

    view.innerHTML = html;
    bindActions();
  }

  function bindActions() {
    view.querySelectorAll("[data-act]").forEach((el) => {
      el.addEventListener("click", () => handleAction(el.dataset.act, el));
    });
  }

  async function handleAction(act, el) {
    haptic();
    if (act === "buy") return switchTab("plans");
    if (act === "wallet") return switchTab("wallet");
    if (act === "qr") return showQr();
    if (act === "trial") return claimTrial(el);
  }

  /* ---------------------------------------------------------------- plans */
  async function renderPlans() {
    if (!state.plans.length) {
      const data = await api("/api/plans");
      state.plans = data.plans || [];
      state.pricing = data.custom_pricing || state.pricing;
    }
    const me = state.me || (state.me = await api("/api/me"));
    let html = "";

    if (me.maintenance) html += '<div class="banner danger">فروش موقتاً متوقف است.</div>';

    html += '<div class="section-title">پلن‌های آماده</div><div class="grid">';
    state.plans.forEach((p) => {
      html += '<div class="plan" data-plan="' + esc(p.id) + '">' +
        '<div class="name">' + esc(p.name) + "</div>" +
        '<div class="meta">' + p.days + " روز · " + p.traffic + " GB · " + (p.devices || 1) + " دستگاه</div>" +
        '<div class="price">' + money(p.price) + " <small>تومان</small></div>" +
        (p.description ? '<div class="desc">' + esc(p.description) + "</div>" : "") +
        "</div>";
    });
    if (!state.plans.length) html += '<div class="empty">پلنی ثبت نشده است.</div>';
    html += "</div>";

    html += '<div class="section-title">پلن سفارشی</div>' +
      '<div class="card"><div class="inline-2">' +
      '<label class="field"><span class="lbl">مدت (روز)</span><input id="c-days" type="number" min="1" max="365" value="30"></label>' +
      '<label class="field"><span class="lbl">حجم (GB)</span><input id="c-traffic" type="number" min="1" max="500" value="50"></label>' +
      "</div>" +
      '<p class="muted small">هر روز ' + money(state.pricing.per_day) + " تومان، هر گیگابایت " + money(state.pricing.per_gb) + " تومان</p>" +
      '<button class="btn" data-act="custom">محاسبه و خرید</button></div>';

    html += '<div class="card"><h2>کد پرومو</h2>' +
      '<div style="display:flex;gap:8px"><input id="promo-input" placeholder="کد پرومو (اختیاری)">' +
      '<button class="btn ghost sm" data-act="apply-promo">اعمال</button></div>' +
      '<p class="muted small" id="promo-msg"></p></div>';

    view.innerHTML = html;
    bindActions();
    view.querySelectorAll("[data-plan]").forEach((el) => {
      el.addEventListener("click", () => { haptic(); openCheckout({ plan_id: el.dataset.plan }); });
    });
  }

  function promoValue() { const el = $("#promo-input"); return el ? el.value.trim() : ""; }

  async function openCheckout(payload) {
    const body = Object.assign({}, payload);
    const code = promoValue();
    if (code) body.promo_code = code;
    const quote = await api("/api/quote", { body });
    state.quote = quote;

    if (quote.promo_error) toast(quote.promo_error, "err");

    let rows = "";
    rows += row("پلن", quote.name);
    rows += row("مدت", quote.days + " روز");
    rows += row("حجم", quote.traffic + " گیگابایت");
    rows += row("دستگاه", quote.devices + " عدد");
    if (quote.promo_code) rows += row("کد پرومو", quote.promo_code + " (−" + money(quote.promo_discount) + ")");
    if (quote.promo_bonus_days) rows += row("روز اضافه", quote.promo_bonus_days);
    if (quote.promo_bonus_gb) rows += row("حجم اضافه", quote.promo_bonus_gb + " GB");
    if (quote.referral_discount) rows += row("تخفیف معرفی", "اعمال شد");
    rows += row("قابل پرداخت", money(quote.total) + " تومان", true);

    const html = '<div class="card"><div class="row"><span class="k">خلاصه سفارش</span></div>' + rows + "</div>" +
      '<div class="section-title">روش پرداخت</div>' +
      '<div class="btns" style="flex-direction:column">' +
      '<button class="btn success" data-pay="wallet">👛 پرداخت از کیف پول</button>' +
      '<button class="btn" data-pay="stars">⭐ پرداخت با تلگرام استارز</button>' +
      '<button class="btn ghost" data-pay="card">🏦 کارت به کارت</button>' +
      "</div>";

    openSheet("تایید سفارش", html);
    $("#sheet-body").querySelectorAll("[data-pay]").forEach((btn) => {
      btn.addEventListener("click", () => choosePayment(btn.dataset.pay, quote));
    });
  }

  function row(k, v, total) {
    return '<div class="row' + (total ? " row-total" : "") + '"><span class="k">' + esc(k) + "</span><span class=\"v\">" + esc(v) + "</span></div>";
  }

  async function choosePayment(method, quote) {
    haptic();
    try {
      if (method === "wallet") {
        const res = await api("/api/orders", { body: quoteRequest(quote) });
        const paid = await api("/api/orders/" + res.order.id + "/pay/wallet");
        closeSheet();
        toast("پرداخت انجام شد.", "ok");
        state.me = null;
        await switchTab("home");
        return;
      }
      if (method === "stars") {
        const res = await api("/api/orders", { body: quoteRequest(quote) });
        const info = await api("/api/orders/" + res.order.id + "/pay/stars");
        closeSheet();
        toast("فاکتور " + info.stars + " ⭐ ارسال شد.");
        return;
      }
      const res = await api("/api/orders", { body: quoteRequest(quote) });
      const card = await api("/api/orders/" + res.order.id + "/pay/card");
      openCardSheet(res.order, card);
    } catch (err) {
      toast(err.message, "err");
    }
  }

  function quoteRequest(quote) {
    const body = {};
    if (quote.plan_id) body.plan_id = quote.plan_id;
    else { body.days = quote.days; body.traffic = quote.traffic; }
    if (quote.promo_code) body.promo_code = quote.promo_code;
    return body;
  }

  function openCardSheet(order, card) {
    const html = '<div class="card"><div class="row"><span class="k">شماره کارت</span><span class="v mono">' + esc(card.card_number) + "</span></div>" +
      '<div class="row"><span class="k">به نام</span><span class="v">' + esc(card.card_holder) + "</span></div>" +
      '<div class="row row-total"><span class="k">مبلغ</span><span class="v">' + money(card.amount) + " تومان</span></div></div>" +
      '<div class="section-title">ارسال رسید</div>' +
      '<input type="file" id="receipt-file" accept="image/*">' +
      '<button class="btn" id="send-receipt" style="margin-top:10px">ارسال رسید</button>' +
      '<p class="muted small">رسید برای ادمین ارسال می‌شود و پس از تایید، اکانت شما فعال می‌شود.</p>';

    openSheet("پرداخت کارت به کارت", html);
    $("#send-receipt").addEventListener("click", async () => {
      const file = $("#receipt-file").files[0];
      if (!file) { toast("ابتدا عکس رسید را انتخاب کنید.", "err"); return; }
      const form = new FormData();
      form.append("photo", file);
      try {
        await api("/api/orders/" + order.id + "/receipt", { body: form });
        closeSheet();
        toast("رسید ارسال شد.", "ok");
        state.me = null;
      } catch (err) { toast(err.message, "err"); }
    });
  }

  /* --------------------------------------------------------------- wallet */
  async function renderWallet() {
    const data = await api("/api/wallet");
    state.wallet = data;
    let html = '<div class="card"><h2>موجودی کیف پول</h2>' +
      '<div class="row row-total"><span class="k">موجودی</span><span class="v">' + money(data.balance) + " تومان</span></div></div>";

    html += '<div class="card"><h2>شارژ کیف پول</h2>' +
      '<label class="field"><span class="lbl">مبلغ (تومان)</span><input id="topup-amount" type="number" min="1" placeholder="مثال: 200000"></label>' +
      (data.card_number ? '<div class="row"><span class="k">شماره کارت</span><span class="v mono">' + esc(data.card_number) + "</span></div>" +
        '<div class="row"><span class="k">به نام</span><span class="v">' + esc(data.card_holder) + "</span></div>" : "") +
      '<button class="btn" data-act="topup" style="margin-top:10px">ثبت درخواست شارژ</button>' +
      '<p class="muted small">پس از واریز، عکس رسید را برای ادمین بفرستید یا درخواست را ثبت کنید.</p></div>';

    if (data.topups.length) {
      html += '<div class="section-title">درخواست‌های اخیر</div><div class="card">';
      data.topups.forEach((t) => {
        const chip = t.status === "approved" ? '<span class="chip ok">تایید</span>'
          : t.status === "rejected" ? '<span class="chip bad">رد</span>'
            : '<span class="chip warn">در انتظار</span>';
        html += '<div class="list-item"><div><div class="t">' + money(t.amount) + " تومان</div>" +
          '<div class="s">' + esc(t.created_at) + "</div></div><div>" + chip + "</div></div>";
      });
      html += "</div>";
    }
    view.innerHTML = html;
    bindActions();
  }

  async function claimTrial(el) {
    el.disabled = true;
    try {
      await api("/api/trial", { body: {} });
      toast("تست رایگان فعال شد.", "ok");
      state.me = null;
      await render();
    } catch (err) { toast(err.message, "err"); el.disabled = false; }
  }

  /* --------------------------------------------------------------- orders */
  async function renderOrders() {
    const data = await api("/api/orders");
    state.orders = data.orders || [];
    if (!state.orders.length) { view.innerHTML = '<div class="empty">سفارشی ثبت نشده است.</div>'; return; }
    let html = '<div class="card">';
    state.orders.forEach((o) => {
      const chip = o.status === "completed" ? '<span class="chip ok">تکمیل</span>'
        : o.status === "rejected" ? '<span class="chip bad">رد</span>'
          : '<span class="chip warn">' + (o.status === "paid" ? "در انتظار تایید" : "در انتظار پرداخت") + "</span>";
      html += '<div class="list-item"><div><div class="t">#' + o.id + " · " + money(o.price) + " تومان</div>" +
        '<div class="s">' + o.days + " روز · " + o.traffic_gb + " GB · " + esc(o.created_at) + "</div></div><div>" + chip + "</div></div>";
    });
    view.innerHTML = html + "</div>";
  }

  /* -------------------------------------------------------------- referral */
  async function renderReferral() {
    const data = await api("/api/referral");
    state.referral = data;
    const html = '<div class="card center"><h2>کد دعوت شما</h2>' +
      '<div class="mono" style="font-size:19px;font-weight:700;margin:10px 0">' + esc(data.code) + "</div>" +
      '<div class="muted small mono">' + esc(data.link) + "</div>" +
      '<div class="btns" style="margin-top:12px">' +
      '<button class="btn" data-act="share">ارسال به دوست</button>' +
      '<button class="btn ghost" data-act="copy">کپی لینک</button></div></div>' +
      '<div class="card"><h2>آمار</h2>' +
      '<div class="row"><span class="k">دعوت‌های موفق</span><span class="v">' + data.referrals + "</span></div>" +
      '<div class="row"><span class="k">کیف پول معرف</span><span class="v">' + money(data.referral_balance) + " تومان</span></div>" +
      '<div class="row"><span class="k">تخفیف ۱۵٪</span><span class="v">' + (data.discount_available ? "فعال" : data.discount_used ? "استفاده شده" : "ناموجود") + "</span></div></div>";
    view.innerHTML = html;
    bindActions();
  }

  async function showQr() {
    try {
      const res = await fetch("/api/qr.png", { headers: { "X-Telegram-Init-Data": tg ? tg.initData : "" } });
      if (!res.ok) throw new Error("کد QR در دسترس نیست.");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      openSheet("کد QR اشتراک", '<img class="qr" src="' + url + '" alt="QR">');
    } catch (err) { toast(err.message, "err"); }
  }

  /* ---------------------------------------------------------------- admin */
  async function renderAdmin() {
    const tab = state.adminTab;
    let html = '<div class="section-title" style="overflow-x:auto;white-space:nowrap">' +
      [["dashboard", "داشبورد"], ["pending", "در انتظار"], ["plans", "پلن‌ها"], ["promos", "پرومو"], ["users", "کاربران"], ["inventory", "انبار"], ["broadcast", "پیام"], ["settings", "تنظیمات"]]
        .map(([k, label]) => '<button class="chip ' + (k === tab ? "ok" : "") + '" data-atab="' + k + '">' + label + "</button>").join("") +
      "</div>";

    if (tab === "dashboard") html += await adminDashboard();
    else if (tab === "pending") html += await adminPending();
    else if (tab === "plans") html += await adminPlans();
    else if (tab === "promos") html += await adminPromos();
    else if (tab === "users") html += await adminUsers();
    else if (tab === "inventory") html += await adminInventory();
    else if (tab === "broadcast") html += adminBroadcast();
    else if (tab === "settings") html += await adminSettings();

    view.innerHTML = html;
    view.querySelectorAll("[data-atab]").forEach((el) => el.addEventListener("click", () => {
      state.adminTab = el.dataset.atab; render();
    }));
    bindAdmin();
  }

  async function adminDashboard() {
    const data = await api("/api/admin/dashboard");
    const s = data.stats;
    return '<div class="card"><h2>داشبورد</h2><div class="grid">' +
      stat("کاربران", s.total_users) + stat("سفارش‌ها", s.total_orders) +
      stat("درآمد کل", money(s.total_revenue) + " ت") + stat("اشتراک فعال", s.active_accounts) +
      stat("پرداخت در انتظار", s.pending_payments) + stat("نزدیک به انقضا", s.expiring_soon) +
      "</div></div>";
  }
  function stat(k, v) { return '<div class="stat"><div class="k">' + esc(k) + '</div><div class="v">' + esc(v) + "</div></div>"; }

  async function adminPending() {
    const data = await api("/api/admin/pending");
    if (!data.items.length) return '<div class="empty">موردی در انتظار نیست.</div>';
    let html = '<div class="card">';
    data.items.forEach((it) => {
      const title = it.kind === "topup" ? "شارژ کیف پول" : "سفارش #" + it.order_id;
      const sub = it.kind === "topup" ? "کاربر " + it.user_id : "کاربر " + it.user_id + " · " + money(it.amount) + " تومان";
      const actions = it.kind === "topup"
        ? '<button class="btn success sm" data-appr="topup:' + it.id + '">تایید</button><button class="btn danger sm" data-rej="topup:' + it.id + '">رد</button>'
        : '<button class="btn success sm" data-appr="order:' + it.id + '">تایید</button><button class="btn danger sm" data-rej="order:' + it.id + '">رد</button>';
      html += '<div class="list-item"><div><div class="t">' + esc(title) + "</div><div class=\"s\">" + esc(sub) + "</div></div><div class=\"btns\">" + actions + "</div></div>";
    });
    return html + "</div>";
  }

  async function adminPlans() {
    const data = await api("/api/admin/plans");
    let html = '<div class="card"><h3>افزودن پلن</h3>' +
      '<div class="inline-2"><input id="np-id" placeholder="شناسه (لاتین)"><input id="np-name" placeholder="نام"></div>' +
      '<div class="inline-2" style="margin-top:8px"><input id="np-days" type="number" placeholder="روز" value="30"><input id="np-traffic" type="number" placeholder="GB" value="50"></div>' +
      '<div class="inline-2" style="margin-top:8px"><input id="np-price" type="number" placeholder="قیمت"><input id="np-devices" type="number" placeholder="دستگاه" value="1"></div>' +
      '<input id="np-desc" placeholder="توضیحات" style="margin-top:8px">' +
      '<button class="btn sm" data-act="add-plan" style="margin-top:10px">افزودن</button></div>';

    html += '<div class="card"><h3>پلن‌ها</h3>';
    data.plans.forEach((p) => {
      html += '<div class="list-item"><div><div class="t">' + esc(p.name) + "</div>" +
        '<div class="s">' + p.days + " روز · " + p.traffic_gb + " GB · " + p.devices + " دستگاه · " + money(p.price) + " ت</div></div>" +
        '<div class="btns"><button class="btn ghost sm" data-toggle-plan="' + esc(p.id) + '">' + (p.is_active ? "غیرفعال" : "فعال") + "</button>" +
        '<button class="btn danger sm" data-del-plan="' + esc(p.id) + '">حذف</button></div></div>';
    });
    return html + "</div>";
  }

  async function adminPromos() {
    const data = await api("/api/admin/promos");
    let html = '<div class="card"><h3>کد جدید</h3>' +
      '<input id="np-code" placeholder="کد (مثال: SUMMER)">' +
      '<div class="inline-2" style="margin-top:8px"><select id="np-kind"><option value="days">روز اضافه</option><option value="gb">گیگابایت اضافه</option><option value="percent">درصد تخفیف</option></select><input id="np-value" type="number" placeholder="مقدار"></div>' +
      '<input id="np-max" type="number" placeholder="حداکثر استفاده (۰ = نامحدود)" style="margin-top:8px">' +
      '<button class="btn sm" data-act="add-promo" style="margin-top:10px">ساخت</button></div>';

    html += '<div class="card"><h3>کدها</h3>';
    if (!data.promos.length) html += '<div class="empty">کدی وجود ندارد.</div>';
    data.promos.forEach((p) => {
      const used = p.used_count + (p.max_uses ? " / " + p.max_uses : "");
      html += '<div class="list-item"><div><div class="t mono">' + esc(p.code) + "</div>" +
        '<div class="s">' + esc(p.kind) + " · " + p.value + " · استفاده: " + used + "</div></div>" +
        '<div class="btns"><button class="btn ghost sm" data-toggle-promo="' + p.id + '">' + (p.is_active ? "خاموش" : "فعال") + "</button>" +
        '<button class="btn danger sm" data-del-promo="' + p.id + '">حذف</button></div></div>';
    });
    return html + "</div>";
  }

  async function adminUsers() {
    const q = state.userQuery || "";
    const data = await api("/api/admin/users" + (q ? "?q=" + encodeURIComponent(q) : ""));
    let html = '<div class="card"><div style="display:flex;gap:8px">' +
      '<input id="user-q" placeholder="جستجو با شناسه یا یوزرنیم" value="' + esc(q) + '">' +
      '<button class="btn ghost sm" data-act="user-search">جستجو</button></div></div>';
    html += '<div class="card">';
    if (!data.users.length) html += '<div class="empty">کاربری پیدا نشد.</div>';
    data.users.forEach((u) => {
      html += '<div class="list-item"><div><div class="t">' + esc(u.name) + "</div>" +
        '<div class="s">' + money(u.balance) + " ت · " + u.orders + " سفارش</div></div>" +
        '<button class="btn ghost sm" data-user="' + u.id + '">مدیریت</button></div>';
    });
    return html + "</div>";
  }

  async function adminInventory() {
    const data = await api("/api/admin/inventory");
    let html = '<div class="card"><h3>افزودن کانفیگ</h3>' +
      '<input id="inv-plan" placeholder="شناسه پلن"><input id="inv-text" placeholder="لینک subscription / vless://" style="margin-top:8px">' +
      '<button class="btn sm" data-act="add-inv" style="margin-top:10px">افزودن</button></div>';
    html += '<div class="card"><h3>انبار</h3>';
    if (!data.entries.length) html += '<div class="empty">کانفیگی ثبت نشده است.</div>';
    data.entries.slice(0, 40).forEach((e) => {
      html += '<div class="list-item"><div><div class="t">' + esc(e.plan_id) + " #" + e.id + "</div>" +
        '<div class="s">' + (e.is_assigned ? "تخصیص‌یافته به سفارش " + e.order_id : "آزاد") + "</div></div></div>";
    });
    return html + "</div>";
  }

  function adminBroadcast() {
    return '<div class="card"><h3>پیام همگانی</h3>' +
      '<textarea id="bc-text" placeholder="متن پیام"></textarea>' +
      '<label class="check" style="margin-top:8px"><input type="checkbox" id="bc-html"> استفاده از HTML</label>' +
      '<label class="field"><span class="lbl">ارسال فقط به یک کاربر (اختیاری)</span><input id="bc-user" placeholder="شناسه کاربر"></label>' +
      '<button class="btn" data-act="send-broadcast">ارسال</button></div>';
  }

  async function adminSettings() {
    const data = await api("/api/admin/pending");
    return '<div class="card"><h3>تنظیمات فروشگاه</h3>' +
      '<label class="check"><input type="checkbox" id="set-maint"> حالت نگهداری (فروش متوقف)</label>' +
      '<label class="check"><input type="checkbox" id="set-trial"> تست رایگان فعال</label>' +
      '<div class="inline-2"><input id="set-trial-days" type="number" placeholder="روز تست"><input id="set-trial-gb" type="number" placeholder="GB تست"></div>' +
      '<button class="btn sm" data-act="save-settings" style="margin-top:10px">ذخیره</button></div>' +
      '<div class="card"><h3>پشتیبان‌گیری</h3><button class="btn ghost" data-act="backup">دانلود پشتیبان دیتابیس</button></div>';
  }

  function bindAdmin() {
    view.querySelectorAll("[data-appr]").forEach((el) => el.addEventListener("click", () => approveItem(el.dataset.appr)));
    view.querySelectorAll("[data-rej]").forEach((el) => el.addEventListener("click", () => rejectItem(el.dataset.rej)));
    view.querySelectorAll("[data-toggle-plan]").forEach((el) => el.addEventListener("click", async () => {
      try { await api("/api/admin/plans/" + encodeURIComponent(el.dataset.togglePlan), { body: {} }); toast("ذخیره شد.", "ok"); render(); }
      catch (e) { toast(e.message, "err"); }
    }));
    view.querySelectorAll("[data-del-plan]").forEach((el) => el.addEventListener("click", async () => {
      try { await api("/api/admin/plans/" + encodeURIComponent(el.dataset.delPlan), { method: "DELETE" }); toast("حذف شد.", "ok"); render(); }
      catch (e) { toast(e.message, "err"); }
    }));
    view.querySelectorAll("[data-toggle-promo]").forEach((el) => el.addEventListener("click", async () => {
      try { await api("/api/admin/promos/" + el.dataset.togglePromo + "/toggle", { body: {} }); toast("ذخیره شد.", "ok"); render(); }
      catch (e) { toast(e.message, "err"); }
    }));
    view.querySelectorAll("[data-del-promo]").forEach((el) => el.addEventListener("click", async () => {
      try { await api("/api/admin/promos/" + el.dataset.delPromo, { method: "DELETE" }); toast("حذف شد.", "ok"); render(); }
      catch (e) { toast(e.message, "err"); }
    }));
    view.querySelectorAll("[data-user]").forEach((el) => el.addEventListener("click", () => openUser(el.dataset.user)));

    const acts = {
      "add-plan": async () => {
        try {
          await api("/api/admin/plans", { body: {
            id: $("#np-id").value, name: $("#np-name").value,
            days: $("#np-days").value, traffic_gb: $("#np-traffic").value,
            price: $("#np-price").value, devices: $("#np-devices").value,
            description: $("#np-desc").value,
          } });
          toast("پلن ساخته شد.", "ok"); render();
        } catch (e) { toast(e.message, "err"); }
      },
      "add-promo": async () => {
        try {
          await api("/api/admin/promos", { body: {
            code: $("#np-code").value, kind: $("#np-kind").value,
            value: $("#np-value").value, max_uses: $("#np-max").value,
          } });
          toast("کد ساخته شد.", "ok"); render();
        } catch (e) { toast(e.message, "err"); }
      },
      "user-search": () => { state.userQuery = $("#user-q").value.trim(); render(); },
      "add-inv": async () => {
        try {
          await api("/api/admin/inventory", { body: { plan_id: $("#inv-plan").value, config_text: $("#inv-text").value } });
          toast("کانفیگ اضافه شد.", "ok"); render();
        } catch (e) { toast(e.message, "err"); }
      },
      "send-broadcast": async () => {
        try {
          const res = await api("/api/admin/broadcast", { body: {
            text: $("#bc-text").value, use_html: $("#bc-html").checked,
            user_id: $("#bc-user").value || null,
          } });
          toast(res.summary, "ok"); render();
        } catch (e) { toast(e.message, "err"); }
      },
      "save-settings": async () => {
        try {
          await api("/api/admin/maintenance", { body: { enabled: $("#set-maint").checked } });
          await api("/api/admin/trial", { body: {
            enabled: $("#set-trial").checked,
            days: $("#set-trial-days").value,
            traffic_gb: $("#set-trial-gb").value,
          } });
          toast("ذخیره شد.", "ok"); render();
        } catch (e) { toast(e.message, "err"); }
      },
      "backup": () => { window.open("/api/admin/backup", "_blank"); },
    };
    view.querySelectorAll("[data-act]").forEach((el) => {
      const fn = acts[el.dataset.act];
      if (fn) el.addEventListener("click", fn);
    });
  }

  async function approveItem(token) {
    const [kind, id] = token.split(":");
    try {
      if (kind === "topup") await api("/api/admin/topups/" + id + "/approve", { body: {} });
      else await api("/api/admin/payments/" + id + "/approve", { body: {} });
      toast("تایید شد.", "ok"); render();
    } catch (e) { toast(e.message, "err"); }
  }

  async function rejectItem(token) {
    const [kind, id] = token.split(":");
    try {
      if (kind === "topup") await api("/api/admin/topups/" + id + "/reject", { body: {} });
      else await api("/api/admin/payments/" + id + "/reject", { body: {} });
      toast("رد شد.", "ok"); render();
    } catch (e) { toast(e.message, "err"); }
  }

  async function openUser(userId) {
    try {
      const data = await api("/api/admin/users/" + userId);
      const u = data.user;
      const html = '<div class="card">' +
        row("شناسه", u.id) + row("موجودی", money(u.balance) + " تومان") +
        row("سفارش‌ها", u.total_orders + " (" + u.completed_orders + " تکمیل)") +
        row("مجموع خرید", money(u.total_spent) + " تومان") +
        row("اشتراک فعال", u.active_accounts) + row("دعوت‌ها", u.referrals) +
        row("مسدود", u.blocked ? "بله" : "خیر") + "</div>" +
        '<div class="section-title">اقدام</div><div class="btns" style="flex-direction:column">' +
        '<button class="btn ghost" data-ublock="' + u.id + '">' + (u.blocked ? "رفع مسدودی" : "مسدود کردن") + "</button>" +
        '<button class="btn success" data-ucredit="' + u.id + '">افزودن اعتبار</button>' +
        '<button class="btn" data-urenew="' + u.id + '">تمدید ۳۰ روزه</button></div>';

      openSheet("مدیریت کاربر", html);
      const body = $("#sheet-body");
      const block = body.querySelector("[data-ublock]");
      if (block) block.addEventListener("click", async () => {
        try {
          await api("/api/admin/users/" + u.id + "/block", { body: { blocked: !u.blocked } });
          closeSheet(); toast("انجام شد.", "ok"); render();
        } catch (e) { toast(e.message, "err"); }
      });
      const credit = body.querySelector("[data-ucredit]");
      if (credit) credit.addEventListener("click", () => {
        openSheet("افزودن اعتبار", '<label class="field"><span class="lbl">مبلغ (تومان)</span><input id="credit-amt" type="number"></label>' +
          '<button class="btn" id="credit-go">تایید</button>');
        $("#credit-go").addEventListener("click", async () => {
          try {
            await api("/api/admin/users/" + u.id + "/credit", { body: { amount: $("#credit-amt").value } });
            closeSheet(); toast("اعتبار اضافه شد.", "ok"); render();
          } catch (e) { toast(e.message, "err"); }
        });
      });
      const renew = body.querySelector("[data-urenew]");
      if (renew) renew.addEventListener("click", async () => {
        try {
          const res = await api("/api/admin/users/" + u.id + "/renew", { body: {} });
          closeSheet(); toast("تمدید تا " + res.expires_at, "ok"); render();
        } catch (e) { toast(e.message, "err"); }
      });
    } catch (e) { toast(e.message, "err"); }
  }

  /* ------------------------------------------------------------- boot */
  async function boot() {
    try {
      state.me = await api("/api/me");
      $("#balance-value").textContent = money(state.me.balance);
    } catch (err) {
      view.innerHTML = '<div class="empty">دسترسی نامعتبر است.<br><span class="small">لطفاً Mini App را از داخل تلگرام باز کنید.</span></div>';
      return;
    }
    await switchTab("home");
  }

  document.addEventListener("DOMContentLoaded", boot);
})();

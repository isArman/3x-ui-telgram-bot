/* The Mini App of the VPN shop.
 *
 * One envelope for the whole API: a successful call returns the `data` object,
 * a failed call throws an Error with the Persian message from the server.
 */
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

  var ADMIN_TABS = [
    ["dashboard", "داشبورد"],
    ["pending", "بررسی"],
    ["payments", "پرداخت‌ها"],
    ["plans", "پلن‌ها"],
    ["promos", "پرومو"],
    ["users", "کاربران"],
    ["panel", "پنل"],
    ["inventory", "انبار"],
    ["settings", "تنظیمات"],
    ["broadcast", "پیام"]
  ];

  var state = {
    me: null,
    tab: "home",
    adminTab: "dashboard",
    plans: [],
    pricing: null,
    promoCode: "",
    quote: null,
    order: null,
    busy: false
  };

  // --- tiny helpers ---------------------------------------------------------

  function $(selector, root) {
    return (root || document).querySelector(selector);
  }

  function esc(value) {
    return String(value === undefined || value === null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function money(value) {
    var number = Number(value || 0);
    return number.toLocaleString("en-US");
  }

  function haptic() {
    if (tg && tg.HapticFeedback && tg.HapticFeedback.impactOccurred) {
      tg.HapticFeedback.impactOccurred("light");
    }
  }

  function toast(message, kind) {
    var el = $("#toast");
    el.textContent = message;
    el.className = "toast" + (kind ? " " + kind : "");
    el.hidden = false;
    clearTimeout(el._timer);
    el._timer = setTimeout(function () {
      el.hidden = true;
    }, 3200);
  }

  function api(path, options) {
    var opts = options || {};
    var headers = { "X-Telegram-Init-Data": (tg && tg.initData) || "" };
    var body;
    var method = opts.method;

    if (opts.body !== undefined) {
      if (opts.body instanceof FormData) {
        body = opts.body;
      } else {
        headers["Content-Type"] = "application/json";
        body = JSON.stringify(opts.body);
      }
      method = method || "POST";
    } else {
      method = method || "GET";
    }

    return fetch(path, { method: method, headers: headers, body: body })
      .then(function (res) {
        return res
          .json()
          .catch(function () {
            return null;
          })
          .then(function (payload) {
            if (!res.ok || !payload || payload.ok === false) {
              var err = new Error(
                (payload && payload.error) || "ارتباط با سرور برقرار نشد."
              );
              err.code = payload && payload.code;
              err.status = res.status;
              throw err;
            }
            return payload.data || {};
          });
      })
      .catch(function (err) {
        if (err instanceof Error && err.message) {
          throw err;
        }
        throw new Error("ارتباط با سرور برقرار نشد.");
      });
  }

  function openSheet(title, html) {
    $("#sheet-title").textContent = title;
    $("#sheet-body").innerHTML = html;
    $("#sheet").hidden = false;
    $("#sheet-backdrop").hidden = false;
  }

  function closeSheet() {
    $("#sheet").hidden = true;
    $("#sheet-backdrop").hidden = true;
    $("#sheet-body").innerHTML = "";
  }

  function row(key, value, isTotal) {
    return (
      '<div class="row' +
      (isTotal ? " row-total" : "") +
      '"><span class="k">' +
      esc(key) +
      '</span><span class="v">' +
      esc(value) +
      "</span></div>"
    );
  }

  function chip(text, kind) {
    return '<span class="chip' + (kind ? " " + kind : "") + '">' + esc(text) + "</span>";
  }

  function orderStatusChip(status) {
    var map = {
      pending: ["در انتظار پرداخت", "warn"],
      awaiting_stars: ["در انتظار پرداخت", "warn"],
      paid: ["پرداخت شده", ""],
      completed: ["تکمیل شده", "ok"],
      rejected: ["رد شده", "bad"],
      cancelled: ["لغو شده", ""]
    };
    var found = map[status] || [status, ""];
    return chip(found[0], found[1]);
  }

  // --- routing --------------------------------------------------------------

  function tabFromLocation() {
    var params = new URLSearchParams(window.location.search);
    var tab = params.get("tab");
    var admin = params.get("admin");
    if (tab === "admin") {
      state.tab = "admin";
      if (admin) {
        state.adminTab = admin;
      }
      return;
    }
    if (["home", "plans", "wallet", "orders", "referral"].indexOf(tab) >= 0) {
      state.tab = tab;
    }
  }

  function switchTab(tab) {
    state.tab = tab;
    state.quote = null;
    state.order = null;
    render();
  }

  function render() {
    var view = $("#view");

    if (!state.me) {
      view.innerHTML = '<div class="loader">در حال بارگذاری…</div>';
      return;
    }

    if (state.tab === "admin") {
      if (!state.me.is_admin) {
        switchTab("home");
        return;
      }
      renderAdminBar();
      renderAdmin();
      return;
    }

    renderSubbar();
    if (state.tab === "home") {
      renderHome();
    } else if (state.tab === "plans") {
      renderPlans();
    } else if (state.tab === "wallet") {
      renderWallet();
    } else if (state.tab === "orders") {
      renderOrders();
    } else if (state.tab === "referral") {
      renderReferral();
    }
  }

  function renderSubbar() {
    var bar = $("#subbar");
    bar.hidden = true;
    bar.innerHTML = "";
  }

  function renderAdminBar() {
    var bar = $("#subbar");
    var html = "";
    ADMIN_TABS.forEach(function (entry) {
      html +=
        '<button class="subtab' +
        (state.adminTab === entry[0] ? " active" : "") +
        '" data-admin-tab="' +
        esc(entry[0]) +
        '" type="button">' +
        esc(entry[1]) +
        "</button>";
    });
    bar.innerHTML = html;
    bar.hidden = false;
  }

  function updateHeader() {
    var me = state.me;
    $("#balance-value").textContent = money(me.balance);
    $("#balance-pill").onclick = function () {
      switchTab("wallet");
    };
    var name = me.user.first_name || me.user.username || "مشتری";
    $("#brand-name").textContent = name;
    $("#brand-sub").textContent = me.is_admin ? "مدیر فروشگاه" : "فروشگاه اشتراک";
  }

  // --- customer views -------------------------------------------------------

  function renderHome() {
    var me = state.me;
    var acc = me.account;
    var view = $("#view");
    var html = "";

    if (me.maintenance) {
      html +=
        '<div class="banner">فروش موقتاً متوقف است. لطفاً کمی بعد تلاش کنید.</div>';
    }
    if (me.is_blocked) {
      html +=
        '<div class="banner danger">دسترسی شما مسدود شده است. برای راهنمایی با پشتیبانی تماس بگیرید.</div>';
    }
    if (me.trial && me.trial.available) {
      html +=
        '<div class="card"><div class="row"><span class="k">پلن تست رایگان</span>' +
        chip("قابل دریافت", "ok") +
        "</div>" +
        '<p class="muted small">' +
        esc(me.trial.days) +
        " روز، " +
        esc(me.trial.traffic) +
        " گیگابایت</p>" +
        '<button class="btn" data-action="claim-trial" type="button">دریافت پلن تست</button></div>';
    }

    if (acc) {
      var total = Number(acc.traffic_gb || 0);
      var used = Number(acc.used_gb || 0);
      var pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
      html +=
        '<div class="card"><h2>اشتراک من</h2>' +
        row("شماره سفارش", "#" + acc.order_id) +
        row("انقضا", acc.expires_at) +
        row("روز باقی‌مانده", acc.days_left) +
        row("حجم کل", total + " GB") +
        row("مصرف شده", used + " GB") +
        '<div class="meter"><span style="width:' +
        pct +
        '%"></span></div>' +
        '<div class="btns" style="margin-top:12px">' +
        '<button class="btn ghost" data-action="copy-sub" type="button">کپی لینک</button>' +
        '<button class="btn ghost" data-action="show-qr" type="button">کد QR</button>' +
        "</div>" +
        '<div class="sub mono small" style="margin-top:10px;word-break:break-all">' +
        esc(acc.subscription_url || "") +
        "</div></div>";

      html +=
        '<button class="btn success" data-action="go-plans" type="button">تمدید یا خرید اشتراک</button>';
    } else {
      html +=
        '<div class="card center"><h2>هنوز اشتراکی ندارید</h2>' +
        '<p class="muted small">یک پلن انتخاب کنید تا اشتراک شما فعال شود.</p></div>' +
        '<button class="btn success" data-action="go-plans" type="button">مشاهده پلن‌ها</button>';
    }

    html +=
      '<div class="section-title">راهنما</div>' +
      '<div class="card"><p class="muted small">برای اتصال، لینک اشتراک را در کلاینت مورد نظر خود (روت یا v2rayN) وارد کنید. حجم و زمان اشتراک از همین صفحه دیده می‌شود.</p></div>';

    view.innerHTML = html;
  }

  function renderPlans() {
    var view = $("#view");
    var html = "";

    html +=
      '<div class="card"><label class="field"><span class="lbl">کد پرومو (اختیاری)</span>' +
      '<div class="inline-2"><input id="promo-input" type="text" placeholder="مثلاً WELCOME" value="' +
      esc(state.promoCode) +
      '" autocomplete="off"><button class="btn ghost" data-action="apply-promo" type="button">اعمال</button></div></label>' +
      '<p class="muted small" id="promo-hint"></p></div>';

    if (state.plans.length) {
      html += '<div class="section-title">پلن‌های آماده</div><div class="grid">';
      state.plans.forEach(function (plan) {
        html +=
          '<div class="plan" data-plan="' +
          esc(plan.id) +
          '"><div class="name">' +
          esc(plan.name) +
          '</div><div class="meta">' +
          esc(plan.days) +
          " روز · " +
          esc(plan.traffic) +
          " GB · " +
          esc(plan.devices) +
          " دستگاه</div>" +
          '<div class="price">' +
          money(plan.price) +
          " <small>تومان</small></div>" +
          (plan.description
            ? '<div class="desc">' + esc(plan.description) + "</div>"
            : "") +
          "</div>";
      });
      html += "</div>";
    } else {
      html += '<div class="empty">هنوز پلنی ثبت نشده است.</div>';
    }

    var pricing = state.pricing || { per_day: 0, per_gb: 0 };
    html +=
      '<div class="section-title">پلن سفارشی</div>' +
      '<div class="card"><div class="inline-2">' +
      '<label class="field"><span class="lbl">مدت (روز)</span><input id="custom-days" type="number" min="1" max="365" value="30"></label>' +
      '<label class="field"><span class="lbl">حجم (GB)</span><input id="custom-traffic" type="number" min="1" max="500" value="50"></label>' +
      "</div>" +
      '<p class="muted small">قیمت هر روز ' +
      money(pricing.per_day) +
      " تومان و هر گیگابایت " +
      money(pricing.per_gb) +
      " تومان.</p>" +
      '<button class="btn" data-action="quote-custom" type="button">محاسبه قیمت</button></div>';

    view.innerHTML = html;
  }

  function renderWallet() {
    var view = $("#view");
    var me = state.me;
    var html =
      '<div class="card"><h2>کیف پول</h2>' +
      row("موجودی", money(me.balance) + " تومان", true) +
      "</div>";

    html +=
      '<div class="card"><h3>افزایش موجودی</h3>' +
      '<label class="field"><span class="lbl">مبلغ (تومان)</span><input id="topup-amount" type="number" min="1" step="1000" placeholder="50000"></label>' +
      '<button class="btn" data-action="topup" type="button">درخواست افزایش موجودی</button>' +
      '<p class="muted small">پس از واریز، رسید را برای ادمین بفرستید و موجودی کیف پول دستی اضافه می‌شود.</p>' +
      "</div>";

    html += '<div class="section-title">سوابق شارژ</div><div class="card" id="topup-list"><div class="loader">…</div></div>';
    view.innerHTML = html;

    api("/api/wallet")
      .then(function (data) {
        var list = $("#topup-list");
        if (!list) {
          return;
        }
        if (!data.topups || !data.topups.length) {
          list.innerHTML = '<div class="empty">درخواستی ثبت نشده است.</div>';
          return;
        }
        var rows = "";
        data.topups.forEach(function (topup) {
          rows +=
            '<div class="list-item"><div><div class="t">' +
            money(topup.amount) +
            " تومان</div><div class=\"s\">" +
            esc(topup.created_at) +
            '</div></div><div>' +
            chip(
              topup.status === "approved"
                ? "تایید شده"
                : topup.status === "rejected"
                ? "رد شده"
                : "در انتظار",
              topup.status === "approved" ? "ok" : topup.status === "rejected" ? "bad" : "warn"
            ) +
            "</div></div>";
        });
        list.innerHTML = rows;
      })
      .catch(function () {
        var list = $("#topup-list");
        if (list) {
          list.innerHTML = '<div class="empty">سوابق در دسترس نیست.</div>';
        }
      });
  }

  function renderOrders() {
    var view = $("#view");
    view.innerHTML = '<div class="loader">در حال بارگذاری…</div>';
    api("/api/orders")
      .then(function (data) {
        var orders = data.orders || [];
        if (!orders.length) {
          view.innerHTML = '<div class="empty">هنوز سفارشی ثبت نکرده‌اید.</div>';
          return;
        }
        var html = "";
        orders.forEach(function (order) {
          var canPay = order.status === "pending";
          html +=
            '<div class="card"><div class="row"><span class="k">#' +
            esc(order.id) +
            "</span>" +
            orderStatusChip(order.status) +
            "</div>" +
            row("مدت", order.days + " روز") +
            row("حجم", order.traffic_gb + " GB") +
            row("مبلغ", money(order.price) + " تومان") +
            row("تاریخ", order.created_at) +
            (order.promo_code ? row("کد پرومو", order.promo_code) : "") +
            (canPay
              ? '<button class="btn sm" data-action="pay-order" data-order="' +
                esc(order.id) +
                '" type="button">پرداخت</button>'
              : "") +
            "</div>";
        });
        view.innerHTML = html;
      })
      .catch(function (err) {
        view.innerHTML = '<div class="empty">' + esc(err.message) + "</div>";
      });
  }

  function renderReferral() {
    var view = $("#view");
    view.innerHTML = '<div class="loader">در حال بارگذاری…</div>';
    api("/api/referral")
      .then(function (data) {
        var html =
          '<div class="card"><h2>دعوت دوستان</h2>' +
          '<p class="muted small">این لینک را برای دوستانتان بفرستید. اولین خرید آن‌ها با تخفیف ثبت می‌شود و بخشی از آن به کیف پول شما برمی‌گردد.</p>' +
          '<div class="sub mono" style="margin:10px 0;word-break:break-all">' +
          esc(data.link || "") +
          "</div>" +
          '<button class="btn" data-action="copy-link" data-link="' +
          esc(data.link || "") +
          '" type="button">کپی لینک دعوت</button></div>';

        html +=
          '<div class="grid">' +
          '<div class="stat"><div class="k">دعوت‌ها</div><div class="v">' +
          esc(data.referrals) +
          "</div></div>" +
          '<div class="stat"><div class="k">پاداش دریافتی</div><div class="v">' +
          money(data.referral_balance) +
          "</div></div>" +
          "</div>";

        if (data.discount_available) {
          html +=
            '<div class="banner">برای اولین خرید شما تخفیف معرفی فعال است.</div>';
        } else if (data.discount_used) {
          html +=
            '<div class="banner">تخفیف معرفی شما قبلاً استفاده شده است.</div>';
        }

        if (tg && tg.shareUrl) {
          html +=
            '<button class="btn ghost" data-action="share" data-link="' +
            esc(data.link || "") +
            '" type="button">اشتراک‌گذاری</button>';
        }
        view.innerHTML = html;
      })
      .catch(function (err) {
        view.innerHTML = '<div class="empty">' + esc(err.message) + "</div>";
      });
  }

  // --- checkout -------------------------------------------------------------

  function quoteRequest(quote) {
    var body = {};
    if (quote.plan_id) {
      body.plan_id = quote.plan_id;
    } else {
      body.days = quote.days;
      body.traffic = quote.traffic;
    }
    if (quote.promo_code) {
      body.promo_code = quote.promo_code;
    }
    return body;
  }

  function openCheckout(payload) {
    var body = {};
    Object.keys(payload).forEach(function (key) {
      body[key] = payload[key];
    });
    if (state.promoCode) {
      body.promo_code = state.promoCode;
    }
    state.order = null;
    api("/api/quote", { body: body })
      .then(function (quote) {
        state.quote = quote;
        var rows = "";
        rows += row("پلن", quote.name);
        rows += row("مدت", quote.total_days + " روز");
        rows += row("حجم", quote.total_traffic + " گیگابایت");
        rows += row("دستگاه", quote.devices + " عدد");
        if (quote.promo_bonus_days) {
          rows += row("روز اضافه", quote.promo_bonus_days);
        }
        if (quote.promo_bonus_gb) {
          rows += row("حجم اضافه", quote.promo_bonus_gb + " GB");
        }
        if (quote.promo_discount) {
          rows += row("تخفیف پرومو", "−" + money(quote.promo_discount) + " تومان");
        }
        if (quote.referral_discount) {
          rows += row("تخفیف معرفی", "−" + money(quote.referral_discount_amount) + " تومان");
        }
        rows += row("قابل پرداخت", money(quote.total) + " تومان", true);

        var html =
          '<div class="card">' +
          row("خلاصه سفارش", "") +
          rows +
          "</div>" +
          '<div class="section-title">روش پرداخت</div>' +
          '<div class="btns" style="flex-direction:column">' +
          '<button class="btn success" data-action="pay-wallet" type="button">👛 پرداخت از کیف پول</button>' +
          '<button class="btn" data-action="pay-stars" type="button">⭐ پرداخت با تلگرام استارز</button>' +
          '<button class="btn ghost" data-action="pay-card" type="button">🏦 کارت به کارت</button>' +
          "</div>";
        openSheet("تایید سفارش", html);
      })
      .catch(function (err) {
        toast(err.message, "err");
      });
  }

  function ensureOrder() {
    if (state.order) {
      return Promise.resolve(state.order);
    }
    return api("/api/orders", { body: quoteRequest(state.quote) }).then(function (data) {
      state.order = data.order;
      return state.order;
    });
  }

  function withBusy(button, promise) {
    if (state.busy) {
      return promise;
    }
    state.busy = true;
    if (button) {
      button.disabled = true;
      button.dataset.label = button.textContent;
      button.textContent = "…";
    }
    return promise
      .catch(function (err) {
        toast(err.message, "err");
        throw err;
      })
      .finally(function () {
        state.busy = false;
        if (button) {
          button.disabled = false;
          button.textContent = button.dataset.label || button.textContent;
        }
      });
  }

  function choosePayment(method, button) {
    haptic();
    withBusy(
      button,
      ensureOrder()
        .then(function (order) {
          if (method === "wallet") {
            return api("/api/orders/" + order.id + "/pay/wallet", { body: {} }).then(
              function (data) {
                closeSheet();
                toast("پرداخت انجام شد.", "ok");
                state.me = null;
                state.order = null;
                return refreshMe().then(function () {
                  switchTab("home");
                });
              }
            );
          }
          if (method === "stars") {
            return api("/api/orders/" + order.id + "/pay/stars", { body: {} }).then(
              function (data) {
                closeSheet();
                toast("فاکتور " + data.stars + " ⭐ ارسال شد.");
              }
            );
          }
          return api("/api/orders/" + order.id + "/pay/card", { body: {} }).then(
            function (card) {
              openCardSheet(order, card);
            }
          );
        })
    );
  }

  function openCardSheet(order, card) {
    var html =
      '<div class="card">' +
      row("شماره کارت", card.card_number) +
      row("به نام", card.card_holder) +
      row("مبلغ", money(card.amount) + " تومان", true) +
      "</div>" +
      '<div class="section-title">ارسال رسید</div>' +
      '<input type="file" id="receipt-file" accept="image/*">' +
      '<button class="btn" id="send-receipt" type="button">ارسال رسید</button>' +
      '<p class="muted small">رسید برای ادمین ارسال می‌شود و پس از تایید، اکانت شما فعال می‌شود.</p>';

    openSheet("پرداخت کارت به کارت", html);

    $("#send-receipt").onclick = function (event) {
      var button = event.currentTarget;
      var input = $("#receipt-file");
      var file = input && input.files ? input.files[0] : null;
      if (!file) {
        toast("ابتدا عکس رسید را انتخاب کنید.", "err");
        return;
      }
      var form = new FormData();
      form.append("photo", file);
      withBusy(
        button,
        api("/api/orders/" + order.id + "/receipt", { body: form }).then(function () {
          closeSheet();
          toast("رسید ارسال شد.", "ok");
          state.me = null;
        })
      );
    };
  }

  // --- admin views ----------------------------------------------------------

  function renderAdmin() {
    var view = $("#view");
    view.innerHTML = '<div class="loader">در حال بارگذاری…</div>';
    var tab = state.adminTab;
    var handlers = {
      dashboard: adminDashboard,
      pending: adminPending,
      payments: adminPayments,
      plans: adminPlans,
      promos: adminPromos,
      users: adminUsers,
      panel: adminPanel,
      inventory: adminInventory,
      settings: adminSettings,
      broadcast: adminBroadcast
    };
    (handlers[tab] || adminDashboard)();
  }

  function adminDashboard() {
    api("/api/admin/dashboard")
      .then(function (data) {
        var stats = data.stats || {};
        var stock = data.stock || {};
        var html = '<div class="grid">';
        Object.keys(stats).forEach(function (key) {
          html +=
            '<div class="stat"><div class="k">' +
            esc(key) +
            '</div><div class="v">' +
            esc(stats[key]) +
            "</div></div>";
        });
        html += "</div>";

        var stockRows = "";
        Object.keys(stock).forEach(function (key) {
          stockRows += row(key, stock[key]);
        });
        html +=
          '<div class="section-title">موجودی کانفیگ</div><div class="card">' +
          (stockRows || '<div class="empty">موردی نیست.</div>') +
          "</div>";
        $("#view").innerHTML = html;
      })
      .catch(function (err) {
        $("#view").innerHTML = '<div class="empty">' + esc(err.message) + "</div>";
      });
  }

  function adminPending() {
    api("/api/admin/pending")
      .then(function (data) {
        var items = data.items || [];
        if (!items.length) {
          $("#view").innerHTML = '<div class="empty">موردی در انتظار بررسی نیست.</div>';
          return;
        }
        var html = "";
        items.forEach(function (item) {
          if (item.kind === "topup") {
            html +=
              '<div class="card"><div class="row"><span class="k">شارژ کیف پول #' +
              esc(item.id) +
              "</span>" +
              chip("در انتظار", "warn") +
              "</div>" +
              row("کاربر", item.user_id) +
              row("مبلغ", money(item.amount) + " تومان") +
              row("تاریخ", item.created_at) +
              '<div class="btns" style="margin-top:10px">' +
              '<button class="btn success sm" data-action="approve-topup" data-id="' +
              esc(item.id) +
              '" type="button">تایید</button>' +
              '<button class="btn danger sm" data-action="reject-topup" data-id="' +
              esc(item.id) +
              '" type="button">رد</button>' +
              "</div></div>";
            return;
          }
          html +=
            '<div class="card"><div class="row"><span class="k">سفارش #' +
            esc(item.order_id) +
            "</span>" +
            chip("در انتظار", "warn") +
            "</div>" +
            row("کاربر", item.user_id) +
            row("روش", item.method) +
            row("مبلغ", money(item.amount) + " تومان") +
            row("مدت", item.days + " روز · " + item.traffic_gb + " GB") +
            row("تاریخ", item.created_at) +
            (item.receipt_file_id
              ? '<div class="sub mono small" style="margin-top:8px;word-break:break-all">' +
                esc(item.receipt_file_id) +
                "</div>"
              : "") +
            '<div class="btns" style="margin-top:10px">' +
            '<button class="btn success sm" data-action="approve-payment" data-id="' +
            esc(item.id) +
            '" type="button">تایید و فعال‌سازی</button>' +
            '<button class="btn danger sm" data-action="reject-payment" data-id="' +
            esc(item.id) +
            '" type="button">رد</button>' +
            "</div></div>";
        });
        $("#view").innerHTML = html;
      })
      .catch(function (err) {
        $("#view").innerHTML = '<div class="empty">' + esc(err.message) + "</div>";
      });
  }

  function adminPayments() {
    api("/api/admin/payments?limit=100")
      .then(function (data) {
        var payments = data.payments || [];
        if (!payments.length) {
          $("#view").innerHTML = '<div class="empty">پرداختی ثبت نشده است.</div>';
          return;
        }
        var html = '<div class="card">';
        payments.forEach(function (payment) {
          html +=
            '<div class="list-item"><div><div class="t">#' +
            esc(payment.order_id) +
            " · " +
            esc(payment.method) +
            '</div><div class="s">' +
            esc(payment.created_at) +
            " · " +
            money(payment.amount) +
            " تومان</div></div><div>" +
            chip(
              payment.status === "approved"
                ? "تایید شده"
                : payment.status === "rejected"
                ? "رد شده"
                : payment.status === "duplicate"
                ? "تکراری"
                : "در انتظار",
              payment.status === "approved"
                ? "ok"
                : payment.status === "rejected"
                ? "bad"
                : payment.status === "duplicate"
                ? "bad"
                : "warn"
            ) +
            "</div></div>";
        });
        html += "</div>";
        $("#view").innerHTML = html;
      })
      .catch(function (err) {
        $("#view").innerHTML = '<div class="empty">' + esc(err.message) + "</div>";
      });
  }

  function adminPlans() {
    api("/api/admin/plans")
      .then(function (data) {
        var plans = data.plans || [];
        var stock = data.stock || {};
        var html = "";
        plans.forEach(function (plan) {
          html +=
            '<div class="card"><div class="row"><span class="k">' +
            esc(plan.name) +
            '</span><div class="btns">' +
            '<button class="btn sm ghost" data-action="toggle-plan" data-id="' +
            esc(plan.id) +
            '" data-active="' +
            (plan.is_active ? "1" : "0") +
            '" type="button">' +
            (plan.is_active ? "غیرفعال" : "فعال") +
            "</button>" +
            '<button class="btn sm danger" data-action="delete-plan" data-id="' +
            esc(plan.id) +
            '" type="button">حذف</button>' +
            "</div></div>" +
            row("شناسه", plan.id) +
            row("مدت", plan.days + " روز") +
            row("حجم", plan.traffic_gb + " GB") +
            row("دستگاه", plan.devices) +
            row("قیمت", money(plan.price) + " تومان") +
            row("موجودی کانفیگ", stock[plan.id] || 0) +
            "</div>";
        });

        html +=
          '<div class="section-title">پلن جدید</div>' +
          '<div class="card"><div class="inline-2">' +
          '<label class="field"><span class="lbl">شناسه</span><input id="plan-id" type="text" placeholder="premium"></label>' +
          '<label class="field"><span class="lbl">نام</span><input id="plan-name" type="text" placeholder="پریمیوم"></label>' +
          "</div>" +
          '<div class="inline-2">' +
          '<label class="field"><span class="lbl">روز</span><input id="plan-days" type="number" min="1" value="30"></label>' +
          '<label class="field"><span class="lbl">حجم (GB)</span><input id="plan-traffic" type="number" min="1" value="50"></label>' +
          "</div>" +
          '<div class="inline-2">' +
          '<label class="field"><span class="lbl">قیمت</span><input id="plan-price" type="number" min="0" value="100000"></label>' +
          '<label class="field"><span class="lbl">دستگاه</span><input id="plan-devices" type="number" min="1" value="1"></label>' +
          "</div>" +
          '<label class="field"><span class="lbl">توضیح</span><input id="plan-desc" type="text"></label>' +
          '<button class="btn" data-action="create-plan" type="button">افزودن پلن</button></div>';
        $("#view").innerHTML = html;
      })
      .catch(function (err) {
        $("#view").innerHTML = '<div class="empty">' + esc(err.message) + "</div>";
      });
  }

  function adminPromos() {
    api("/api/admin/promos")
      .then(function (data) {
        var promos = data.promos || [];
        var html = "";
        promos.forEach(function (promo) {
          var effect =
            promo.kind === "percent"
              ? promo.value + "٪ تخفیف"
              : promo.kind === "days"
              ? promo.value + " روز اضافه"
              : promo.value + " گیگابایت اضافه";
          html +=
            '<div class="card"><div class="row"><span class="k mono">' +
            esc(promo.code) +
            "</span>" +
            chip(promo.is_active ? "فعال" : "غیرفعال", promo.is_active ? "ok" : "") +
            "</div>" +
            row("اثر", effect) +
            row("محدودیت", promo.max_uses ? promo.max_uses + " بار" : "نامحدود") +
            row("استفاده شده", promo.used_count) +
            '<div class="btns" style="margin-top:10px">' +
            '<button class="btn sm ghost" data-action="toggle-promo" data-id="' +
            esc(promo.id) +
            '" type="button">' +
            (promo.is_active ? "غیرفعال کردن" : "فعال کردن") +
            "</button>" +
            '<button class="btn sm danger" data-action="delete-promo" data-id="' +
            esc(promo.id) +
            '" type="button">حذف</button>' +
            "</div></div>";
        });

        html +=
          '<div class="section-title">کد جدید</div>' +
          '<div class="card"><div class="inline-2">' +
          '<label class="field"><span class="lbl">کد</span><input id="promo-code" type="text" placeholder="SUMMER"></label>' +
          '<label class="field"><span class="lbl">نوع</span><select id="promo-kind"><option value="percent">درصد تخفیف</option><option value="days">روز اضافه</option><option value="gb">گیگابایت اضافه</option></select></label>' +
          "</div>" +
          '<div class="inline-2">' +
          '<label class="field"><span class="lbl">مقدار</span><input id="promo-value" type="number" min="1" value="10"></label>' +
          '<label class="field"><span class="lbl">حداکثر استفاده (۰ = نامحدود)</span><input id="promo-max" type="number" min="0" value="0"></label>' +
          "</div>" +
          '<button class="btn" data-action="create-promo" type="button">افزودن کد</button></div>';
        $("#view").innerHTML = html;
      })
      .catch(function (err) {
        $("#view").innerHTML = '<div class="empty">' + esc(err.message) + "</div>";
      });
  }

  function adminUsers(query) {
    var path = "/api/admin/users";
    if (query) {
      path += "?q=" + encodeURIComponent(query);
    }
    api(path)
      .then(function (data) {
        var users = data.users || [];
        var html =
          '<div class="card"><label class="field"><span class="lbl">جستجو (شناسه یا یوزرنیم)</span>' +
          '<div class="inline-2"><input id="user-search" type="text" value="' +
          esc(query || "") +
          '" placeholder="123456">' +
          '<button class="btn ghost" data-action="search-user" type="button">جستجو</button></div></label></div>';
        users.forEach(function (user) {
          html +=
            '<div class="card"><div class="row"><span class="k">' +
            esc(user.name) +
            "</span>" +
            (user.blocked ? chip("مسدود", "bad") : "") +
            "</div>" +
            row("شناسه", user.id) +
            row("موجودی", money(user.balance) + " تومان") +
            row("سفارش‌ها", user.orders) +
            row("مجموع خرید", money(user.spent) + " تومان") +
            row("دعوت‌ها", user.referrals) +
            '<button class="btn sm ghost" data-action="open-user" data-id="' +
            esc(user.id) +
            '" type="button">جزئیات</button></div>';
        });
        $("#view").innerHTML = html;
      })
      .catch(function (err) {
        $("#view").innerHTML = '<div class="empty">' + esc(err.message) + "</div>";
      });
  }

  function adminPanel() {
    $("#view").innerHTML = '<div class="loader">در حال بارگذاری…</div>';
    api("/api/admin/panel")
      .then(function (data) {
        var panel = data.panel || {};
        var inbounds = data.inbounds || [];
        var html =
          '<div class="card"><h3>اتصال به پنل 3x-ui</h3>' +
          '<label class="field"><span class="lbl">آدرس پنل</span><input id="panel-url" type="url" value="' +
          esc(panel.url || "") +
          '" placeholder="https://panel.example.com"></label>' +
          '<label class="field"><span class="lbl">پیشوند مسیر (اگر پنل زیر مسیر دارد)</span><input id="panel-base" type="text" value="' +
          esc(panel.base_path || "") +
          '" placeholder="/panel"></label>' +
          '<div class="inline-2">' +
          '<label class="field"><span class="lbl">نام کاربری</span><input id="panel-user" type="text" value="' +
          esc(panel.username || "") +
          '"></label>' +
          '<label class="field"><span class="lbl">رمز عبور</span><input id="panel-pass" type="password" placeholder="' +
          (panel.has_password ? "••••••" : "") +
          '"></label>' +
          "</div>" +
          '<label class="field"><span class="lbl">توکن API</span><input id="panel-token" type="password" placeholder="' +
          (panel.has_token ? "••••••" : "") +
          '"></label>' +
          '<label class="field"><span class="lbl">حالت فعال‌سازی</span><select id="panel-mode">' +
          '<option value="manual"' +
          (panel.mode === "manual" ? " selected" : "") +
          ">دستی (لینک کانفیگ)</option>" +
          '<option value="auto"' +
          (panel.mode === "auto" ? " selected" : "") +
          ">خودکار (3x-ui)</option>" +
          "</select></label>" +
          '<button class="btn" data-action="save-panel" type="button">ذخیره</button>' +
          '<div class="btns" style="margin-top:8px">' +
          '<button class="btn ghost sm" data-action="test-panel" type="button">تست اتصال</button>' +
          '<button class="btn ghost sm" data-action="mint-token" type="button">ساخت توکن API</button>' +
          "</div>" +
          (data.error ? '<div class="banner danger" style="margin-top:10px">' + esc(data.error) + "</div>" : "") +
          "</div>";

        html +=
          '<div class="section-title">Inbound ها</div><div class="card">';
        if (!inbounds.length) {
          html += '<div class="empty">موردی پیدا نشد. ابتدا اتصال را تست کنید.</div>';
        } else {
          var selected = (panel.selected_inbound_ids || []).map(String);
          inbounds.forEach(function (inbound) {
            var id = String(inbound.id);
            var isOn = selected.indexOf(id) >= 0;
            html +=
              '<label class="check"><input type="checkbox" class="inbound-check" value="' +
              esc(id) +
              '"' +
              (isOn ? " checked" : "") +
              "> <span>" +
              esc(inbound.remark || "inbound " + id) +
              " (" +
              esc(inbound.protocol || "?") +
              ")</span></label>";
          });
          html +=
            '<button class="btn" data-action="save-inbounds" type="button">ذخیره inbound ها</button>';
        }
        html += "</div>";

        html +=
          '<div class="section-title">لینک اشتراک</div><div class="card">' +
          row("پایه", panel.subscription_base_url || "—") +
          row("تایید اتصال", panel.verified ? "بله" : "خیر") +
          "</div>";
        $("#view").innerHTML = html;
      })
      .catch(function (err) {
        $("#view").innerHTML = '<div class="empty">' + esc(err.message) + "</div>";
      });
  }

  function adminInventory() {
    api("/api/admin/inventory")
      .then(function (data) {
        var entries = data.entries || [];
        var html =
          '<div class="card"><h3>افزودن کانفیگ</h3>' +
          '<label class="field"><span class="lbl">شناسه پلن</span><input id="inv-plan" type="text" placeholder="premium"></label>' +
          '<label class="field"><span class="lbl">متن کانفیگ / لینک اشتراک</span><textarea id="inv-text"></textarea></label>' +
          '<button class="btn" data-action="add-inventory" type="button">افزودن</button></div>';

        html += '<div class="section-title">انبار</div><div class="card">';
        if (!entries.length) {
          html += '<div class="empty">موردی ثبت نشده است.</div>';
        } else {
          entries.forEach(function (entry) {
            html +=
              '<div class="list-item"><div><div class="t">' +
              esc(entry.plan_id) +
              '</div><div class="s">#' +
              esc(entry.id) +
              " · " +
              esc(entry.created_at) +
              '</div></div><div>' +
              chip(entry.is_assigned ? "استفاده شده" : "آزاد", entry.is_assigned ? "" : "ok") +
              "</div></div>";
          });
        }
        html += "</div>";
        $("#view").innerHTML = html;
      })
      .catch(function (err) {
        $("#view").innerHTML = '<div class="empty">' + esc(err.message) + "</div>";
      });
  }

  function adminSettings() {
    api("/api/admin/settings")
      .then(function (data) {
        var trial = data.trial || {};
        var pricing = data.pricing || {};
        var html =
          '<div class="card"><h3>اطلاعات کارت</h3>' +
          '<label class="field"><span class="lbl">شماره کارت</span><input id="set-card" type="text" value="' +
          esc(data.card_number || "") +
          '"></label>' +
          '<label class="field"><span class="lbl">نام صاحب کارت</span><input id="set-holder" type="text" value="' +
          esc(data.card_holder || "") +
          '"></label></div>';

        html +=
          '<div class="card"><h3>پلن تست رایگان</h3>' +
          '<label class="check"><input type="checkbox" id="set-trial"' +
          (trial.enabled ? " checked" : "") +
          "> <span>فعال باشد</span></label>" +
          '<div class="inline-2">' +
          '<label class="field"><span class="lbl">روز</span><input id="set-trial-days" type="number" min="0" value="' +
          esc(trial.days || 0) +
          '"></label>' +
          '<label class="field"><span class="lbl">حجم (GB)</span><input id="set-trial-gb" type="number" min="0" value="' +
          esc(trial.traffic_gb || 0) +
          '"></label>' +
          "</div></div>";

        html +=
          '<div class="card"><h3>قیمت‌گذاری</h3>' +
          '<div class="inline-2">' +
          '<label class="field"><span class="lbl">قیمت هر روز</span><input id="set-per-day" type="number" min="0" value="' +
          esc(pricing.per_day || 0) +
          '"></label>' +
          '<label class="field"><span class="lbl">قیمت هر گیگابایت</span><input id="set-per-gb" type="number" min="0" value="' +
          esc(pricing.per_gb || 0) +
          '"></label>' +
          "</div>" +
          '<label class="field"><span class="lbl">ارزش هر استارز (تومان)</span><input id="set-stars" type="number" min="1" value="' +
          esc(data.stars_rate || 50) +
          '"></label></div>';

        html +=
          '<div class="card"><h3>فروشگاه</h3>' +
          '<label class="check"><input type="checkbox" id="set-maintenance"' +
          (data.maintenance ? " checked" : "") +
          "> <span>حالت نگهداری (فروش بسته)</span></label>" +
          '<button class="btn" data-action="save-settings" type="button">ذخیره تنظیمات</button></div>';

        $("#view").innerHTML = html;
      })
      .catch(function (err) {
        $("#view").innerHTML = '<div class="empty">' + esc(err.message) + "</div>";
      });
  }

  function adminBroadcast() {
    $("#view").innerHTML =
      '<div class="card"><h3>ارسال پیام همگانی</h3>' +
      '<label class="field"><span class="lbl">متن پیام</span><textarea id="bc-text"></textarea></label>' +
      '<label class="check"><input type="checkbox" id="bc-html"><span>استفاده از HTML</span></label>' +
      '<label class="field"><span class="lbl">شناسه کاربر (خالی = همه)</span><input id="bc-user" type="text"></label>' +
      '<button class="btn" data-action="send-broadcast" type="button">ارسال</button>' +
      '<p class="muted small">ارسال پیام همگانی ممکن است چند دقیقه طول بکشد؛ صفحه را نبندید.</p></div>';
  }

  // --- admin actions --------------------------------------------------------

  function openUser(userId) {
    api("/api/admin/users/" + userId)
      .then(function (data) {
        var user = data.user || {};
        var orders = data.orders || [];
        var html =
          '<div class="card">' +
          row("شناسه", user.id) +
          row("نام", user.name) +
          row("یوزرنیم", user.username || "—") +
          row("موجودی", money(user.balance) + " تومان") +
          row("سفارش‌ها", user.total_orders) +
          row("تکمیل‌شده", user.completed_orders) +
          row("در انتظار بررسی", user.pending_payments) +
          row("مجموع خرید", money(user.total_spent) + " تومان") +
          row("اشتراک فعال", user.active_accounts) +
          row("دعوت‌ها", user.referrals) +
          "</div>";

        html +=
          '<div class="card"><h3>عملیات</h3>' +
          '<label class="field"><span class="lbl">افزایش موجودی (تومان)</span><input id="credit-amount" type="number" min="1" value="10000"></label>' +
          '<button class="btn ghost" data-action="credit-user" data-id="' +
          esc(user.id) +
          '" type="button">افزایش موجودی</button>' +
          '<button class="btn ghost" data-action="toggle-block" data-id="' +
          esc(user.id) +
          '" data-blocked="' +
          (user.blocked ? "1" : "0") +
          '" type="button" style="margin-top:8px">' +
          (user.blocked ? "رفع مسدودی" : "مسدود کردن") +
          "</button></div>";

        html += '<div class="section-title">سفارش‌ها</div><div class="card">';
        if (!orders.length) {
          html += '<div class="empty">سفارشی ندارد.</div>';
        } else {
          orders.forEach(function (order) {
            html +=
              '<div class="list-item"><div><div class="t">#' +
              esc(order.id) +
              '</div><div class="s">' +
              esc(order.days) +
              " روز · " +
              esc(order.traffic_gb) +
              " GB · " +
              money(order.price) +
              " تومان</div></div><div>" +
              orderStatusChip(order.status) +
              "</div></div>";
          });
        }
        html += "</div>";
        openSheet("کاربر #" + user.id, html);
      })
      .catch(function (err) {
        toast(err.message, "err");
      });
  }

  function numberOf(id) {
    var el = $(id);
    var value = el ? el.value : "";
    var number = parseInt(value, 10);
    return isNaN(number) ? null : number;
  }

  function textOf(id) {
    var el = $(id);
    return el ? String(el.value || "").trim() : "";
  }

  // --- events ---------------------------------------------------------------

  document.addEventListener("click", function (event) {
    var target = event.target;

    var tab = target.closest ? target.closest("[data-tab]") : null;
    if (tab) {
      switchTab(tab.dataset.tab);
      return;
    }

    var adminTab = target.closest ? target.closest("[data-admin-tab]") : null;
    if (adminTab) {
      state.adminTab = adminTab.dataset.adminTab;
      renderAdminBar();
      renderAdmin();
      return;
    }

    var plan = target.closest ? target.closest("[data-plan]") : null;
    if (plan) {
      haptic();
      openCheckout({ plan_id: plan.dataset.plan });
      return;
    }

    var action = target.closest ? target.closest("[data-action]") : null;
    if (!action) {
      return;
    }

    var name = action.dataset.action;
    var button = action.tagName === "BUTTON" ? action : null;

    if (name === "go-plans") {
      switchTab("plans");
      return;
    }
    if (name === "copy-sub") {
      copyText(state.me.account && state.me.account.subscription_url);
      return;
    }
    if (name === "copy-link") {
      copyText(action.dataset.link);
      return;
    }
    if (name === "share") {
      if (tg && tg.shareUrl) {
        tg.shareUrl(action.dataset.link, "با این لینک ثبت‌نام کن و تخفیف بگیر");
      }
      return;
    }
    if (name === "show-qr") {
      showQr();
      return;
    }
    if (name === "claim-trial") {
      withBusy(
        button,
        api("/api/trial", { body: {} }).then(function () {
          toast("پلن تست فعال شد.", "ok");
          state.me = null;
          return refreshMe().then(render);
        })
      );
      return;
    }
    if (name === "apply-promo") {
      state.promoCode = textOf("#promo-input");
      var hint = $("#promo-hint");
      if (hint) {
        hint.textContent = state.promoCode
          ? "کد «" + state.promoCode + "» اعمال شد."
          : "کد پرومو پاک شد.";
      }
      toast(state.promoCode ? "کد پرومو اعمال شد." : "کد پرومو پاک شد.");
      return;
    }
    if (name === "quote-custom") {
      var days = numberOf("#custom-days");
      var traffic = numberOf("#custom-traffic");
      if (!days || !traffic) {
        toast("مدت و حجم را وارد کنید.", "err");
        return;
      }
      openCheckout({ days: days, traffic: traffic });
      return;
    }
    if (name === "pay-wallet" || name === "pay-stars" || name === "pay-card") {
      choosePayment(name.replace("pay-", ""), button);
      return;
    }
    if (name === "topup") {
      var amount = numberOf("#topup-amount");
      if (!amount || amount < 1) {
        toast("مبلغ را وارد کنید.", "err");
        return;
      }
      withBusy(
        button,
        api("/api/wallet/topup", { body: { amount: amount } }).then(function () {
          toast("درخواست ثبت شد. رسید را برای ادمین بفرستید.", "ok");
          renderWallet();
        })
      );
      return;
    }
    if (name === "pay-order") {
      var orderId = action.dataset.order;
      api("/api/orders")
        .then(function (data) {
          var order = (data.orders || []).filter(function (item) {
            return String(item.id) === String(orderId);
          })[0];
          if (!order) {
            throw new Error("سفارش پیدا نشد.");
          }
          var payload = order.plan_id
            ? { plan_id: order.plan_id }
            : { days: order.days, traffic: order.traffic_gb };
          openCheckout(payload);
        })
        .catch(function (err) {
          toast(err.message, "err");
        });
      return;
    }
    if (name === "approve-payment") {
      withBusy(
        button,
        api("/api/admin/payments/" + action.dataset.id + "/approve", { body: {} }).then(
          function () {
            toast("تایید شد.", "ok");
            renderAdmin();
          }
        )
      );
      return;
    }
    if (name === "reject-payment") {
      withBusy(
        button,
        api("/api/admin/payments/" + action.dataset.id + "/reject", { body: {} }).then(
          function () {
            toast("رد شد.", "ok");
            renderAdmin();
          }
        )
      );
      return;
    }
    if (name === "approve-topup") {
      withBusy(
        button,
        api("/api/admin/topups/" + action.dataset.id + "/approve", { body: {} }).then(
          function () {
            toast("تایید شد.", "ok");
            renderAdmin();
          }
        )
      );
      return;
    }
    if (name === "reject-topup") {
      withBusy(
        button,
        api("/api/admin/topups/" + action.dataset.id + "/reject", { body: {} }).then(
          function () {
            toast("رد شد.", "ok");
            renderAdmin();
          }
        )
      );
      return;
    }
    if (name === "create-plan") {
      withBusy(
        button,
        api("/api/admin/plans", {
          body: {
            id: textOf("#plan-id"),
            name: textOf("#plan-name"),
            days: numberOf("#plan-days"),
            traffic_gb: numberOf("#plan-traffic"),
            price: numberOf("#plan-price"),
            devices: numberOf("#plan-devices"),
            description: textOf("#plan-desc")
          }
        }).then(function () {
          toast("پلن ساخته شد.", "ok");
          renderAdmin();
        })
      );
      return;
    }
    if (name === "toggle-plan") {
      withBusy(
        button,
        api("/api/admin/plans/" + action.dataset.id, {
          body: { is_active: action.dataset.active !== "1" }
        }).then(function () {
          toast("به‌روزرسانی شد.", "ok");
          renderAdmin();
        })
      );
      return;
    }
    if (name === "delete-plan") {
      if (!confirm("این پلن حذف شود؟")) {
        return;
      }
      withBusy(
        button,
        api("/api/admin/plans/" + action.dataset.id, { method: "DELETE" }).then(
          function () {
            toast("حذف شد.", "ok");
            renderAdmin();
          }
        )
      );
      return;
    }
    if (name === "create-promo") {
      withBusy(
        button,
        api("/api/admin/promos", {
          body: {
            code: textOf("#promo-code"),
            kind: textOf("#promo-kind"),
            value: numberOf("#promo-value"),
            max_uses: numberOf("#promo-max")
          }
        }).then(function () {
          toast("کد ساخته شد.", "ok");
          renderAdmin();
        })
      );
      return;
    }
    if (name === "toggle-promo") {
      withBusy(
        button,
        api("/api/admin/promos/" + action.dataset.id + "/toggle", { body: {} }).then(
          function () {
            toast("به‌روزرسانی شد.", "ok");
            renderAdmin();
          }
        )
      );
      return;
    }
    if (name === "delete-promo") {
      if (!confirm("این کد حذف شود؟")) {
        return;
      }
      withBusy(
        button,
        api("/api/admin/promos/" + action.dataset.id, { method: "DELETE" }).then(
          function () {
            toast("حذف شد.", "ok");
            renderAdmin();
          }
        )
      );
      return;
    }
    if (name === "search-user") {
      var query = textOf("#user-search");
      adminUsers(query);
      return;
    }
    if (name === "open-user") {
      openUser(action.dataset.id);
      return;
    }
    if (name === "toggle-block") {
      withBusy(
        button,
        api("/api/admin/users/" + action.dataset.id + "/block", {
          body: { blocked: action.dataset.blocked !== "1" }
        }).then(function () {
          toast("به‌روزرسانی شد.", "ok");
          closeSheet();
          renderAdmin();
        })
      );
      return;
    }
    if (name === "credit-user") {
      var credit = numberOf("#credit-amount");
      if (!credit || credit < 1) {
        toast("مبلغ را وارد کنید.", "err");
        return;
      }
      withBusy(
        button,
        api("/api/admin/users/" + action.dataset.id + "/credit", {
          body: { amount: credit }
        }).then(function () {
          toast("موجودی اضافه شد.", "ok");
          closeSheet();
        })
      );
      return;
    }
    if (name === "save-panel") {
      withBusy(
        button,
        api("/api/admin/panel", {
          body: {
            url: textOf("#panel-url"),
            base_path: textOf("#panel-base"),
            username: textOf("#panel-user"),
            password: textOf("#panel-pass"),
            api_token: textOf("#panel-token"),
            mode: textOf("#panel-mode")
          }
        }).then(function () {
          toast("ذخیره شد.", "ok");
          adminPanel();
        })
      );
      return;
    }
    if (name === "test-panel") {
      withBusy(
        button,
        api("/api/admin/panel/test", { body: {} }).then(function (data) {
          toast(
            "اتصال برقرار است. " +
              (data.total_inbounds || 0) +
              " inbound، " +
              (data.enabled_inbounds || 0) +
              " فعال.",
            "ok"
          );
          adminPanel();
        })
      );
      return;
    }
    if (name === "mint-token") {
      withBusy(
        button,
        api("/api/admin/panel/token", { body: {} }).then(function () {
          toast("توکن ساخته و ذخیره شد.", "ok");
          adminPanel();
        })
      );
      return;
    }
    if (name === "save-inbounds") {
      var ids = [];
      var checks = document.querySelectorAll(".inbound-check");
      for (var i = 0; i < checks.length; i++) {
        if (checks[i].checked) {
          ids.push(parseInt(checks[i].value, 10));
        }
      }
      withBusy(
        button,
        api("/api/admin/panel/inbounds", { body: { inbound_ids: ids } }).then(function () {
          toast("ذخیره شد.", "ok");
          adminPanel();
        })
      );
      return;
    }
    if (name === "add-inventory") {
      withBusy(
        button,
        api("/api/admin/inventory", {
          body: { plan_id: textOf("#inv-plan"), config_text: textOf("#inv-text") }
        }).then(function () {
          toast("افزوده شد.", "ok");
          renderAdmin();
        })
      );
      return;
    }
    if (name === "save-settings") {
      withBusy(
        button,
        api("/api/admin/settings", {
          body: {
            card_number: textOf("#set-card"),
            card_holder: textOf("#set-holder"),
            trial_enabled: $("#set-trial").checked,
            trial_days: numberOf("#set-trial-days"),
            trial_gb: numberOf("#set-trial-gb"),
            stars_rate: numberOf("#set-stars"),
            maintenance: $("#set-maintenance").checked,
            pricing: {
              per_day: numberOf("#set-per-day"),
              per_gb: numberOf("#set-per-gb")
            }
          }
        }).then(function () {
          toast("ذخیره شد.", "ok");
          renderAdmin();
        })
      );
      return;
    }
    if (name === "send-broadcast") {
      var text = textOf("#bc-text");
      if (!text) {
        toast("متن پیام را وارد کنید.", "err");
        return;
      }
      var userId = textOf("#bc-user");
      withBusy(
        button,
        api("/api/admin/broadcast", {
          body: {
            text: text,
            use_html: $("#bc-html").checked,
            user_id: userId ? parseInt(userId, 10) : null
          }
        }).then(function (data) {
          toast("ارسال شد: " + (data.sent || 0) + " موفق، " + (data.failed || 0) + " ناموفق.", "ok");
        })
      );
      return;
    }
  });

  function copyText(text) {
    if (!text) {
      toast("موردی برای کپی وجود ندارد.", "err");
      return;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () {
          toast("کپی شد.", "ok");
        },
        function () {
          toast("کپی نشد. متن را دستی انتخاب کنید.", "err");
        }
      );
      return;
    }
    var area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand("copy");
      toast("کپی شد.", "ok");
    } catch (err) {
      toast("کپی نشد.", "err");
    }
    document.body.removeChild(area);
  }

  function showQr() {
    fetch("/api/qr.png", {
      headers: { "X-Telegram-Init-Data": (tg && tg.initData) || "" }
    })
      .then(function (res) {
        if (!res.ok) {
          throw new Error("کد QR در دسترس نیست.");
        }
        return res.blob();
      })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        openSheet(
          "کد QR اشتراک",
          '<img class="qr" src="' + url + '" alt="QR">' +
            '<p class="muted small center">این کد را با کلاینت خود اسکن کنید.</p>'
        );
      })
      .catch(function (err) {
        toast(err.message, "err");
      });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      var el = event.target;
      if (el && el.id === "promo-input") {
        state.promoCode = String(el.value || "").trim();
        toast(state.promoCode ? "کد پرومو اعمال شد." : "کد پرومو پاک شد.");
      }
      if (el && el.id === "user-search") {
        var button = document.querySelector('[data-action="search-user"]');
        if (button) {
          button.click();
        }
      }
    }
  });

  $("#sheet-close").onclick = closeSheet;
  $("#sheet-backdrop").onclick = closeSheet;

  // --- boot -----------------------------------------------------------------

  function refreshMe() {
    return api("/api/me").then(function (data) {
      state.me = data;
      return data;
    });
  }

  function refreshPlans() {
    return api("/api/plans")
      .then(function (data) {
        state.plans = data.plans || [];
        state.pricing = data.custom_pricing || null;
      })
      .catch(function () {
        state.plans = [];
      });
  }

  function boot() {
    if (tg) {
      tg.ready();
      tg.expand();
      if (tg.setHeaderColor) {
        tg.setHeaderColor("#0f1216");
      }
      if (tg.setBackgroundColor) {
        tg.setBackgroundColor("#0f1216");
      }
    }

    tabFromLocation();

    refreshMe()
      .then(function (me) {
        updateHeader();
        var adminTab = document.querySelector('[data-tab="admin"]');
        if (adminTab) {
          adminTab.hidden = !me.is_admin;
        }
        if (state.tab === "admin" && !me.is_admin) {
          state.tab = "home";
        }
        return refreshPlans();
      })
      .then(function () {
        render();
      })
      .catch(function (err) {
        $("#view").innerHTML =
          '<div class="empty">' +
          esc(err.message || "ارتباط با سرور برقرار نشد.") +
          "</div>";
      });
  }

  boot();
})();

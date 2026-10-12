// Sentinelle — Veille intelligente & Recherche ancrée
const $ = (s) => document.querySelector(s);

// --- Firebase Configuration ---
const FIREBASE_CONFIG = {
  apiKey: "AIzaSyDEbXSEs3etZrYg3AF1OegxGuwHP1oU0Ss",
  authDomain: "sentinelle-553b2.firebaseapp.com",
  projectId: "sentinelle-553b2",
  storageBucket: "sentinelle-553b2.firebasestorage.app",
  messagingSenderId: "329906905902",
  appId: "1:329906905902:web:db8e30d17d8f9d626f50bf",
  measurementId: "G-0LZ7R6J88X"
};

const state = {
  sessionId: localStorage.getItem("sentinelle_session") || ("s_" + Date.now()),
  spaces: [],
  currentSpace: null,
  currentWatchSlug: null,
  theme: localStorage.getItem("scout_theme") || "dark",
  offers: [],
  agents: {},
  running: false,
  token: localStorage.getItem("med_token") || "",
  firebaseUser: null,
  priceChart: null,
  typeChart: null,
  lwChart: null,
  lwSeries: null,
  lwSma: null,
  marketSSE: null,
  oppTab: "feed",
  watchRefreshTimer: null,
  watchAssetClass: null,
  news: { items: [], total: 0, offset: 0, loading: false, knownIds: null, io: null, pollTimer: null, currentTopic: null, category: "" },
};

localStorage.setItem("scout_session", state.sessionId);

async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  // Préférer le token Firebase, sinon fallback sur le legacy token médical
  if (state.firebaseUser) {
    try {
      const freshToken = await state.firebaseUser.getIdToken();
      headers.Authorization = "Bearer " + freshToken;
    } catch (e) {
      console.warn("[Auth] Failed to get Firebase token:", e);
    }
  } else if (state.token) {
    headers.Authorization = "Bearer " + state.token;
  }
  const r = await fetch(path, { ...opts, headers });
  const contentType = r.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    const data = await r.json();
    if (!r.ok && !data.error) {
      data.error = `Erreur HTTP ${r.status}`;
    }
    return data;
  }
  if (!r.ok) {
    const text = await r.text();
    throw new Error(text.slice(0, 120) || `Erreur HTTP ${r.status}`);
  }
  return {};
}

function esc(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// N'accepte que les URLs http(s)/mailto : tout le reste (javascript:, data:, ...) devient "#".
function safeUrl(u) {
  const s = String(u || "").trim();
  return /^(https?:|mailto:)/i.test(s) ? s : "#";
}

function mdToHtml(md) {
  let h = esc(md);
  h = h.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  h = h.replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  // Listes markdown (- item, * item, 1. item) → vraies listes HTML
  h = h.replace(/(^|\n)((?:[ \t]*(?:[-*]|\d+\.)[ \t]+[^\n]+(?:\n|$))+)/g, (m, pre, block) => {
    const lines = block.trim().split("\n");
    const ordered = /^[ \t]*\d+\./.test(lines[0]);
    const items = lines
      .map((l) => l.replace(/^[ \t]*(?:[-*]|\d+\.)[ \t]+/, "").trim())
      .filter(Boolean)
      .map((l) => `<li>${l}</li>`)
      .join("");
    return `${pre}<${ordered ? "ol" : "ul"}>${items}</${ordered ? "ol" : "ul"}>`;
  });
  h = h.replace(/\n/g, "<br>");
  h = h.replace(/(<\/(?:ul|ol)>)<br>/g, "$1").replace(/<br>(<(?:ul|ol)>)/g, "$1");
  return h;
}

function applyTheme() {
  document.documentElement.setAttribute("data-theme", state.theme);
  const btn = $("#btnTheme");
  if (btn) btn.textContent = state.theme === "dark" ? "Theme clair" : "Theme sombre";
  document.querySelector('meta[name="theme-color"]')?.setAttribute(
    "content",
    state.theme === "dark" ? "#1C1917" : "#FAF7F2"
  );
}

function showView(name) {
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  const el = $("#view-" + name);
  if (el) el.classList.add("active");
  document.querySelectorAll(".nav-item").forEach((n) => {
    n.classList.toggle("active", n.dataset.view === name || (name === "space" && n.dataset.space && n.dataset.space === state.currentSpace));
  });
  $("#sidebar")?.classList.remove("open");
}
window.showView = showView;

window.quickQuery = function(text) {
  showView("home");
  const input = $("#homeInput");
  if (input) {
    input.value = text;
    $("#homeForm")?.dispatchEvent(new Event("submit", { cancelable: true }));
  }
};

// --- Firebase Auth helpers ---
function initFirebaseAuth() {
  if (typeof firebase === "undefined" || !firebase.apps) {
    console.warn("[Auth] Firebase SDK non chargé.");
    return;
  }
  if (!firebase.apps.length) {
    firebase.initializeApp(FIREBASE_CONFIG);
  }
  if (firebase.analytics) {
    try { firebase.analytics(); } catch (e) { console.debug("Analytics init:", e); }
  }
  firebase.auth().onAuthStateChanged((user) => {
    state.firebaseUser = user;
    updateAuthUI(user);
  });
}

function updateAuthUI(user) {
  const loggedOut = $("#authLoggedOut");
  const loggedIn = $("#authLoggedIn");
  if (!loggedOut || !loggedIn) return;

  if (user) {
    loggedOut.classList.add("hidden");
    loggedIn.classList.remove("hidden");
    const avatar = $("#authAvatar");
    const name = $("#authName");
    if (avatar) {
      avatar.src = user.photoURL || "";
      avatar.style.display = user.photoURL ? "block" : "none";
    }
    if (name) name.textContent = user.displayName || user.email || "Utilisateur";
  } else {
    loggedOut.classList.remove("hidden");
    loggedIn.classList.add("hidden");
  }
}

async function loginWithGoogle() {
  try {
    const provider = new firebase.auth.GoogleAuthProvider();
    await firebase.auth().signInWithPopup(provider);
    $("#loginModal")?.classList.add("hidden");
  } catch (e) {
    console.error("[Auth] Google login error:", e);
    alert("Erreur de connexion Google : " + e.message);
  }
}

async function loginWithEmail(email, password) {
  try {
    await firebase.auth().signInWithEmailAndPassword(email, password);
    $("#loginModal")?.classList.add("hidden");
  } catch (e) {
    if (e.code === "auth/user-not-found") {
      // Auto-cr\u00e9ation du compte
      try {
        await firebase.auth().createUserWithEmailAndPassword(email, password);
        $("#loginModal")?.classList.add("hidden");
      } catch (e2) {
        alert("Erreur de cr\u00e9ation : " + e2.message);
      }
    } else {
      alert("Erreur de connexion : " + e.message);
    }
  }
}

function logout() {
  if (typeof firebase !== "undefined" && firebase.auth) {
    firebase.auth().signOut();
  }
  state.token = "";
  state.firebaseUser = null;
  localStorage.removeItem("med_token");
}

async function init() {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  applyTheme();
  initFirebaseAuth();

  $("#btnTheme").addEventListener("click", () => {
    state.theme = state.theme === "dark" ? "light" : "dark";
    localStorage.setItem("scout_theme", state.theme);
    applyTheme();
  });
  $("#btnSidebar")?.addEventListener("click", () => $("#sidebar").classList.toggle("open"));

  // --- Câblage des anciens handlers inline (aucun onclick= restant dans index.html) ---
  document.querySelectorAll("[data-quick-query]").forEach((btn) => {
    btn.addEventListener("click", () => quickQuery(btn.dataset.quickQuery));
  });
  document.querySelectorAll("[data-nav]").forEach((el) => {
    el.addEventListener("click", () => {
      const v = el.dataset.nav;
      if (v === "home-focus") {
        showView("home");
        $("#homeInput").focus();
        return;
      }
      if (v === "watches") {
        showView("watches");
        loadWatches();
      } else {
        showView(v);
        if (v === "learn") loadLearn();
        else if (v === "medical-pro") {
          loadMedicalStats();
          loadMedicalRecords();
        }
      }
    });
    // Les pillar-card sont focusables : Entrée/Espace = clic
    el.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        el.click();
      }
    });
  });
  document.querySelectorAll("[data-nav-link]").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      const v = a.dataset.navLink;
      showView(v);
      if (v === "watches") loadWatches();
      else if (v === "learn") loadLearn();
      else if (v === "medical-pro") {
        loadMedicalStats();
        loadMedicalRecords();
      }
    });
  });
  $("#btnCtaLogin")?.addEventListener("click", () => $("#loginModal")?.classList.remove("hidden"));
  $("#btnPrintMedical")?.addEventListener("click", () => window.print());
  document.querySelectorAll("[data-amount-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = $(btn.dataset.amountPreset);
      if (!input) return;
      input.value = btn.dataset.amountValue;
      updateBetPayout();
    });
  });
  document.querySelectorAll("[data-beginner-side]").forEach((btn) => {
    btn.addEventListener("click", () => executeBeginnerSide(btn.dataset.beginnerSide));
  });
  $("#beginnerOrderForm")?.addEventListener("submit", (e) => e.preventDefault());
  document.querySelectorAll("[data-asset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      try {
        const a = JSON.parse(btn.dataset.asset);
        selectAsset(a.symbol, a.name);
      } catch (_) {}
    });
  });
  document.querySelectorAll("[data-learn-prompt]").forEach((btn) => {
    btn.addEventListener("click", () => askLearnPrompt(btn.dataset.learnPrompt));
  });
  const rangeBeginner = $("#learnChartRangeBeginner");
  rangeBeginner?.addEventListener("change", () => {
    const pro = $(rangeBeginner.dataset.syncRange);
    if (pro) pro.value = rangeBeginner.value;
    loadLearnChart();
  });

  // Auth event listeners
  $("#btnLogin")?.addEventListener("click", () => $("#loginModal")?.classList.remove("hidden"));
  $("#loginClose")?.addEventListener("click", () => $("#loginModal")?.classList.add("hidden"));
  $("#btnLogout")?.addEventListener("click", logout);
  $("#btnGoogleLogin")?.addEventListener("click", loginWithGoogle);
  $("#emailLoginForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const email = $("#loginEmail")?.value;
    const pwd = $("#loginPassword")?.value;
    if (email && pwd) loginWithEmail(email, pwd);
  });
  $("#btnSwitchRegister")?.addEventListener("click", (e) => {
    e.preventDefault();
    // Le formulaire email fait d\u00e9j\u00e0 auto-register si user-not-found
    alert("Remplis le formulaire email et clique Se connecter \u2014 ton compte sera cr\u00e9\u00e9 automatiquement.");
  });

  $("#btnNewChat").addEventListener("click", newChat);
  $("#btnBackHome").addEventListener("click", () => {
    $("#chatThread").classList.add("hidden");
    $("#homeLanding").classList.remove("hidden");
    showView("home");
  });
  $("#homeForm").addEventListener("submit", (e) => {
    e.preventDefault();
    ask($("#homeInput").value);
  });
  $("#threadForm").addEventListener("submit", (e) => {
    e.preventDefault();
    ask($("#threadInput").value);
  });
  $("#btnSpaceRefresh").addEventListener("click", () => loadSpace(state.currentSpace));
  $("#btnSpaceAsk").addEventListener("click", () => {
    const title = state.spaces.find((s) => s.slug === state.currentSpace)?.title || state.currentSpace;
    showView("home");
    ask(`Fais le point sur l'espace ${title}`);
  });
  $("#spaceSearch").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadSpace(state.currentSpace, $("#spaceSearch").value.trim());
  });

  document.querySelectorAll(".nav-item[data-view]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const v = btn.dataset.view;
      if (v === "home") {
        newChat(false);
        showView("home");
      } else if (v === "watches") {
        showView("watches");
        loadWatches();
      } else if (v === "opportunities") {
        showView("opportunities");
        loadOpportunitiesView();
      } else if (v === "news") {
        showView("news");
        loadNews({ reset: true });
        startNewsPolling();
      } else if (v === "profile") {
        showView("profile");
        loadProfile();
      } else if (v === "learn") {
        showView("learn");
        loadLearn();
      } else if (v === "tools") {
        showView("tools");
        loadStats();
        loadOffers();
        loadAgents();
      } else if (v === "medical-pro") {
        showView("medical-pro");
        loadMedicalStats();
        loadMedicalRecords();
      }
    });
  });

  $("#btnNewsRefresh")?.addEventListener("click", refreshNewsManual);
  setupNewsCategoryTabs();
  $("#btnNewsBanner")?.addEventListener("click", () => {
    $("#newsBanner")?.classList.add("hidden");
    $("#newsList")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  $("#watchCreateForm")?.addEventListener("submit", createWatchFromForm);
  $("#btnBackWatches")?.addEventListener("click", () => {
    stopMarketSSE();
    showView("watches");
    loadWatches();
  });
  $("#btnWatchRefresh")?.addEventListener("click", () => refreshCurrentWatch());
  $("#btnWatchSummary")?.addEventListener("click", () => analyzeCurrentWatch("summary"));
  $("#btnWatchAnalysis")?.addEventListener("click", () => analyzeCurrentWatch("analysis"));
  $("#btnWatchTrading")?.addEventListener("click", () => analyzeCurrentWatch("trading"));
  $("#watchAutoRefresh")?.addEventListener("change", () => setupWatchAutoRefresh());
  $("#watchChartRange")?.addEventListener("change", () => {
    if (state.currentWatchSlug) loadWatchLiveChart();
  });

  $("#btnOppDigestRefresh")?.addEventListener("click", () => loadOppDigest());
  $("#btnOppSearch")?.addEventListener("click", () => loadOppGrid());
  $("#oppSearch")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadOppGrid();
  });
  document.querySelectorAll("#oppTabs .tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.oppTab = btn.dataset.opp;
      document.querySelectorAll("#oppTabs .tab").forEach((t) => t.classList.toggle("active", t === btn));
      loadOppGrid();
    });
  });
  $("#profileForm")?.addEventListener("submit", saveProfile);
  $("#learnOrderForm")?.addEventListener("submit", placeLearnOrder);
  $("#learnAskForm")?.addEventListener("submit", askLearn);
  $("#btnLearnLevel")?.addEventListener("click", setLearnLevel);
  $("#btnLearnReset")?.addEventListener("click", resetLearn);
  $("#learnOrderType")?.addEventListener("change", () => {
    $("#learnLimitWrap")?.classList.toggle("hidden", $("#learnOrderType").value !== "limit");
  });
  $("#learnSide")?.addEventListener("change", () => {
    const side = $("#learnSide").value;
    $("#learnSubmitBtn").textContent = side === "buy" ? "Acheter" : "Vendre";
  });
  $("#learnSymbol")?.addEventListener("change", loadLearnChart);
  $("#learnChartRange")?.addEventListener("change", loadLearnChart);
  $("#btnLearnChartRefresh")?.addEventListener("click", loadLearnChart);
  wireTradingEvents();

  $("#btnRun")?.addEventListener("click", runAll);
  ["search", "fTier", "fNetwork", "fType"].forEach((id) => {
    const el = $("#" + id);
    if (el) el.addEventListener("input", renderOffers);
  });
  $("#btnMedSearch")?.addEventListener("click", loadMedicalRecords);
  ["medFilterCountry", "medFilterPhase", "medFilterStatus"].forEach((id) => {
    $("#" + id)?.addEventListener("change", loadMedicalRecords);
  });
  $("#medSearch")?.addEventListener("keypress", (e) => {
    if (e.key === "Enter") loadMedicalRecords();
  });
  $("#tabMedTrials")?.addEventListener("click", () => {
    $("#tabMedTrials").classList.add("active");
    $("#tabMedPlants").classList.remove("active");
    $("#medGrid").classList.remove("hidden");
    $("#medPlantsGrid").classList.add("hidden");
    loadMedicalRecords();
  });
  $("#tabMedPlants")?.addEventListener("click", () => {
    $("#tabMedPlants").classList.add("active");
    $("#tabMedTrials").classList.remove("active");
    $("#medPlantsGrid").classList.remove("hidden");
    $("#medGrid").classList.add("hidden");
    loadMedicalPlants();
  });
  $("#medClose")?.addEventListener("click", () => $("#medicalModal").classList.add("hidden"));
  $("#btnGenerateMedicalEmail")?.addEventListener("click", generateMedicalEmail);

  await loadSpaces();
  await loadHistory();
  connectSSE();
}

function newChat(resetSession = true) {
  if (resetSession) {
    state.sessionId = "s_" + Date.now();
    localStorage.setItem("scout_session", state.sessionId);
  }
  $("#threadMessages").innerHTML = "";
  $("#chatThread").classList.add("hidden");
  $("#homeLanding").classList.remove("hidden");
  $("#homeInput").value = "";
  showView("home");
}

async function loadSpaces() {
  try {
    state.spaces = await api("/api/spaces");
  } catch (_) {
    state.spaces = [];
  }
  const nav = $("#spacesNav");
  const chips = $("#spaceChips");
  nav.innerHTML = state.spaces
    .map(
      (s) =>
        `<button class="nav-item" type="button" data-space="${esc(s.slug)}">${esc(s.title)}</button>`
    )
    .join("");
  chips.innerHTML = state.spaces
    .map((s) => `<button class="chip" type="button" data-space="${esc(s.slug)}">${esc(s.title)}</button>`)
    .join("");

  document.querySelectorAll("[data-space]").forEach((btn) => {
    btn.addEventListener("click", () => openSpace(btn.dataset.space));
  });
}

async function openSpace(slug) {
  state.currentSpace = slug;
  showView("space");
  document.querySelectorAll(".nav-item").forEach((n) => {
    n.classList.toggle("active", n.dataset.space === slug);
  });
  await loadSpace(slug);
}

async function loadSpace(slug, q = "") {
  const grid = $("#spaceGrid");
  grid.innerHTML = '<p class="muted">Chargement des sources...</p>';
  let url = `/api/spaces/${encodeURIComponent(slug)}`;
  if (q) url += `?q=${encodeURIComponent(q)}`;
  try {
    const data = await api(url);
    $("#spaceTitle").textContent = data.title || slug;
    $("#spaceDesc").textContent = data.description || "";
    const disc = $("#spaceDisclaimer");
    if (data.disclaimer) {
      disc.textContent = data.disclaimer;
      disc.classList.add("show");
    } else {
      disc.classList.remove("show");
      disc.textContent = "";
    }
    const items = data.items || [];
    grid.innerHTML =
      items
        .map((it) => {
          const meta = esc(it.provider || it.source || it.offer_type || "");
          const title = esc(it.title || "");
          const desc = esc(it.description || "");
          const img = it.image_url || it.image || "https://images.unsplash.com/photo-1585829365295-ab7cd400c167?w=700&auto=format&fit=crop&q=80";
          const payload = encodeURIComponent(JSON.stringify(it));
          const isPrediction = slug === "predictions" || it.offer_type === "prediction";
          
          let predictionActions = "";
          if (isPrediction) {
            let yesPrice = Number(it.yes_price);
            let noPrice = Number(it.no_price);
            if (!Number.isFinite(yesPrice) || yesPrice <= 0) {
              const matchProb = (it.description || "").match(/(\d+(\.\d+)?)%\s*Oui/i) || (it.description || "").match(/(\d+(\.\d+)?)%\s*Yes/i);
              const yesProb = matchProb ? parseFloat(matchProb[1]) : 50;
              yesPrice = yesProb / 100;
            }
            if (!Number.isFinite(noPrice) || noPrice <= 0) {
              noPrice = Math.max(0.01, 1 - yesPrice);
            }
            yesPrice = Math.min(0.99, Math.max(0.01, yesPrice));
            noPrice = Math.min(0.99, Math.max(0.01, noPrice));
            const yesCents = Math.round(yesPrice * 100);
            const noCents = Math.round(noPrice * 100);
            const mid = esc(it.id || it.slug || "");
            predictionActions = `
              <div class="poly-actions" data-stop>
                <button class="btn-bet-yes" type="button" data-side="YES" data-price="${yesPrice.toFixed(4)}" data-market-id="${mid}">🟢 Acheter OUI (${yesCents}¢)</button>
                <button class="btn-bet-no" type="button" data-side="NO" data-price="${noPrice.toFixed(4)}" data-market-id="${mid}">🔴 Acheter NON (${noCents}¢)</button>
              </div>
            `;
          }

          return `<article class="discover-card" data-payload="${payload}">
            <img class="discover-card-img" src="${esc(img)}" alt="${title}" loading="lazy" data-fallback-img>
            <div class="discover-card-body">
              <div>
                <div class="meta">
                  <span>📰 ${meta}</span>
                  ${it.status ? `<span>· ${esc(it.status)}</span>` : ""}
                </div>
                <h3>${title}</h3>
                <p>${desc}</p>
              </div>
              <div class="discover-card-footer">
                ${predictionActions || `<span class="read-link">Lire la source complète sur ${meta} ↗</span>`}
                <span class="muted small">${esc(it.source || "source")}</span>
              </div>
            </div>
          </article>`;
        })
        .join("") || '<p class="muted">Aucun élément pour le moment.</p>';

    // Délégation d'événements attachée UNE SEULE fois par grille.
    if (!grid.dataset.wired) {
      grid.dataset.wired = "1";
      // Fallback image cassée → visuel générique (sans onerror inline)
      grid.addEventListener("error", (ev) => {
        const img = ev.target.closest("img[data-fallback-img]");
        if (img) img.src = "https://images.unsplash.com/photo-1585829365295-ab7cd400c167?w=700&auto=format&fit=crop&q=80";
      }, true);
      grid.addEventListener("click", (ev) => {
        const betBtn = ev.target.closest("[data-side]");
        if (betBtn) {
          ev.stopPropagation();
          const card = betBtn.closest("[data-payload]");
          if (!card) return;
          openBetModal(card.dataset.payload, betBtn.dataset.side, parseFloat(betBtn.dataset.price));
          return;
        }
        const card = ev.target.closest("[data-payload]");
        if (!card || !grid.contains(card)) return;
        let it;
        try {
          it = JSON.parse(decodeURIComponent(card.dataset.payload));
        } catch (_) {
          return;
        }
        if (it.offer_type === "medical" || it.source === "clinicaltrials" || it.source === "pubmed") {
          openMedicalModal(encodeURIComponent(JSON.stringify(it)));
          return;
        }
        openExternal(it.url);   // no-op si url vide/# → plus jamais de duplicata de l'accueil
      });
    }
  } catch (e) {
    grid.innerHTML = '<p class="muted">Erreur de chargement de l\'espace.</p>';
  }
}

async function loadHistory() {
  const box = $("#historyList");
  try {
    const sessions = await api("/api/chat/history?limit=20");
    box.innerHTML = (sessions || [])
      .map(
        (s) =>
          `<button class="history-item" type="button" data-sid="${esc(s.session_id)}">${esc(s.preview || s.session_id)}</button>`
      )
      .join("") || '<p class="muted small" style="padding:8px;">Pas encore d\'historique</p>';
    box.querySelectorAll(".history-item").forEach((btn) => {
      btn.addEventListener("click", () => openHistory(btn.dataset.sid));
    });
  } catch (_) {
    box.innerHTML = "";
  }
}

async function openHistory(sessionId) {
  state.sessionId = sessionId;
  localStorage.setItem("scout_session", sessionId);
  showView("home");
  $("#homeLanding").classList.add("hidden");
  $("#chatThread").classList.remove("hidden");
  const box = $("#threadMessages");
  box.innerHTML = "";
  try {
    const data = await api(`/api/chat/history/${encodeURIComponent(sessionId)}`);
    for (const t of data.turns || []) {
      appendBubble(t.role === "user" ? "user" : "bot", t.content, t.sources || []);
    }
  } catch (_) {}
}

async function ask(raw) {
  const message = (raw || "").trim();
  if (!message) return;
  showView("home");
  $("#homeLanding").classList.add("hidden");
  $("#chatThread").classList.remove("hidden");
  $("#homeInput").value = "";
  $("#threadInput").value = "";

  appendBubble("user", message);
  const typing = document.createElement("div");
  typing.className = "bubble bot typing";
  typing.textContent = "Recherche et scraping en cours...";
  $("#threadMessages").appendChild(typing);
  $("#threadMessages").scrollTop = $("#threadMessages").scrollHeight;

  try {
    const res = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, session_id: state.sessionId }),
    });
    typing.remove();
    if (res.error && !res.reply) {
      appendBubble("bot", `⚠️ ${res.error}`);
    } else {
      appendBubble("bot", res.reply || "Recherche complétée.", res.sources || res.offers || []);
    }
    if (res.watch?.watch?.slug) {
      const slug = res.watch.watch.slug;
      const link = document.createElement("div");
      link.className = "bubble bot";
      link.innerHTML = `<button class="primary" type="button">Ouvrir le dashboard ${esc(res.watch.watch.title)}</button>`;
      link.querySelector("button").addEventListener("click", () => openWatchDash(slug));
      $("#threadMessages").appendChild(link);
    }
    loadHistory();
    if (typeof loadWatches === "function") loadWatches();
  } catch (e) {
    typing.remove();
    console.error("[Chat] Request error:", e);
    const errMsg = (e && e.message && !e.message.includes("Failed to fetch"))
      ? `Erreur : ${e.message}`
      : "Erreur de connexion au serveur ou délai dépassé. Veuillez réessayer.";
    appendBubble("bot", errMsg);
  }
}

// Ouvre une URL externe dans un nouvel onglet — ne fait RIEN si l'URL est vide/#/interne.
// (window.open("#") est ce qui provoquait l'ouverture d'un duplicata de l'accueil.)
function openExternal(url) {
  const safe = safeUrl(url);
  if (!safe || safe === "#") return;
  window.open(safe, "_blank", "noopener");
}
window.openExternal = openExternal;

/* --- Espace Actualités : synthèses IA (veille 15 min) --- */
function newsRelTime(iso) {
  try {
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return "à l'instant";
    if (s < 3600) return `il y a ${Math.floor(s / 60)} min`;
    if (s < 86400) return `il y a ${Math.floor(s / 3600)} h`;
    return `il y a ${Math.floor(s / 86400)} j`;
  } catch (_) { return ""; }
}

function newsThumb(t) {
  const img = safeUrl(t.image || "");
  if (img && img !== "#") {
    return `<img class="news-thumb" src="${esc(img)}" alt="" loading="lazy" referrerpolicy="no-referrer" decoding="async">`;
  }
  const first = (t.sources && t.sources[0] && (t.sources[0].source || t.sources[0].title)) || t.title || "?";
  const initials = srcInitials({ source: first });
  return `<div class="news-thumb-fallback" aria-hidden="true">${esc(initials)}</div>`;
}

function newsCardHtml(t) {
  const ready = t.summary_status === "ready";
  const extract = ready
    ? esc(String(t.summary_md || "").replace(/[#*`\[\]]/g, "").replace(/\s+/g, " ").trim().slice(0, 220))
    : "";
  const status = ready
    ? `<span class="badge-ia">✦ Synthèse IA</span>`
    : `<span class="news-pending">Synthèse en cours…</span>`;
  return `
    ${newsThumb(t)}
    <div class="news-body">
      <h3>${esc(t.title || "Sujet en cours d'analyse")}</h3>
      ${ready ? `<p class="news-extract">${extract}…</p>` : ""}
      <div class="news-meta">
        ${status}
        <span>${esc(String(t.source_count ?? (t.sources ? t.sources.length : 0)))} sources</span>
        <span>·</span>
        <span>${esc(newsRelTime(t.first_seen || t.last_seen))}</span>
      </div>
    </div>`;
}

async function newsFetchPage(offset) {
  const cat = state.news.category || "";
  const q = cat ? `&category=${encodeURIComponent(cat)}` : "";
  const res = await api(`/api/news/digest?limit=20&offset=${encodeURIComponent(offset)}${q}`);
  if (res && res.error) throw new Error(res.error);
  const items = Array.isArray(res.items) ? res.items : [];
  return { items, total: Number(res.total ?? items.length) };
}

function renderNewsPage(items, { prepend = false } = {}) {
  const list = $("#newsList");
  if (!list) return;
  const frag = document.createDocumentFragment();
  for (const t of items) {
    const card = document.createElement("article");
    card.className = "news-card";
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.dataset.topicId = String(t.id ?? "");
    card.innerHTML = newsCardHtml(t);
    card.addEventListener("click", () => openNewsArticle(t.id));
    card.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openNewsArticle(t.id); }
    });
    frag.appendChild(card);
  }
  if (prepend) list.prepend(frag); else list.appendChild(frag);
}

async function loadNews({ reset = false } = {}) {
  const list = $("#newsList");
  if (!list || state.news.loading) return;
  state.news.loading = true;
  try {
    if (reset) {
      state.news.offset = 0;
      list.innerHTML = "";
    }
    const { items, total } = await newsFetchPage(state.news.offset);
    state.news.total = total;
    const fresh = state.news.knownIds === null
      ? items
      : items.filter((t) => !state.news.knownIds.has(String(t.id)));
    if (state.news.knownIds === null) state.news.knownIds = new Set();
    for (const t of items) state.news.knownIds.add(String(t.id));
    renderNewsPage(items);
    state.news.offset += items.length;
    if (!list.children.length) {
      list.innerHTML = '<p class="muted" style="text-align:center;padding:30px 0;">Aucune actualité pour l\'instant — la première veille arrive dans ~15 minutes.</p>';
    }
    if (reset && fresh.length && state.news.items.length) {
      const banner = $("#newsBanner");
      if (banner) {
        banner.classList.remove("hidden");
        $("#btnNewsBanner").textContent = `${fresh.length} nouveau${fresh.length > 1 ? "x" : ""} sujet${fresh.length > 1 ? "s" : ""} — afficher`;
      }
    }
    state.news.items = reset ? items : state.news.items.concat(items);
    setupNewsInfiniteScroll();
  } catch (e) {
    if (!$("#newsList").children.length) {
      $("#newsList").innerHTML = '<p class="muted" style="text-align:center;padding:30px 0;">Actualités indisponibles pour le moment.</p>';
    }
  } finally {
    state.news.loading = false;
  }
}

function setupNewsInfiniteScroll() {
  if (state.news.io) return;
  const sentinel = document.createElement("div");
  sentinel.id = "newsSentinel";
  sentinel.style.height = "10px";
  $("#newsList").after(sentinel);
  state.news.io = new IntersectionObserver((entries) => {
    if (entries.some((en) => en.isIntersecting) && state.news.offset < state.news.total) {
      loadNews();
    }
  }, { rootMargin: "400px" });
  state.news.io.observe(sentinel);
}

function setNewsCategory(cat) {
  state.news.category = cat || "";
  state.news.knownIds = null;
  document.querySelectorAll(".news-cat").forEach((btn) => {
    const active = (btn.dataset.cat || "") === state.news.category;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  // Reset infinite scroll sentinel
  if (state.news.io) {
    try { state.news.io.disconnect(); } catch (_) {}
    state.news.io = null;
    const sent = $("#newsSentinel");
    if (sent) sent.remove();
  }
  loadNews({ reset: true });
}

function setupNewsCategoryTabs() {
  const wrap = $("#newsCats");
  if (!wrap || wrap.dataset.ready) return;
  wrap.dataset.ready = "1";
  wrap.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".news-cat");
    if (!btn) return;
    setNewsCategory(btn.dataset.cat || "");
  });
}

function openNewsFromChat(topicId) {
  if (!topicId) return;
  showView("news");
  openNewsArticle(topicId);
}
window.openNewsFromChat = openNewsFromChat;

async function openNewsArticle(topicId) {
  const wrap = $("#newsArticleView");
  if (!wrap) return;
  showView("news-article");
  wrap.innerHTML = '<div class="news-skeleton"></div>';
  try {
    const t = await api(`/api/news/topics/${encodeURIComponent(topicId)}`);
    if (t && t.error) throw new Error(t.error);
    const sources = Array.isArray(t.sources) ? t.sources : [];
    const srcRows = sources.map((s, i) => {
      const url = safeUrl(s.url);
      const thumb = chatSourceThumb(s);
      return `<div class="src-row">
        ${thumb}
        <div>
          <a class="t" href="${esc(url)}" target="_blank" rel="noopener">[${i + 1}] ${esc(s.title || url)}</a>
          <span class="n">${esc(s.source || s.provider || "source")} — ${esc(newsRelTime(s.published || t.first_seen))}</span>
        </div>
      </div>`;
    }).join("");
    wrap.innerHTML = `
      <div class="news-article">
        <button class="back" type="button" id="btnNewsBack">← Toutes les actualités</button>
        <h1>${esc(t.title || "")}</h1>
        <div class="article-meta">
          <span class="badge-ia">✦ Synthèse IA Sentinelle</span>
          · ${esc(String(t.source_count ?? sources.length))} sources · ${esc(newsRelTime(t.first_seen || t.last_seen))}
        </div>
        <div class="article-body">${mdToHtml(t.summary_md || "Synthèse en cours de rédaction…")}</div>
        ${sources.length ? `<div class="news-sources"><h3>Sources utilisées (${sources.length})</h3>${srcRows}</div>` : ""}
      </div>`;
    $("#btnNewsBack").addEventListener("click", () => {
      showView("news");
      wrap.innerHTML = "";
    });
  } catch (e) {
    wrap.innerHTML = `<div class="news-article"><p class="muted">Sujet indisponible : ${esc(e.message || "erreur")}</p></div>`;
  }
}

async function refreshNewsManual() {
  const btn = $("#btnNewsRefresh");
  if (!btn) return;
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "Actualisation…";
  try {
    await api("/api/news/refresh", { method: "POST", body: "{}" });
  } catch (_) {}
  setTimeout(() => {
    btn.disabled = false;
    btn.textContent = original;
    loadNews({ reset: true });
  }, 3000);
}

function startNewsPolling() {
  if (state.news.pollTimer) return;
  state.news.pollTimer = setInterval(async () => {
    if (document.hidden || state.news.loading) return;
    try {
      const { items } = await newsFetchPage(0);
      const fresh = items.filter((t) => !state.news.knownIds.has(String(t.id)));
      if (fresh.length) {
        const banner = $("#newsBanner");
        if (banner && $("#view-news") && $("#view-news").classList.contains("active")) {
          banner.classList.remove("hidden");
          $("#btnNewsBanner").textContent = `${fresh.length} nouveau${fresh.length > 1 ? "x" : ""} sujet${fresh.length > 1 ? "s" : ""} — afficher`;
        } else {
          for (const t of fresh) state.news.knownIds.add(String(t.id));
          renderNewsPage(fresh, { prepend: true });
          state.news.items = fresh.concat(state.news.items);
        }
      }
    } catch (_) {}
  }, 60000);
}

function srcInitials(s) {
  const base = s.source || s.provider || s.title || "?";
  const words = String(base).replace(/[^\p{L}\p{N} ]/gu, " ").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

// Vignette image si fournie, sinon fallback initiale du média sur fond token.
function chatSourceThumb(s) {
  const img = safeUrl(s.image || "");
  const initials = esc(srcInitials(s));
  const alt = esc(`Vignette — ${s.source || s.provider || s.title || "source"}`);
  if (!img || img === "#") {
    return `<div class="chat-src-fallback" aria-hidden="true">${initials}</div>`;
  }
  return `<img class="chat-src-thumb" src="${esc(img)}" alt="${alt}" loading="lazy"
    referrerpolicy="no-referrer" decoding="async">`;
}

// Strip de sources façon Perplexity : cartes horizontales scrollables + bouton
// « Afficher plus » qui révèle la liste détaillée. Code défensif : champs absents OK.
function renderChatSources(sources) {
  const strip = document.createElement("div");
  strip.className = "chat-sources-strip";

  const track = document.createElement("div");
  track.className = "chat-strip-track";

  for (const s of sources.slice(0, 12)) {
    const url = safeUrl(s.url);
    const hasTopic = s.topic_id != null && s.topic_id !== "";
    if ((!url || url === "#") && !hasTopic) continue;

    const card = document.createElement(hasTopic ? "div" : "a");
    card.className = "chat-src-card";
    if (!hasTopic) {
      card.href = url;
      card.target = "_blank";
      card.rel = "noopener";
    }
    card.innerHTML =
      chatSourceThumb(s) +
      `<span class="chat-src-title">${esc(s.title || url || "Source")}</span>` +
      `<span class="chat-src-name">${esc(s.source || s.provider || "source")}</span>` +
      (hasTopic
        ? `<button type="button" class="chat-news-link" data-topic-id="${esc(String(s.topic_id))}">Lire dans Actualités</button>`
        : "");
    if (hasTopic) {
      const btn = card.querySelector(".chat-news-link");
      if (btn) {
        btn.addEventListener("click", (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          openNewsFromChat(s.topic_id);
        });
      }
      if (url && url !== "#") {
        card.style.cursor = "pointer";
        card.addEventListener("click", (ev) => {
          if (ev.target.closest(".chat-news-link")) return;
          window.open(url, "_blank", "noopener");
        });
      }
    }
    track.appendChild(card);
  }
  strip.appendChild(track);

  if (track.children.length) {
    const wrap = document.createElement("div");
    wrap.className = "chat-more-wrap";
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "chat-more-toggle";
    toggle.innerHTML = `Afficher plus <span class="chevron">▾</span>`;
    const details = document.createElement("div");
    details.className = "chat-more-details";
    details.hidden = true;
    for (const s of sources) {
      const url = safeUrl(s.url);
      const hasTopic = s.topic_id != null && s.topic_id !== "";
      if ((!url || url === "#") && !hasTopic) continue;
      const row = document.createElement("div");
      row.className = "chat-detail-row";
      row.innerHTML =
        `<span class="chat-src-title">${esc(s.title || url || "Source")}</span>` +
        (s.description ? `<span class="chat-src-desc">${esc(String(s.description).slice(0, 200))}</span>` : "") +
        `<span class="chat-src-name">${esc(s.source || s.provider || "source")}</span>` +
        (hasTopic
          ? `<button type="button" class="chat-news-link" data-topic-id="${esc(String(s.topic_id))}">Lire dans Actualités</button>`
          : (url && url !== "#"
            ? `<a class="chat-src-ext" href="${esc(url)}" target="_blank" rel="noopener">Ouvrir</a>`
            : ""));
      if (hasTopic) {
        const btn = row.querySelector(".chat-news-link");
        if (btn) {
          btn.addEventListener("click", (ev) => {
            ev.preventDefault();
            openNewsFromChat(s.topic_id);
          });
        }
      }
      details.appendChild(row);
    }
    toggle.addEventListener("click", () => {
      const open = !details.hidden;
      details.hidden = open;
      toggle.classList.toggle("open", !open);
      toggle.firstChild.textContent = open ? "Afficher plus " : "Afficher moins ";
    });
    wrap.appendChild(toggle);
    wrap.appendChild(details);
    strip.appendChild(wrap);
  }
  return strip;
}

function appendBubble(role, content, sources = []) {
  const box = $("#threadMessages");
  const el = document.createElement("div");
  el.className = "bubble " + (role === "user" ? "user" : "bot");
  // Toujours passer par mdToHtml côté bot : listes, gras et liens cliquables garantis.
  let html = role === "user" ? esc(content) : mdToHtml(content);
  el.innerHTML = html;
  if (role !== "user" && Array.isArray(sources) && sources.length) {
    el.appendChild(renderChatSources(sources));
  }
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
}

/* --- Watches / dashboards --- */
async function loadWatches() {
  const grid = $("#watchesList");
  if (!grid) return;
  try {
    const list = await api("/api/watches");
    grid.innerHTML =
      (list || [])
        .map((w) => {
          const meta = `${esc(w.kind)}${w.ticker ? " · " + esc(w.ticker) : ""}`;
          return `<article class="discover-card" data-wslug="${esc(w.slug)}">
            <div class="meta">${meta}</div>
            <h3>${esc(w.title)}</h3>
            <p>${esc(w.query)}</p>
            <p class="muted small">MAJ : ${esc(w.last_refreshed_at || "jamais")}</p>
          </article>`;
        })
        .join("") || '<p class="muted">Aucun suivi. Crée-en un ou dis « suis SpaceX » dans la recherche.</p>';
    grid.querySelectorAll(".discover-card").forEach((card) => {
      card.addEventListener("click", () => openWatchDash(card.dataset.wslug));
    });
  } catch (_) {
    grid.innerHTML = '<p class="muted">Erreur chargement suivis.</p>';
  }
}

async function createWatchFromForm(e) {
  e?.preventDefault();
  const title = ($("#watchTitle")?.value || "").trim();
  if (!title) return;
  const body = {
    title,
    kind: $("#watchKind")?.value || null,
    ticker: ($("#watchTicker")?.value || "").trim() || null,
    refresh: true,
  };
  try {
    const w = await api("/api/watches", { method: "POST", body: JSON.stringify(body) });
    $("#watchTitle").value = "";
    $("#watchTicker").value = "";
    await loadWatches();
    if (w.slug) openWatchDash(w.slug);
  } catch (_) {
    alert("Échec création du suivi");
  }
}

async function openWatchDash(slug) {
  state.currentWatchSlug = slug;
  showView("watch-dash");
  document.querySelectorAll(".nav-item").forEach((n) => {
    n.classList.toggle("active", n.dataset.view === "watches");
  });
  await renderWatchDashboard(slug);
  setupWatchAutoRefresh();
}

function dedupeChartPoints(rows) {
  const out = [];
  let lastT = null;
  for (const r of rows) {
    if (!r || r.time == null || Number.isNaN(r.time)) continue;
    if (lastT != null && r.time <= lastT) continue;
    out.push(r);
    lastT = r.time;
  }
  return out;
}

function toChartTime(ts) {
  const ms = new Date(ts).getTime();
  if (Number.isNaN(ms)) return null;
  // Business day string for daily-ish bars improves Lightweight Charts stability
  return Math.floor(ms / 1000);
}

async function loadWatchLiveChart() {
  const ticker = state.currentTicker;
  const host = $("#watchPriceChart");
  const empty = $("#watchPriceEmpty");
  if (!ticker || !host) {
    empty?.classList.remove("hidden");
    if (empty) empty.textContent = "Pas de ticker / pas encore de données de prix.";
    return;
  }
  if (empty) {
    empty.classList.remove("hidden");
    empty.textContent = "Chargement du graphique…";
  }
  const range = $("#watchChartRange")?.value || "3mo";
  let chart;
  try {
    chart = await api(
      `/api/market/chart?symbol=${encodeURIComponent(ticker)}&range=${encodeURIComponent(range)}&force=true`
    );
    if (chart?.detail && !chart?.points) {
      throw new Error(typeof chart.detail === "string" ? chart.detail : "chart API error");
    }
  } catch (e) {
    console.warn("[chart] fetch", e);
    if (empty) {
      empty.textContent = "Erreur lors du chargement du cours — réessaie Actualiser.";
      empty.classList.remove("hidden");
    }
    return;
  }
  const points = chart?.points || [];
  if (!points.length) {
    if (empty) {
      empty.textContent = `Aucune donnée de prix pour ${ticker} (source: ${chart?.source || "none"}).`;
      empty.classList.remove("hidden");
    }
    return;
  }

  const srcEl = $("#watchChartSource");
  if (srcEl) srcEl.textContent = `· ${chart.source || ""} · ${chart.latency || ""}`;
  const note = $("#watchMarketNote");
  if (note && chart.delay_note) note.textContent = chart.delay_note;

  if (window.LightweightCharts) {
    try {
      renderLwChart(host, points, chart);
      empty?.classList.add("hidden");
      return;
    } catch (err) {
      console.warn("[chart] LightweightCharts échec, fallback Chart.js", err);
    }
  }
  try {
    renderChartJsFallback(host, points, ticker);
    empty?.classList.add("hidden");
  } catch (err) {
    console.warn("[chart] Chart.js fallback échec", err);
    if (empty) {
      empty.textContent = "Impossible d'afficher le graphique.";
      empty.classList.remove("hidden");
    }
  }
}

function renderLwChart(host, points, chart) {
  if (state.lwChart) {
    try { state.lwChart.remove(); } catch (_) {}
    state.lwChart = null;
    state.lwSeries = null;
    state.lwSma = null;
  }
  host.innerHTML = "";
  const isDark = state.theme === "dark";
  const width = Math.max(host.clientWidth || host.parentElement?.clientWidth || 640, 320);
  state.lwChart = LightweightCharts.createChart(host, {
    width,
    height: 280,
    layout: {
      background: { color: isDark ? "#292524" : "#FAF7F2" },
      textColor: isDark ? "#E7E5E4" : "#1C1917",
    },
    grid: {
      vertLines: { color: isDark ? "rgba(168,162,158,.12)" : "rgba(28,25,23,.08)" },
      horzLines: { color: isDark ? "rgba(168,162,158,.12)" : "rgba(28,25,23,.08)" },
    },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
  });

  const mapped = dedupeChartPoints(
    points
      .map((p) => {
        const time = toChartTime(p.ts);
        if (time == null) return null;
        return {
          time,
          open: Number(p.open ?? p.price),
          high: Number(p.high ?? p.price),
          low: Number(p.low ?? p.price),
          close: Number(p.close ?? p.price),
          value: Number(p.price),
        };
      })
      .filter(Boolean)
  );
  if (!mapped.length) throw new Error("no mapped points");

  const hasOHLC = mapped.every(
    (p) =>
      Number.isFinite(p.open) &&
      Number.isFinite(p.high) &&
      Number.isFinite(p.low) &&
      Number.isFinite(p.close) &&
      p.high >= p.low
  );

  if (hasOHLC && mapped.length > 2) {
    state.lwSeries = state.lwChart.addCandlestickSeries({
      upColor: "#16A34A",
      downColor: "#DC2626",
      borderVisible: false,
      wickUpColor: "#16A34A",
      wickDownColor: "#DC2626",
    });
    state.lwSeries.setData(mapped.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
  } else {
    state.lwSeries = state.lwChart.addAreaSeries({
      lineColor: "#D97706",
      topColor: "rgba(217,119,6,.35)",
      bottomColor: "rgba(217,119,6,.02)",
    });
    state.lwSeries.setData(mapped.map(({ time, value, close }) => ({ time, value: value ?? close })));
  }

  if (chart.sma20 && chart.sma20.some((x) => x != null)) {
    state.lwSma = state.lwChart.addLineSeries({ color: "#78716C", lineWidth: 1 });
    const smaData = [];
    for (let i = 0; i < points.length; i++) {
      if (chart.sma20[i] == null) continue;
      const time = toChartTime(points[i].ts);
      if (time == null) continue;
      smaData.push({ time, value: chart.sma20[i] });
    }
    state.lwSma.setData(dedupeChartPoints(smaData));
  }
  state.lwChart.timeScale().fitContent();
  // Resize when container gets a real width
  requestAnimationFrame(() => {
    if (state.lwChart && host.clientWidth) {
      state.lwChart.applyOptions({ width: host.clientWidth });
      state.lwChart.timeScale().fitContent();
    }
  });
}

function renderChartJsFallback(host, points, ticker) {
  if (state.lwChart) {
    try { state.lwChart.remove(); } catch (_) {}
    state.lwChart = null;
  }
  host.innerHTML = "";
  const canvas = document.createElement("canvas");
  canvas.height = 120;
  host.appendChild(canvas);
  if (state.priceChart) {
    state.priceChart.destroy();
    state.priceChart = null;
  }
  if (!window.Chart) throw new Error("Chart.js missing");
  state.priceChart = new Chart(canvas.getContext("2d"), {
    type: "line",
    data: {
      labels: points.map((p) => (p.ts || "").slice(0, 16)),
      datasets: [{
        label: ticker || "Prix",
        data: points.map((p) => p.price),
        borderColor: "#D97706",
        backgroundColor: "rgba(217,119,6,.15)",
        fill: true,
        tension: 0.25,
        pointRadius: 0,
      }],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { maxTicksLimit: 8, color: "#A8A29E" }, grid: { color: "rgba(168,162,158,.15)" } },
        y: { ticks: { color: "#A8A29E" }, grid: { color: "rgba(168,162,158,.15)" } },
      },
    },
  });
}

function setupWatchAutoRefresh() {
  if (state.watchRefreshTimer) {
    clearInterval(state.watchRefreshTimer);
    state.watchRefreshTimer = null;
  }
  stopMarketSSE();
  const on = $("#watchAutoRefresh")?.checked;
  if (!on || !state.currentWatchSlug) return;
  const ms = state.watchAssetClass === "crypto" ? 20 * 1000 : 10 * 60 * 1000;
  state.watchRefreshTimer = setInterval(() => {
    if (state.currentWatchSlug && $("#view-watch-dash")?.classList.contains("active")) {
      if (state.watchAssetClass === "crypto") loadWatchLiveQuote();
      else refreshCurrentWatch(true);
    }
  }, ms);
  if (state.watchAssetClass === "crypto") startMarketSSE();
}

function stopMarketSSE() {
  if (state.marketSSE) {
    try { state.marketSSE.close(); } catch (_) {}
    state.marketSSE = null;
  }
}

function startMarketSSE() {
  stopMarketSSE();
  const ticker = state.currentTicker;
  if (!ticker || !window.EventSource) return;
  const es = new EventSource(`/api/market/stream/${encodeURIComponent(ticker)}?interval_sec=3`);
  state.marketSSE = es;
  es.onmessage = (ev) => {
    try {
      const q = JSON.parse(ev.data);
      if (q.price != null) {
        applyLiveQuote(q);
        if (state.lwSeries && typeof state.lwSeries.update === "function") {
          const t = Math.floor(Date.now() / 1000);
          try {
            // Area series uses value; candle uses close — try both safely
            state.lwSeries.update({ time: t, value: q.price, close: q.price });
          } catch (_) {}
        }
      }
    } catch (_) {}
  };
}

async function loadWatchLiveQuote() {
  const ticker = state.currentTicker;
  if (!ticker) return;
  try {
    const q = await api(`/api/market/quote?symbol=${encodeURIComponent(ticker)}&force=1`);
    if (q && !q.error) applyLiveQuote(q);
  } catch (_) {}
}

function applyLiveQuote(q) {
  const lastPx = q.price != null ? Number(q.price).toFixed(q.price < 10 ? 4 : 2) : "—";
  const ch = q.change_pct_24h != null ? ` · 24h ${Number(q.change_pct_24h).toFixed(2)}%` : "";
  const src = q.source || "?";
  $("#watchDashMeta").textContent =
    `${state.watchKind || ""}${state.currentTicker ? " · " + state.currentTicker : ""} · live ${lastPx}${ch} · ${src}`;
  const note = $("#watchMarketNote");
  if (note && q.delay_note) note.textContent = q.delay_note;
  const srcEl = $("#watchChartSource");
  if (srcEl) srcEl.textContent = `· ${src} · ${q.latency || ""}`;
}

async function refreshCurrentWatch(silent = false) {
  if (!state.currentWatchSlug) return;
  if (!silent) $("#watchDashMeta").textContent = "Actualisation...";
  await api(`/api/watches/${encodeURIComponent(state.currentWatchSlug)}/refresh`, {
    method: "POST",
    body: "{}",
  });
  await renderWatchDashboard(state.currentWatchSlug);
}

async function analyzeCurrentWatch(kind) {
  if (!state.currentWatchSlug) return;
  $("#watchInsights").textContent = "Génération de l'insight...";
  const res = await api(`/api/watches/${encodeURIComponent(state.currentWatchSlug)}/analyze`, {
    method: "POST",
    body: JSON.stringify({ kind }),
  });
  const content = res.insight?.content || "Pas d'insight.";
  $("#watchInsights").innerHTML = mdToHtml(content);
  if (res.dashboard) paintWatchDashboard(res.dashboard);
}

async function renderWatchDashboard(slug) {
  try {
    const dash = await api(`/api/watches/${encodeURIComponent(slug)}`);
    paintWatchDashboard(dash);
  } catch (_) {
    $("#watchDashTitle").textContent = "Erreur";
  }
}

function paintWatchDashboard(dash) {
  const w = dash.watch || {};
  const stats = dash.stats || {};
  const market = dash.market || {};
  const quote = market.quote || {};
  state.currentTicker = w.ticker || null;
  state.watchKind = w.kind || "";
  state.watchAssetClass = market.asset_class || (w.kind === "crypto" ? "crypto" : "equity");

  $("#watchDashTitle").textContent = w.title || "Dashboard";
  const lastPx = (quote.price ?? stats.last_price);
  const lastStr = lastPx != null ? Number(lastPx).toFixed(Number(lastPx) < 10 ? 4 : 2) : "—";
  const src = quote.source || market.price_source || "market";
  const lat = quote.latency || market.latency || "";
  $("#watchDashMeta").textContent =
    `${w.kind || ""}${w.ticker ? " · " + w.ticker : ""} · cotation ${lastStr} · ${src}${lat ? " (" + lat + ")" : ""} · refresh ${w.last_refreshed_at || "n/a"}`;
  $("#watchDashDisclaimer").textContent = dash.disclaimer || "";
  const note = $("#watchMarketNote");
  if (note) {
    note.textContent = quote.delay_note || market.delay_note ||
      "Yahoo (~15 min actions) · Binance near-realtime (crypto) · pas de tick-by-tick actions US gratuit.";
  }

  $("#watchKpis").innerHTML = [
    kpi(stats.events_count ?? 0, "Événements"),
    kpi(stats.prices_count ?? 0, "Points histo"),
    kpi(lastStr, "Dernier cours"),
    kpi(
      quote.change_pct_24h != null
        ? Number(quote.change_pct_24h).toFixed(2) + "%"
        : stats.price_change_pct != null
          ? stats.price_change_pct + "%"
          : "—",
      quote.change_pct_24h != null ? "Var. 24h" : "Var. période"
    ),
  ].join("");

  const tbody = $("#watchEventsTable tbody");
  tbody.innerHTML = (dash.events || [])
    .map((e) => {
      const title = e.url
        ? `<a href="${esc(safeUrl(e.url))}" target="_blank" rel="noopener">${esc(e.title)}</a>`
        : esc(e.title);
      return `<tr>
        <td>${esc(e.event_type || "news")}</td>
        <td>${title}<div class="muted small">${esc((e.summary || "").slice(0, 120))}</div></td>
        <td>${esc(e.provider || "")}</td>
        <td>${esc((e.published_at || e.created_at || "").slice(0, 25))}</td>
      </tr>`;
    })
    .join("") || `<tr><td colspan="4" class="muted">Aucun événement</td></tr>`;

  const insights = dash.insights || {};
  const prefer = insights.analysis || insights.summary || insights.trading;
  if (prefer?.content) $("#watchInsights").innerHTML = mdToHtml(prefer.content);

  // Prix : TradingView Lightweight Charts (live API) ; fallback points stockés
  if (w.ticker) {
    loadWatchLiveChart();
    setupWatchAutoRefresh();
  } else {
    const empty = $("#watchPriceEmpty");
    empty?.classList.remove("hidden");
    const host = $("#watchPriceChart");
    if (host) host.innerHTML = "";
    stopMarketSSE();
  }

  const types = dash.event_types || {};
  if (window.Chart) {
    const ctx2 = $("#watchTypeChart").getContext("2d");
    if (state.typeChart) state.typeChart.destroy();
    state.typeChart = new Chart(ctx2, {
      type: "doughnut",
      data: {
        labels: Object.keys(types),
        datasets: [{
          data: Object.values(types),
          backgroundColor: ["#D97706", "#F59E0B", "#B45309", "#78716C", "#A8A29E"],
        }],
      },
      options: { plugins: { legend: { position: "bottom", labels: { color: "#A8A29E" } } } },
    });
  }
}

function kpi(v, label) {
  return `<div class="kpi"><b>${esc(String(v))}</b><span>${esc(label)}</span></div>`;
}

const _usdFmt = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "USD" });
function fmtUsd(n) {
  const num = Number(n);
  return Number.isFinite(num) ? _usdFmt.format(num) : String(n);
}

/* --- tools legacy --- */
async function loadStats() {
  try {
    const s = await api("/api/stats");
    $("#stats").innerHTML = [
      stat(s.total, "Offres"),
      stat((s.by_tier || {}).verified || 0, "Vérifiées"),
      stat(s.favorites || 0, "Favoris"),
      stat(s.runs || 0, "Runs"),
    ].join("");
    fillSelect("#fNetwork", Object.keys(s.by_network || {}));
    fillSelect("#fType", Object.keys(s.by_type || {}));
  } catch (_) {}
}
const stat = (n, l) => `<div class="stat"><b>${n}</b><span>${l}</span></div>`;
function fillSelect(sel, keys) {
  const el = $(sel);
  if (!el) return;
  const cur = el.value;
  el.innerHTML = `<option value="">Tous</option>` + keys.map((k) => `<option value="${k}">${k}</option>`).join("");
  el.value = cur;
}

async function loadOffers() {
  try {
    state.offers = await api("/api/offers?limit=200");
    renderOffers();
  } catch (_) {}
}

function renderOffers() {
  const list = $("#offerList");
  if (!list) return;
  const q = ($("#search")?.value || "").toLowerCase();
  const tier = $("#fTier")?.value || "";
  const network = $("#fNetwork")?.value || "";
  const type = $("#fType")?.value || "";
  const filtered = state.offers.filter((o) => {
    if (tier && o.credibility_tier !== tier) return false;
    if (network && o.network !== network) return false;
    if (type && o.offer_type !== type) return false;
    if (q && !(o.title || "").toLowerCase().includes(q) && !(o.description || "").toLowerCase().includes(q)) return false;
    return true;
  });
  list.innerHTML = filtered
    .slice(0, 60)
    .map(
      (o) => `<div class="card"><a href="${esc(safeUrl(o.url))}" target="_blank" rel="noopener"><strong>${esc(o.title)}</strong></a>
      <div class="muted small">${esc(o.provider)} · ${esc(o.offer_type)} · ${esc(o.credibility_tier || "")}</div>
      <p class="muted small">${esc((o.description || "").slice(0, 140))}</p></div>`
    )
    .join("");
}

async function loadAgents() {
  try {
    const a = await api("/api/agents");
    state.agents = Object.fromEntries((a || []).map((k) => [k, { status: "idle" }]));
    $("#agentStatus").innerHTML = Object.keys(state.agents)
      .map((k) => `<div class="agent"><span>${esc(k)}</span><span class="muted">idle</span></div>`)
      .join("");
  } catch (_) {}
}

async function runAll() {
  $("#liveDot")?.classList.add("running");
  try {
    await api("/api/run", { method: "POST", body: "{}" });
  } catch (_) {}
  setTimeout(() => {
    $("#liveDot")?.classList.remove("running");
    $("#liveDot")?.classList.add("done");
    loadOffers();
    loadStats();
  }, 2000);
}

function connectSSE() {
  try {
    const es = new EventSource("/api/events");
    es.onmessage = () => {};
  } catch (_) {}
}

/* --- medical pro & pharmacopée --- */
async function loadMedical() {
  try {
    const s = await api("/api/medical/stats");
    $("#medStats").textContent = `${s.total_records || 0} dossiers cliniques · ${s.total_plants || 0} plantes répertoriées · base ${s.backend || "active"}`;
  } catch (_) {}
  loadMedicalRecords();
}

async function loadMedicalRecords() {
  const grid = $("#medGrid");
  if (!grid) return;
  grid.innerHTML = '<p class="muted">Recherche des dossiers cliniques en cours...</p>';
  const search = ($("#medSearch")?.value || "").trim();
  const country = ($("#medFilterCountry")?.value || "").trim();
  const phase = ($("#medFilterPhase")?.value || "").trim();
  const status = ($("#medFilterStatus")?.value || "").trim();

  try {
    const params = new URLSearchParams();
    if (search) params.set("q", search);
    if (country) params.set("country", country);
    if (phase) params.set("phase", phase);
    if (status) params.set("status", status);

    const url = `/api/medical/search?${params.toString()}`;
    const res = await api(url);
    const records = res.results || res || [];
    
    if (!records.length) {
      grid.innerHTML = '<p class="muted">Aucun essai clinique correspondant aux filtres.</p>';
      return;
    }

    grid.innerHTML = records
      .map((r) => {
        const payload = encodeURIComponent(JSON.stringify(r));
        const badgeColor = r.status === "RECRUITING" ? "var(--accent)" : "var(--muted)";
        return `<article class="discover-card" data-payload="${payload}">
          <div class="meta" style="display:flex;justify-content:space-between;">
            <span>${esc(r.source || "clinicaltrials")} · ${esc(r.country || "Afrique")}</span>
            <span style="color:${badgeColor};font-weight:600;">${esc(r.phase || r.status || "")}</span>
          </div>
          <h3>${esc(r.title)}</h3>
          <p>${esc((r.ai_cheat_sheet || r.summary || "").slice(0, 150))}…</p>
          <div style="margin-top:8px;font-size:0.78rem;color:var(--accent2);">Voir la fiche de synthèse →</div>
        </article>`;
      })
      .join("");
    grid.querySelectorAll(".discover-card[data-payload]").forEach((card) => {
      card.addEventListener("click", () => openMedicalModal(card.dataset.payload));
    });
  } catch (_) {
    grid.innerHTML = '<p class="muted">Erreur lors de la récupération des données médicales.</p>';
  }
}

async function loadMedicalPlants() {
  const grid = $("#medPlantsGrid");
  if (!grid) return;
  grid.innerHTML = '<p class="muted">Chargement de la pharmacopée africaine...</p>';
  try {
    const res = await api("/api/medical/plants");
    const plants = res.plants || res || [];
    if (!plants.length) {
      grid.innerHTML = '<p class="muted">Aucune plante trouvée.</p>';
      return;
    }
    grid.innerHTML = plants
      .map((p) => {
        return `<article class="discover-card">
          <div class="meta">🌿 Pharmacopée Traditionnelle</div>
          <h3>${esc(p.name)} <em style="font-size:0.85em;color:var(--muted)">(${esc(p.scientific_name || "")})</em></h3>
          <p><strong>Indications :</strong> ${esc(p.indications || "Non renseigné")}</p>
          <p class="small muted"><strong>Principes actifs :</strong> ${esc(p.active_compounds || "N/A")}</p>
        </article>`;
      })
      .join("");
  } catch (_) {
    grid.innerHTML = '<p class="muted">Erreur lors du chargement des plantes.</p>';
  }
}

let currentRecord = null;
window.openMedicalModal = function (encoded) {
  const record = JSON.parse(decodeURIComponent(encoded));
  currentRecord = record;
  $("#medicalModal").classList.remove("hidden");
  $("#medTitle").textContent = record.title || "";
  $("#medStatusBadge").innerHTML = `<span class="muted">${esc(record.status || "")} · ${esc(record.nct_id || record.id || "")}</span>`;
  $("#medCheatSheetContent").innerHTML = mdToHtml(record.ai_cheat_sheet || record.summary || "Pas de fiche.");
  $("#medLabName").textContent = record.sponsor ? `Investigateur / Sponsor : ${record.sponsor}` : "";
  $("#medCityCountry").textContent = record.city || record.country ? `Localisation : ${record.city || ""} ${record.country || ""}` : "";
  $("#medTrialUrl").innerHTML = record.url
    ? `<a href="${esc(safeUrl(record.url))}" target="_blank" rel="noopener">Consulter l'essai officiel en ligne ↗</a>`
    : "";
};

async function generateMedicalEmail() {
  if (!currentRecord?.id) return;
  const caseSummary = ($("#medCaseSummary")?.value || "").trim() || "Demande d'informations sur l'essai clinique";
  try {
    const res = await api(`/api/medical/${encodeURIComponent(currentRecord.id)}/contact-email`, {
      method: "POST",
      body: JSON.stringify({ case_summary: caseSummary }),
    });
    // location.href plutôt que window.open("_blank") : évite un onglet vide
    // lorsque le client mail prend le relais.
    if (res.mailto) window.location.href = safeUrl(res.mailto);
  } catch (_) {}
}

/* --- Profil --- */
async function loadProfile() {
  try {
    const p = await api("/api/profile");
    $("#pfName").value = p.display_name || "";
    $("#pfSkills").value = (p.skills || []).join(", ");
    $("#pfInterests").value = (p.interests || []).join(", ");
    $("#pfCity").value = p.city || "";
    $("#pfCountry").value = p.country || "";
    $("#pfCompanies").value = (p.preferred_companies || []).join(", ");
    $("#pfTrading").value = p.trading_level || "beginner";
  } catch (_) {}
}

async function saveProfile(e) {
  e?.preventDefault();
  const body = {
    display_name: $("#pfName").value.trim(),
    skills: $("#pfSkills").value,
    interests: $("#pfInterests").value,
    city: $("#pfCity").value.trim(),
    country: $("#pfCountry").value.trim(),
    preferred_companies: $("#pfCompanies").value,
    trading_level: $("#pfTrading").value,
  };
  try {
    await api("/api/profile", { method: "PUT", body: JSON.stringify(body) });
    $("#profileSaved").textContent = "Profil enregistré.";
  } catch (_) {
    $("#profileSaved").textContent = "Échec enregistrement.";
  }
}

/* --- Opportunités --- */
async function loadOpportunitiesView() {
  await loadOppDigest();
  await loadOppGrid();
}

async function loadOppDigest() {
  const box = $("#oppDigest");
  if (!box) return;
  box.textContent = "Génération du digest…";
  try {
    const d = await api("/api/opportunities/digest");
    box.innerHTML = mdToHtml(d.message || "Pas de digest.");
  } catch (_) {
    box.textContent = "Impossible de charger le digest.";
  }
}

/* --- Opportunités --- */
async function loadOpportunitiesView() {
  await loadOppDigest();
  await loadOppGrid();
}

const OPP_TYPE_LABEL = {
  job: "Emploi",
  scholarship: "Bourse",
  travel: "Voyage",
  event: "Event",
  news: "Actu",
};

function oppInitials(item) {
  const base = (item.provider && item.provider !== "Inconnu" ? item.provider : item.title) || "?";
  const words = String(base).replace(/[^\p{L}\p{N} ]/gu, " ").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

function oppImageBlock(item) {
  const src = safeUrl(item.image_url || "");
  const initials = esc(oppInitials(item));
  const alt = esc(`Logo ou miniature — ${item.title || ""}`);
  if (!src || src === "#") {
    return `<div class="opp-thumb opp-thumb-fallback" aria-hidden="true">${initials}</div>`;
  }
  return `<img class="opp-thumb" src="${esc(src)}" alt="${alt}" loading="lazy"
    referrerpolicy="no-referrer" decoding="async"
    onerror="this.outerHTML='<div class=\\'opp-thumb opp-thumb-fallback\\' aria-hidden=\\'true\\'>${initials}</div>'">`;
}

function oppMetaLine(it) {
  const provider = it.provider && it.provider !== "Google News" ? it.provider : (it.source || "");
  return provider ? esc(provider) : "";
}

async function loadOppGrid() {
  const grid = $("#oppGrid");
  if (!grid) return;
  grid.innerHTML = '<p class="muted">Chargement…</p>';
  const q = ($("#oppSearch")?.value || "").trim();
  const city = ($("#oppCity")?.value || "").trim();
  const country = ($("#oppCountry")?.value || "").trim();
  const company = ($("#oppCompany")?.value || "").trim();
  let url;
  if (state.oppTab === "feed" && !q && !city && !country && !company) {
    url = "/api/opportunities/feed?limit=24";
  } else {
    const params = new URLSearchParams();
    if (state.oppTab && state.oppTab !== "feed") params.set("type", state.oppTab);
    if (q) params.set("q", q);
    if (city) params.set("city", city);
    if (country) params.set("country", country);
    if (company) params.set("company", company);
    params.set("limit", "40");
    url = "/api/opportunities?" + params.toString();
  }
  try {
    const data = await api(url);
    const items = data.items || [];
    grid.innerHTML =
      items
        .map((it) => {
          const payload = encodeURIComponent(JSON.stringify(it));
          const typeLabel = OPP_TYPE_LABEL[it.offer_type] || it.offer_type || "Info";
          const scoreBadge = Number.isFinite(Number(it.match_score))
            ? `<span class="opp-score" title="Score de correspondance avec ton profil">${esc(String(Math.round(Number(it.match_score))))}</span>`
            : "";
          const title = esc(it.title || "");
          const desc = esc(it.description || "");
          const meta = oppMetaLine(it);
          return `<article class="discover-card opp-card" data-payload="${payload}" tabindex="0" role="link" aria-label="${title}">
            ${oppImageBlock(it)}
            <div class="opp-body">
              <h3 class="opp-title">${title}</h3>
              ${desc ? `<p class="opp-desc">${desc}</p>` : ""}
              <div class="opp-meta-row">
                <span class="opp-type-badge">${esc(typeLabel)}</span>
                <span class="opp-provider">${meta || "&nbsp;"}</span>
                ${scoreBadge}
              </div>
            </div>
          </article>`;
        })
        .join("") || '<p class="muted">Aucune opportunité pour ces filtres.</p>';
    grid.querySelectorAll(".opp-card").forEach((card) => {
      card.addEventListener("click", () => {
        try {
          const it = JSON.parse(decodeURIComponent(card.dataset.payload));
          openExternal(it.url);   // no-op si url vide/# → plus de nouvel onglet vers l'accueil
        } catch (_) {}
      });
      card.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          card.click();
        }
      });
    });
  } catch (_) {
    grid.innerHTML = '<p class="muted">Erreur de chargement.</p>';
  }
}

/* --- Learn Trading & Prediction Markets --- */
let currentSelectedAsset = "GOOGL";
let currentTradeMode = "beginner";
let currentBetPayload = null;
let currentBetOutcome = "YES";
let currentBetPrice = 0.50;

function renderPolyOrderBook(book) {
  const wrap = $("#betOrderBook");
  const bidsEl = $("#betBids");
  const asksEl = $("#betAsks");
  if (!wrap || !bidsEl || !asksEl) return;
  const bids = (book && book.bids) || [];
  const asks = (book && book.asks) || [];
  if (!bids.length && !asks.length) {
    wrap.style.display = "none";
    return;
  }
  wrap.style.display = "block";
  bidsEl.innerHTML = bids.slice(0, 5).map((lvl) => {
    const p = Number(lvl.price);
    const s = Number(lvl.size);
    return `<div style="display:flex;justify-content:space-between;padding:2px 4px;background:rgba(16,185,129,0.06);border-radius:4px;"><span style="color:#10B981;font-weight:600;">${esc((p * 100).toFixed(1))}¢</span><span class="muted">${esc(Number.isFinite(s) ? s.toFixed(0) : "—")}</span></div>`;
  }).join("");
  asksEl.innerHTML = asks.slice(0, 5).map((lvl) => {
    const p = Number(lvl.price);
    const s = Number(lvl.size);
    return `<div style="display:flex;justify-content:space-between;padding:2px 4px;background:rgba(239,68,68,0.06);border-radius:4px;"><span style="color:#EF4444;font-weight:600;">${esc((p * 100).toFixed(1))}¢</span><span class="muted">${esc(Number.isFinite(s) ? s.toFixed(0) : "—")}</span></div>`;
  }).join("");
}

async function openBetModal(payloadStr, outcome, price) {
  try {
    const item = JSON.parse(decodeURIComponent(payloadStr));
    currentBetPayload = item;
    currentBetOutcome = outcome;
    currentBetPrice = price;

    $("#betQuestion").textContent = item.title || item.question || "Marché";
    $("#betOutcomeBadge").textContent = outcome === "YES" ? "🟢 OUI (YES)" : "🔴 NON (NO)";
    $("#betOutcomeBadge").style.color = outcome === "YES" ? "#10B981" : "#EF4444";
    $("#betPriceDisplay").textContent = `${Math.round(price * 100)}¢ (${Number(price).toFixed(4)} USD)`;
    const srcEl = $("#betPriceSource");
    if (srcEl) srcEl.textContent = "Actualisation du prix CLOB…";
    $("#betMsg").textContent = "";
    renderPolyOrderBook(null);
    updateBetPayout();
    $("#betModal").classList.remove("hidden");

    const marketRef = item.id || item.slug || "";
    if (!marketRef) {
      if (srcEl) srcEl.textContent = "Pari impossible : ce marché n'a pas d'identifiant serveur.";
      return;
    }
    try {
      const detail = await api(`/api/predictions/market/${encodeURIComponent(marketRef)}`);
      if (detail && !detail.error) {
        currentBetPayload = { ...item, ...detail };
        const live =
          outcome === "YES"
            ? (detail.clob_yes_price ?? detail.yes_price)
            : (detail.clob_no_price ?? detail.no_price);
        if (Number.isFinite(Number(live)) && Number(live) > 0) {
          currentBetPrice = Number(live);
          $("#betPriceDisplay").textContent = `${Math.round(currentBetPrice * 100)}¢ (${currentBetPrice.toFixed(4)} USD)`;
          if (srcEl) srcEl.textContent = detail.clob_yes_price != null || detail.clob_no_price != null
            ? "Prix mid CLOB (serveur)."
            : "Prix Gamma (fallback serveur).";
        } else if (srcEl) {
          srcEl.textContent = "Prix liste — CLOB indisponible.";
        }
        renderPolyOrderBook(detail.order_book);
        updateBetPayout();
      } else if (srcEl) {
        srcEl.textContent = "Prix liste — détail marché indisponible.";
      }
    } catch (_) {
      if (srcEl) srcEl.textContent = "Prix liste — erreur réseau détail.";
    }
  } catch (e) {
    console.error("Error opening bet modal:", e);
  }
}

function updateBetPayout() {
  const amount = Number($("#betAmount")?.value || 50);
  const payout = currentBetPrice > 0 ? (amount / currentBetPrice).toFixed(2) : "0.00";
  const gain = (payout - amount).toFixed(2);
  $("#betPotentialGain").textContent = `$${payout} (Gain net : +$${gain})`;
}

async function submitBet() {
  if (!currentBetPayload) return;
  const amount = Number($("#betAmount").value);
  if (amount <= 0) return;

  $("#betMsg").textContent = "Envoi de la prise de position…";
  try {
    const res = await api("/api/predictions/bet", {
      method: "POST",
      body: JSON.stringify({
        market_id: currentBetPayload.id || "",
        market_slug: currentBetPayload.slug || currentBetPayload.url || "",
        question: currentBetPayload.title || currentBetPayload.question || "",
        outcome: currentBetOutcome,
        price: currentBetPrice,
        amount: amount,
      }),
    });
    if (res.error) {
      $("#betMsg").textContent = `⚠️ ${res.error}`;
      return;
    }
    const src = res.price_source ? ` · source ${res.price_source}` : "";
    $("#betMsg").textContent = `✅ Pari validé ! ${res.shares} parts @ ${Number(res.buy_price).toFixed(4)}${src}`;
    setTimeout(() => {
      $("#betModal").classList.add("hidden");
      loadLearn();
    }, 1000);
  } catch (e) {
    $("#betMsg").textContent = "Erreur lors de la validation du pari.";
  }
}

function switchTradeMode(mode) {
  currentTradeMode = mode;
  $("#tabModeBeginner")?.classList.toggle("active", mode === "beginner");
  $("#tabModePro")?.classList.toggle("active", mode === "pro");
  $("#tradeBeginnerContainer")?.classList.toggle("hidden", mode !== "beginner");
  $("#tradeProContainer")?.classList.toggle("hidden", mode !== "pro");
  
  api("/api/learn/level", { method: "POST", body: JSON.stringify({ level: mode }) });
  loadLearnChart();
}

function selectAsset(symbol, name) {
  currentSelectedAsset = symbol;
  if ($("#learnSymbol")) $("#learnSymbol").value = symbol;
  if ($("#beginnerAssetTitle")) {
    $("#beginnerAssetTitle").textContent = `Cours en direct : ${name} (${symbol})`;
  }
  loadLearnChart();
}

async function executeBeginnerSide(side) {
  const amount = Number($("#beginnerAmount").value || 500);
  const ticker = ($("#learnSymbol")?.value || "GOOGL").trim().toUpperCase();
  $("#beginnerTradeMsg").textContent = "Exécution en cours...";
  
  let price = 150.0;
  try {
    const q = await api(`/api/market/quote?symbol=${encodeURIComponent(ticker)}`);
    if (q && q.price) price = q.price;
  } catch (_) {}
  
  const qty = Number((amount / price).toFixed(4));
  const res = await api("/api/learn/orders", {
    method: "POST",
    body: JSON.stringify({
      symbol: ticker,
      side: side,
      qty: qty,
      order_type: "market",
    }),
  });
  
  if (res.error) {
    $("#beginnerTradeMsg").textContent = `⚠️ ${res.error}`;
    return;
  }
  $("#beginnerTradeMsg").textContent = `✅ Ordre ${side === 'buy' ? 'Achat' : 'Vente'} exécuté : ${qty} ${ticker} @ ${res.fill_price ?? price} $`;
  loadLearn();
}

async function closePosition(symbol) {
  if (!confirm(`Clôturer la position sur ${symbol} au cours du marché ?`)) return;
  try {
    const p = await api("/api/learn/portfolio");
    const pos = (p.positions || []).find(x => x.symbol === symbol);
    if (!pos) return;
    
    const side = pos.qty > 0 ? "sell" : "buy";
    const qty = Math.abs(pos.qty);
    await api("/api/learn/orders", {
      method: "POST",
      body: JSON.stringify({ symbol, side, qty, order_type: "market" }),
    });
    loadLearn();
  } catch (_) {}
}

function renderSimulatedOrderBook(lastPrice) {
  const base = lastPrice || 150.0;
  const bidsEl = $("#simulatedBids");
  const asksEl = $("#simulatedAsks");
  if (!bidsEl || !asksEl) return;
  
  let bidsHtml = "";
  let asksHtml = "";
  for (let i = 1; i <= 5; i++) {
    const bidP = (base * (1 - 0.0012 * i)).toFixed(2);
    const askP = (base * (1 + 0.0012 * i)).toFixed(2);
    const bidQty = (Math.random() * 40 + 5).toFixed(1);
    const askQty = (Math.random() * 40 + 5).toFixed(1);
    bidsHtml += `<div style="display:flex;justify-content:space-between;padding:2px 4px;background:rgba(16,185,129,0.06);border-radius:4px;"><span style="color:#10B981;font-weight:600;">$${bidP}</span><span class="muted">${bidQty}</span></div>`;
    asksHtml += `<div style="display:flex;justify-content:space-between;padding:2px 4px;background:rgba(239,68,68,0.06);border-radius:4px;"><span style="color:#EF4444;font-weight:600;">$${askP}</span><span class="muted">${askQty}</span></div>`;
  }
  bidsEl.innerHTML = bidsHtml;
  asksEl.innerHTML = asksHtml;
}

async function loadLearnChart() {
  const isBeginner = currentTradeMode === "beginner";
  const host = isBeginner ? $("#learnChartBeginner") : $("#learnChart");
  if (!host) return;
  
  const ticker = ($("#learnSymbol")?.value || "GOOGL").trim().toUpperCase();
  if (!ticker) return;
  const range = (isBeginner ? $("#learnChartRangeBeginner")?.value : $("#learnChartRange")?.value) || "1mo";
  host.innerHTML = `<p class="muted small">Chargement du cours de ${esc(ticker)}…</p>`;
  let chart;
  try {
    chart = await api(
      `/api/market/chart?symbol=${encodeURIComponent(ticker)}&range=${encodeURIComponent(range)}&force=true`
    );
    if (chart?.detail && !chart?.points) {
      throw new Error(typeof chart.detail === "string" ? chart.detail : "chart API error");
    }
  } catch (e) {
    host.innerHTML = `<p class="muted small">Erreur lors du chargement du cours de ${esc(ticker)} — réessaie Actualiser.</p>`;
    return;
  }
  const points = chart?.points || [];
  if (!points.length) {
    host.innerHTML = `<p class="muted small">Aucune donnée de prix pour ${esc(ticker)} (source: ${esc(chart?.source || "none")}).</p>`;
    return;
  }
  const srcEl = $("#learnChartSource");
  if (srcEl) srcEl.textContent = `· ${chart.source || ""} · ${chart.latency || ""} · ${chart.delay_note || ""}`;
  
  renderSimulatedOrderBook(points[points.length - 1]?.price);

  if (window.LightweightCharts) {
    try {
      renderLwChart(host, points, chart);
      return;
    } catch (err) {
      console.warn("[learn chart] LightweightCharts échec, fallback Chart.js", err);
    }
  }
  try {
    renderChartJsFallback(host, points, ticker);
  } catch (err) {
    host.innerHTML = `<p class="muted small">Impossible d'afficher le graphique.</p>`;
  }
}

async function loadLearn() {
  try {
    const p = await api("/api/learn/portfolio");
    paintLearn(p);
  } catch (_) {}
  loadLearnChart();
}

function paintLearn(p) {
  const acc = p.account || {};
  const positions = p.positions || [];
  const predictions = p.predictions || [];
  const fills = p.fills || [];
  const totalTrades = acc.trades_count || fills.length || 0;

  $("#learnKpis").innerHTML = [
    kpi(p.cash != null ? fmtUsd(p.cash) : "—", "Cash Virtuel"),
    kpi(p.equity != null ? fmtUsd(p.equity) : "—", "Valeur Totale (Equity)"),
    kpi(positions.length, "Positions Ouvertes"),
    kpi(predictions.length, "Paris Prédictions"),
    kpi(totalTrades, "Exécutions (Trades)"),
  ].join("");

  const tbody = $("#learnPosTable tbody");
  if (tbody) {
    tbody.innerHTML =
      positions
        .map(
          (x) => {
            const pnlVal = x.pnl != null ? Number(x.pnl) : 0;
            const pnlColor = pnlVal >= 0 ? "#10B981" : "#EF4444";
            return `<tr>
              <td><strong>${esc(x.symbol)}</strong></td>
              <td>${esc(String(x.qty))}</td>
              <td>$${esc(Number(x.avg_price).toFixed(2))}</td>
              <td>$${esc(x.last_price != null ? Number(x.last_price).toFixed(2) : "—")}</td>
              <td style="color:${pnlColor};font-weight:700;">${pnlVal >= 0 ? "+" : ""}$${pnlVal.toFixed(2)}</td>
              <td><button type="button" class="btn" data-close-symbol="${esc(x.symbol)}" style="padding:4px 8px;font-size:0.75rem;">Fermer ✕</button></td>
            </tr>`;
          }
        )
        .join("") || `<tr><td colspan="6" class="muted" style="text-align:center;">Aucune position active. Passez un premier ordre ci-dessus !</td></tr>`;
    tbody.onclick = (ev) => {
      const btn = ev.target.closest("[data-close-symbol]");
      if (!btn) return;
      closePosition(btn.dataset.closeSymbol);
    };
  }

  const predTbody = $("#learnPredTable tbody");
  if (predTbody) {
    predTbody.innerHTML =
      predictions
        .map(
          (pred) => `<tr>
            <td style="max-width:220px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="${esc(pred.question)}">${esc(pred.question)}</td>
            <td><span class="badge-brand" style="font-size:0.75rem;">${esc(pred.outcome)}</span></td>
            <td>${esc(String(pred.shares))}</td>
            <td>$${esc(Number(pred.amount_invested).toFixed(2))}</td>
            <td style="color:#10B981;font-weight:700;">$${esc(Number(pred.potential_payout).toFixed(2))}</td>
          </tr>`
        )
        .join("") || `<tr><td colspan="5" class="muted" style="text-align:center;">Aucun pari en cours. Découvrez l'espace Prédictions !</td></tr>`;
  }

  const ft = $("#learnFillsTable tbody");
  if (ft) {
    ft.innerHTML =
      fills
        .map(
          (f) => `<tr>
          <td><strong>${esc(f.symbol)}</strong></td>
          <td><span style="color:${f.side === 'buy' ? '#10B981' : '#EF4444'};font-weight:700;">${esc(f.side.toUpperCase())}</span></td>
          <td>${esc(String(f.qty))}</td>
          <td>$${esc(Number(f.price).toFixed(2))}</td>
          <td>${esc((f.created_at || "").slice(0, 19).replace('T', ' '))}</td>
        </tr>`
        )
        .join("") || `<tr><td colspan="5" class="muted" style="text-align:center;">Aucun historique récent.</td></tr>`;
  }
}

async function placeLearnOrder(e) {
  e?.preventDefault();
  const body = {
    symbol: $("#learnSymbol").value.trim(),
    side: $("#learnSide").value,
    qty: Number($("#learnQty").value),
    order_type: $("#learnOrderType")?.value || "market",
    limit_price: $("#learnLimit")?.value ? Number($("#learnLimit").value) : null,
  };
  const res = await api("/api/learn/orders", { method: "POST", body: JSON.stringify(body) });
  if (res.error) {
    $("#learnTradeMsg").textContent = res.error;
    return;
  }
  $("#learnTradeMsg").textContent = `Ordre ${res.status} @ ${res.fill_price ?? "n/a"}`;
  if (res.portfolio) paintLearn(res.portfolio);
  else loadLearn();
  loadLearnChart();
}

async function resetLearn() {
  if (!confirm("Réinitialiser le compte virtuel à 10 000 $ ?")) return;
  const p = await api("/api/learn/reset", { method: "POST", body: "{}" });
  paintLearn(p);
}

async function askLearn(promptText) {
  const q = promptText || ($("#learnAsk")?.value || "").trim();
  if (!q) return;
  const ansBox = $("#learnAnswer");
  if (!ansBox) return;
  ansBox.style.display = "block";
  ansBox.innerHTML = '<p class="muted">🤖 Analyse IA en cours...</p>';
  try {
    const res = await api("/api/learn/explain", {
      method: "POST",
      body: JSON.stringify({ question: q }),
    });
    if (res.error) {
      ansBox.innerHTML = `<p style="color:#EF4444;">⚠️ ${esc(res.error)}</p>`;
      return;
    }
    ansBox.innerHTML = mdToHtml(res.answer || "Aucune analyse disponible.") + 
      (res.disclaimer ? `<p class="muted small" style="margin-top:8px;"><em>${esc(res.disclaimer)}</em></p>` : "");
  } catch (e) {
    ansBox.innerHTML = '<p class="muted">Erreur lors de la génération de l\'analyse.</p>';
  }
}

function askLearnPrompt(txt) {
  if ($("#learnAsk")) $("#learnAsk").value = txt;
  askLearn(txt);
}

// Initialise event listeners on startup
function wireTradingEvents() {
  $("#tabModeBeginner")?.addEventListener("click", () => switchTradeMode("beginner"));
  $("#tabModePro")?.addEventListener("click", () => switchTradeMode("pro"));
  $("#btnLearnChartRefresh")?.addEventListener("click", loadLearnChart);
  $("#betClose")?.addEventListener("click", () => $("#betModal")?.classList.add("hidden"));
  $("#betForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    submitBet();
  });
  $("#betAmount")?.addEventListener("input", updateBetPayout);
  $("#learnAskForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    askLearn();
  });
}

document.addEventListener("DOMContentLoaded", init);

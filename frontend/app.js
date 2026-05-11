"use strict";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const LOADING_TIPS = [
  "Ищем статью в Рувики...",
  "Анализирую ключевые факты...",
  "Подбираю слова для твоего возраста...",
  "Нахожу лучшую аналогию...",
  "Составляю вопросы для викторины...",
  "Почти готово!",
];

const HEALTH_FETCH_MS = 15000;
const CACHE_FETCH_MS = 30000;
const SIMPLIFY_FETCH_MS = 180000;

// ---------------------------------------------------------------------------
// App state
// ---------------------------------------------------------------------------
const state = {
  simplifiedText: "",
  historyKey: "",
  recognition: null,
  progressTimer: null,
  statusTimer: null,
  typingTimer: null,
  backendUrl: "",
  game: null,
};

// ---------------------------------------------------------------------------
// Bubble-pop mini game (compact, inline, not fullscreen)
// ---------------------------------------------------------------------------
class BubblePop {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.bubbles = [];
    this.score = 0;
    this.animId = null;
    this.active = false;
    this.spawnTimer = 0;

    this.emojis = ["🔬", "💡", "⭐", "🚀", "📚", "🌍", "🧪", "🔭", "🧠", "💫", "🌱", "⚡"];
    this.colors = [
      "rgba(109,93,252,0.82)",
      "rgba(56,189,248,0.82)",
      "rgba(251,191,36,0.88)",
      "rgba(52,211,153,0.82)",
      "rgba(248,113,113,0.82)",
      "rgba(167,139,250,0.82)",
      "rgba(34,197,94,0.82)",
    ];

    this._onClick = this._onClick.bind(this);
    this._resize = this._resize.bind(this);
  }

  _resize() {
    const parent = this.canvas.parentElement;
    // Use the parent container's dimensions
    this.canvas.width = parent.offsetWidth || 600;
    this.canvas.height = parent.offsetHeight || 160;
  }

  start() {
    this.active = true;
    this.score = 0;
    this.bubbles = [];
    this._resize();
    window.addEventListener("resize", this._resize);
    this.canvas.addEventListener("click", this._onClick);
    this.canvas.addEventListener("touchstart", this._onClick, { passive: true });
    for (let i = 0; i < 7; i++) this._spawnBubble(true);
    this._loop();
  }

  stop() {
    this.active = false;
    if (this.animId) cancelAnimationFrame(this.animId);
    this.animId = null;
    window.removeEventListener("resize", this._resize);
    this.canvas.removeEventListener("click", this._onClick);
    this.canvas.removeEventListener("touchstart", this._onClick);
  }

  _loop() {
    if (!this.active) return;
    this._update();
    this._draw();
    this.animId = requestAnimationFrame(() => this._loop());
  }

  _update() {
    const W = this.canvas.width;
    const H = this.canvas.height;

    this.spawnTimer++;
    if (this.spawnTimer >= 70) {
      this._spawnBubble(false);
      this.spawnTimer = 0;
    }

    this.bubbles = this.bubbles.filter((b) => {
      if (b.popping) {
        b.scale -= 0.09;
        b.alpha -= 0.1;
        return b.scale > 0.05;
      }
      b.y -= b.vy;
      b.x += b.vx;
      b.phase += 0.025;
      b.x += Math.sin(b.phase) * 0.4;
      // Wrap horizontally so bubbles don't disappear off sides
      if (b.x < -b.r) b.x = W + b.r;
      if (b.x > W + b.r) b.x = -b.r;
      return b.y + b.r > -10;
    });
  }

  _draw() {
    const ctx = this.ctx;
    const W = this.canvas.width;
    const H = this.canvas.height;

    ctx.clearRect(0, 0, W, H);

    this.bubbles.forEach((b) => {
      ctx.save();
      ctx.globalAlpha = b.alpha;
      ctx.translate(b.x, b.y);
      ctx.scale(b.scale, b.scale);

      // Shadow glow
      ctx.shadowBlur = 14;
      ctx.shadowColor = b.color.replace("0.82", "0.5").replace("0.88", "0.5");

      // Circle
      ctx.beginPath();
      ctx.arc(0, 0, b.r, 0, Math.PI * 2);
      ctx.fillStyle = b.color;
      ctx.fill();

      // Shine
      ctx.shadowBlur = 0;
      ctx.beginPath();
      ctx.arc(-b.r * 0.28, -b.r * 0.32, b.r * 0.22, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255,255,255,0.38)";
      ctx.fill();

      // Emoji
      ctx.font = `${Math.round(b.r * 1.05)}px serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(b.emoji, 0, 2);

      ctx.restore();
    });
  }

  _spawnBubble(init) {
    const W = this.canvas.width;
    const H = this.canvas.height;
    const r = 22 + Math.random() * 18;
    this.bubbles.push({
      x: r + Math.random() * (W - 2 * r),
      y: init ? r + Math.random() * (H - r) : H + r,
      r,
      vy: 0.45 + Math.random() * 0.65,
      vx: (Math.random() - 0.5) * 0.35,
      phase: Math.random() * Math.PI * 2,
      emoji: this.emojis[Math.floor(Math.random() * this.emojis.length)],
      color: this.colors[Math.floor(Math.random() * this.colors.length)],
      scale: 1,
      alpha: 1,
      popping: false,
    });
  }

  _onClick(e) {
    const rect = this.canvas.getBoundingClientRect();
    const scaleX = this.canvas.width / rect.width;
    const scaleY = this.canvas.height / rect.height;
    let cx, cy;
    if (e.touches) {
      cx = (e.touches[0].clientX - rect.left) * scaleX;
      cy = (e.touches[0].clientY - rect.top) * scaleY;
    } else {
      cx = (e.clientX - rect.left) * scaleX;
      cy = (e.clientY - rect.top) * scaleY;
    }
    for (let i = this.bubbles.length - 1; i >= 0; i--) {
      const b = this.bubbles[i];
      if (b.popping) continue;
      const dx = cx - b.x;
      const dy = cy - b.y;
      if (Math.sqrt(dx * dx + dy * dy) < b.r * b.scale + 4) {
        b.popping = true;
        this.score++;
        $("#gameScoreVal").text(this.score);
        break;
      }
    }
  }
}

function startGame() {
  const $g = $("#miniGame");
  $g.removeClass("hidden");
  // Animate in
  $g[0].style.opacity = "0";
  requestAnimationFrame(() => {
    $g[0].style.transition = "opacity 0.4s";
    $g[0].style.opacity = "1";
  });
  if (!state.game) state.game = new BubblePop(document.getElementById("gameCanvas"));
  state.game.start();
}

function stopGame() {
  const $g = $("#miniGame");
  if (state.game) state.game.stop();
  $g[0].style.transition = "opacity 0.4s";
  $g[0].style.opacity = "0";
  setTimeout(() => $g.addClass("hidden"), 420);
}

// ---------------------------------------------------------------------------
// Interactive Quiz
// ---------------------------------------------------------------------------
const quiz = { items: [], current: 0, score: 0, revealed: false };

function initQuiz(items) {
  quiz.items = items || [];
  quiz.current = 0;
  quiz.score = 0;
  quiz.revealed = false;
  renderQuizStep();
}

function renderQuizStep() {
  const $c = $("#quizContainer").empty();
  if (!quiz.items.length) {
    $c.append($("<p>").addClass("emptyText").text("Вопросы появятся после следующего ответа."));
    return;
  }
  if (quiz.current >= quiz.items.length) {
    renderQuizResult($c);
    return;
  }
  const item = quiz.items[quiz.current];
  const $card = $("<div>").addClass("quizCard pop-in");
  $card.append($("<div>").addClass("quizProgress").text(`Вопрос ${quiz.current + 1} из ${quiz.items.length}`));
  $card.append($("<p>").addClass("quizQuestion").text(item.question || "Вопрос"));

  if (!quiz.revealed) {
    $("<button>").addClass("quizRevealBtn").text("Показать ответ")
      .on("click", () => { quiz.revealed = true; renderQuizStep(); })
      .appendTo($card);
  } else {
    $card.append(
      $("<div>").addClass("quizAnswer pop-in")
        .append($("<span>").addClass("quizAnswerLabel").text("Ответ: "))
        .append(document.createTextNode(item.answer || ""))
    );
    const $row = $("<div>").addClass("quizSelfRow");
    $("<button>").addClass("quizSelfBtn correct").text("✓ Знал!")
      .on("click", () => { quiz.score++; quiz.current++; quiz.revealed = false; renderQuizStep(); })
      .appendTo($row);
    $("<button>").addClass("quizSelfBtn wrong").text("✗ Не знал")
      .on("click", () => { quiz.current++; quiz.revealed = false; renderQuizStep(); })
      .appendTo($row);
    $card.append($row);
  }
  $c.append($card);
}

function renderQuizResult($c) {
  const { score, items } = quiz;
  const total = items.length;
  const [msg, emoji] =
    score === total ? ["Отлично! Всё правильно!", "🏆"] :
    score >= Math.ceil(total * 0.6) ? ["Молодец! Почти всё.", "🌟"] :
    ["Неплохо! Попробуй ещё раз.", "💪"];
  const $res = $("<div>").addClass("quizResult pop-in");
  $res.append($("<div>").addClass("quizEmoji").text(emoji));
  $res.append($("<p>").addClass("quizResultText").text(`${msg} (${score} из ${total})`));
  $("<button>").addClass("quizRestartBtn").text("Пройти снова")
    .on("click", () => { quiz.current = 0; quiz.score = 0; quiz.revealed = false; renderQuizStep(); })
    .appendTo($res);
  $c.append($res);
}

// ---------------------------------------------------------------------------
// Network helpers
// ---------------------------------------------------------------------------
async function fetchWithTimeout(url, options = {}, timeoutMs = 100000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: ctrl.signal });
  } finally {
    clearTimeout(t);
  }
}

function normalizeBackendUrl(raw) {
  let s = String(raw ?? "").trim();
  if (!s) return "http://127.0.0.1:8000";
  if (!/^https?:\/\//i.test(s)) s = "http://" + s.replace(/^\/+/, "");
  try {
    const u = new URL(s);
    if (!u.hostname) throw new Error("no host");
    return `${u.protocol}//${u.host}`;
  } catch {
    return "http://127.0.0.1:8000";
  }
}

function syncBackendUrlInput() {
  const n = normalizeBackendUrl($("#backendUrl").val());
  $("#backendUrl").val(n);
  localStorage.setItem("ruwiki_backend_url", n);
  state.backendUrl = n;
  return n;
}

function initializeBackendUrl() {
  const url = normalizeBackendUrl(localStorage.getItem("ruwiki_backend_url") || "http://127.0.0.1:8000");
  $("#backendUrl").val(url);
  localStorage.setItem("ruwiki_backend_url", url);
  state.backendUrl = url;
  return url;
}

function saveBackendUrl(url) {
  const n = normalizeBackendUrl(url);
  localStorage.setItem("ruwiki_backend_url", n);
  $("#backendUrl").val(n);
  state.backendUrl = n;
}

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------
function setBusy(isBusy, message = "") {
  $("#submitBtn").prop("disabled", isBusy).text(isBusy ? "Думаю..." : "Объяснить!");
  $("#clearBtn").prop("disabled", isBusy);
  $(".controls").toggleClass("isBusy", isBusy);
  $("#progress").toggleClass("hidden", !isBusy);
  $("#status").text(message);
  if (isBusy) {
    startProgress();
    startLoadingTips(message);
    startGame();
  } else {
    stopProgress();
    stopLoadingTips();
    stopGame();
  }
}

function startProgress() {
  const $steps = $("#progress span");
  let index = 0;
  $steps.removeClass("active done").eq(0).addClass("active");
  clearInterval(state.progressTimer);
  state.progressTimer = setInterval(() => {
    $steps.eq(index).removeClass("active").addClass("done");
    index = Math.min(index + 1, $steps.length - 1);
    $steps.eq(index).addClass("active");
  }, 1400);
}

function stopProgress() {
  clearInterval(state.progressTimer);
  state.progressTimer = null;
}

function startLoadingTips(initial) {
  let i = 0;
  clearInterval(state.statusTimer);
  $("#status").text(initial || LOADING_TIPS[0]);
  state.statusTimer = setInterval(() => {
    i = (i + 1) % LOADING_TIPS.length;
    $("#status").text(LOADING_TIPS[i]);
  }, 1600);
}

function stopLoadingTips() {
  clearInterval(state.statusTimer);
  state.statusTimer = null;
}

// ---------------------------------------------------------------------------
// Metrics UI
// ---------------------------------------------------------------------------
function pct(v) {
  if (v === null || v === undefined || Number.isNaN(+v)) return "—";
  return `${Math.round(+v * 100)}%`;
}

function setMetricsUI(data) {
  const metricsEnabled = data?.metrics_enabled !== false;
  if (!metricsEnabled) { $("#metricsStrip").addClass("hidden"); return; }
  $("#metricsStrip").removeClass("hidden");

  const ev = data?.evaluation || {};
  const timings = data?.timings_ms || {};
  const bp = typeof ev.bleurt_proxy === "number" ? ev.bleurt_proxy : null;
  const pctVal = bp !== null ? Math.round(bp * 100) : null;

  if (pctVal !== null) {
    $("#accuracyPercent").text(`${pctVal}%`);
    $("#accuracyArc").attr("stroke-dasharray", `${pctVal}, ${100 - pctVal}`);
  } else {
    $("#accuracyPercent").text("—");
    $("#accuracyArc").attr("stroke-dasharray", "0, 100");
  }

  $("#accuracyLabel").text(data?.accuracy?.metric_label || "Качество объяснения");
  $("#accuracyDetail").text(data?.accuracy?.detail_summary || "");
  $("#mSimplicity").text(pct(ev.simplicity));
  $("#mExamples").text(pct(ev.example_quality));
  $("#mClarity").text(pct(ev.term_clarity));
  $("#mLatency").text(data?.cached ? "<50 мс" : timings.total ? `${timings.total} мс` : "—");
}

// ---------------------------------------------------------------------------
// Typing effect
// ---------------------------------------------------------------------------
function typeText($el, text, speed = 14) {
  clearInterval(state.typingTimer);
  $el.text("");
  const tokens = String(text || "").split(/(\s+)/);
  let i = 0;
  state.typingTimer = setInterval(() => {
    if (i >= tokens.length) { clearInterval(state.typingTimer); state.typingTimer = null; return; }
    $el.append(document.createTextNode(tokens[i]));
    i++;
  }, speed);
}

// ---------------------------------------------------------------------------
// Render result
// ---------------------------------------------------------------------------
function renderResult(data) {
  state.simplifiedText = data.simplified_text || "";
  state.historyKey = data.history_key || "";

  $("#result").removeClass("hidden");
  setMetricsUI(data);

  $("#sourceTitle").text(data.source_title);
  $("#sourceUrl").attr("href", data.source_url).text(data.source_url);
  $("#mainIdea").text(data.main_idea || "Главная мысль выделена в объяснении ниже.");

  typeText($("#simplifiedText"), state.simplifiedText, data.cached ? 4 : 14);

  // Analogies
  const analogies = data.analogies || [];
  if (analogies.length) {
    const $ul = $("#analogies").empty();
    analogies.forEach((t, i) =>
      $("<li>").addClass("pop-in").css("animation-delay", `${i * 0.07}s`).text(t).appendTo($ul)
    );
    $("#analogiesBox").removeClass("hidden");
  } else {
    $("#analogiesBox").addClass("hidden");
  }

  // Glossary
  const glossary = data.glossary || [];
  if (glossary.length) {
    const $ul = $("#glossary").empty();
    glossary.forEach((item, i) => {
      $("<li>").addClass("pop-in").css("animation-delay", `${i * 0.05}s`)
        .append($("<strong>").text(item.term || "Термин"))
        .append(document.createTextNode(` — ${item.definition || ""}`))
        .appendTo($ul);
    });
    $("#glossaryBox").removeClass("hidden");
  } else {
    $("#glossaryBox").addClass("hidden");
  }

  initQuiz(data.quiz);

  // Reasoning (collapsed)
  if (data.reasoning_steps?.length) {
    $("#reasoningDetails").removeClass("hidden");
    const $ol = $("#reasoningSteps").empty();
    data.reasoning_steps.forEach((t, i) =>
      $("<li>").addClass("pop-in").css("animation-delay", `${i * 0.04}s`).text(t).appendTo($ol)
    );
  } else {
    $("#reasoningDetails").addClass("hidden");
  }

  resetRating();
  $("#ratingBox").removeClass("hidden");

  $("#meta").text(JSON.stringify({
    cached: data.cached,
    metrics_enabled: data.metrics_enabled,
    model: data.model,
    quality: data.quality,
    evaluation: data.evaluation,
    accuracy: data.accuracy,
    verifier: data.verifier,
    timings_ms: data.timings_ms,
  }, null, 2));

  requestAnimationFrame(() =>
    document.getElementById("result").scrollIntoView({ behavior: "smooth", block: "start" })
  );
}

// ---------------------------------------------------------------------------
// Rating widget
// ---------------------------------------------------------------------------
function resetRating() {
  $("#starRow .starBtn").removeClass("active");
  $("#ratingMsg").addClass("hidden");
  $("#starRow").show();
}

async function submitRating(stars) {
  $("#starRow .starBtn").each(function () {
    $(this).toggleClass("active", Number($(this).data("stars")) <= stars);
  });
  const backendUrl = state.backendUrl || normalizeBackendUrl($("#backendUrl").val());
  try {
    await fetchWithTimeout(
      `${backendUrl}/rate`,
      { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ history_key: state.historyKey || "unknown", stars }),
        mode: "cors", cache: "no-store" },
      10000
    );
  } catch (_) { /* silent */ }
  $("#starRow").hide();
  $("#ratingMsg").removeClass("hidden");
}

// ---------------------------------------------------------------------------
// Main simplify action
// ---------------------------------------------------------------------------
async function simplify() {
  const backendUrl = syncBackendUrlInput();
  const query = $("#query").val().trim();
  const age = Number($("#age").val());
  const mode = $("#mode").val();
  const enable_metrics = !$("#fastModeToggle").prop("checked");

  if (!query) {
    $("#status").text("Введите название статьи или темы.");
    $("#query").trigger("focus");
    return;
  }

  saveBackendUrl(backendUrl);
  setBusy(true, "Ищу статью в Рувики...");
  try {
    const body = { query, age, mode, enable_metrics };
    const res = await fetchWithTimeout(
      `${backendUrl}/simplify`,
      { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body), mode: "cors", cache: "no-store" },
      SIMPLIFY_FETCH_MS
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Ошибка сервера: ${res.status}`);
    renderResult(data);
    setBusy(false, data.cached ? "Готово: ответ из кэша." : "Готово!");
  } catch (e) {
    setBusy(false, `Ошибка: ${e.message}`);
    console.error("Simplify error:", e);
  }
}

// ---------------------------------------------------------------------------
// Health check
// ---------------------------------------------------------------------------
async function checkHealth() {
  const backendUrl = syncBackendUrlInput();
  const $b = $("#healthBadge");
  $b.removeClass("offline").addClass("checking").text("Связь с API…");
  try {
    const res = await fetchWithTimeout(
      `${backendUrl}/health`, { method: "GET", cache: "no-store", mode: "cors" }, HEALTH_FETCH_MS
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const label = data.api_key_configured ? `${data.provider}: ${data.model}` : "API-ключ не настроен";
    $b.removeClass("offline checking").text(label);
  } catch (e) {
    const abort = e?.name === "AbortError";
    const failed = String(e?.message || "").toLowerCase().includes("failed to fetch");
    const sec = Math.round(HEALTH_FETCH_MS / 1000);
    const msg = abort ? `Нет ответа за ${sec} с — проверьте адрес`
      : failed ? "CORS/сеть: откройте через http://, не file://"
      : `Ошибка: ${String(e?.message || "").slice(0, 96)}`;
    $b.addClass("offline").text(msg);
  } finally {
    $b.removeClass("checking");
  }
}

// ---------------------------------------------------------------------------
// Cache clear
// ---------------------------------------------------------------------------
async function clearCache() {
  const backendUrl = syncBackendUrlInput();
  $("#clearBtn").prop("disabled", true).text("Очищаю...");
  try {
    const res = await fetchWithTimeout(
      `${backendUrl}/cache`, { method: "DELETE", mode: "cors", cache: "no-store" }, CACHE_FETCH_MS
    );
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || "Ошибка"); }
    $("#status").text("Кэш очищен. Следующий ответ будет сгенерирован заново.");
  } catch (e) {
    $("#status").text(`Не удалось очистить кэш: ${e.message}`);
  } finally {
    $("#clearBtn").prop("disabled", false).text("Очистить кэш");
  }
}

// ---------------------------------------------------------------------------
// Copy / Speech
// ---------------------------------------------------------------------------
async function copyResult() {
  if (!state.simplifiedText) return;
  try {
    await navigator.clipboard.writeText([$("#mainIdea").text(), "", state.simplifiedText].join("\n"));
    $("#status").text("Скопировано в буфер обмена.");
  } catch {
    $("#status").text("Браузер не разрешил копирование. Выделите текст вручную.");
  }
}

function setupSpeechRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { $("#micBtn").prop("disabled", true).text("Нет микрофона"); return; }
  state.recognition = new SR();
  state.recognition.lang = "ru-RU";
  state.recognition.interimResults = false;
  state.recognition.onstart = () => $("#status").text("Слушаю...");
  state.recognition.onresult = (e) => { $("#query").val(e.results[0][0].transcript); $("#status").text("Распознано."); };
  state.recognition.onerror = () => $("#status").text("Не удалось распознать голос.");
  state.recognition.onend = () => $("#micBtn").prop("disabled", false);
}

function speak() {
  if (!state.simplifiedText) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(state.simplifiedText);
  u.lang = "ru-RU";
  u.rate = 0.92;
  window.speechSynthesis.speak(u);
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
$(function () {
  initializeBackendUrl();
  setupSpeechRecognition();
  checkHealth();

  $("#submitBtn").on("click", simplify);
  $("#copyBtn").on("click", copyResult);
  $("#clearBtn").on("click", clearCache);
  $("#speakBtn").on("click", speak);

  $("#backendUrl").on("change blur", function () {
    saveBackendUrl($(this).val());
    checkHealth();
  });

  $(".exampleBtn").on("click", function () {
    $("#query").val($(this).data("query"));
    simplify();
  });

  $("#query").on("keydown", (e) => { if (e.ctrlKey && e.key === "Enter") simplify(); });

  $("#micBtn").on("click", () => {
    if (!state.recognition) return;
    $("#micBtn").prop("disabled", true);
    state.recognition.start();
  });

  $("#starRow").on("click", ".starBtn", function () {
    submitRating(Number($(this).data("stars")));
  }).on("mouseenter", ".starBtn", function () {
    const n = Number($(this).data("stars"));
    $("#starRow .starBtn").each(function () {
      $(this).toggleClass("hover", Number($(this).data("stars")) <= n);
    });
  }).on("mouseleave", ".starBtn", () => $("#starRow .starBtn").removeClass("hover"));

  if (!localStorage.getItem("ruwiki_first_visit")) {
    $("#status").text("Введи тему или нажми пример — объясним простыми словами.");
    localStorage.setItem("ruwiki_first_visit", "true");
  }

  $("#query").focus();
});

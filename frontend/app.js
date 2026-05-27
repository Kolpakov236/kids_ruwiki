"use strict";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const REMOTE_BACKEND_URL = "https://d5dg7k4qpk1aie7v9401.nkhmighe.apigw.yandexcloud.net";
const LOCAL_BACKEND_URL  = "http://127.0.0.1:8000";
const RUWIKI_BASE = "https://ruwiki.ru/wiki/";

// Mutable — resolved by detectBackend() before first request
let BACKEND_URL   = REMOTE_BACKEND_URL;
let IS_LOCAL_MODE = false;

async function detectBackend() {
  try {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 1200);
    const res = await fetch(LOCAL_BACKEND_URL + "/health", {
      method: "GET",
      signal: ctrl.signal,
      cache: "no-store",
    });
    clearTimeout(tid);
    if (res.ok) {
      BACKEND_URL   = LOCAL_BACKEND_URL;
      IS_LOCAL_MODE = true;
    }
  } catch (_) {
    // local not reachable — stay on remote
  }
}

const LOADING_TIPS = [
  "Ищем статью в Рувики...",
  "Анализирую ключевые факты...",
  "Подбираю слова для твоего возраста...",
  "Нахожу лучшую аналогию...",
  "Составляю вопросы для викторины...",
  "Почти готово!",
];

// ---------------------------------------------------------------------------
// App State
// ---------------------------------------------------------------------------
const state = {
  user: null,          // { id, email, display_name, birth_date, avatar_url, age }
  token: null,
  chatId: null,
  currentPanel: "chat",
  enableMetrics: false,
  selectedModelId: "",
  recognition: null,
  micPhase: "idle",        // "idle" | "listening" | "paused"
  micAccumulated: "",      // text saved across recognition sessions
  statusTimer: null,
  progressTimer: null,
  game: null,
  currentQuizItems: [],
};

// ---------------------------------------------------------------------------
// Auth helpers
// ---------------------------------------------------------------------------
function getToken() {
  return state.token || localStorage.getItem("rw_token");
}

function setToken(token) {
  state.token = token;
  localStorage.setItem("rw_token", token);
}

function clearAuth() {
  state.token = null;
  state.user = null;
  state.chatId = null;
  localStorage.removeItem("rw_token");
}

function authHeaders() {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

// ---------------------------------------------------------------------------
// Network
// ---------------------------------------------------------------------------
async function apiFetch(path, options = {}, timeoutMs = 30000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(BACKEND_URL + path, {
      ...options,
      signal: ctrl.signal,
      headers: { "Content-Type": "application/json", ...authHeaders(), ...(options.headers || {}) },
    });
    return res;
  } finally {
    clearTimeout(t);
  }
}

// ---------------------------------------------------------------------------
// BubblePop mini game
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
    this.emojis = ["🔬","💡","⭐","🚀","📚","🌍","🧪","🔭","🧠","💫","🌱","⚡"];
    this.colors = [
      "rgba(109,93,252,0.82)","rgba(56,189,248,0.82)","rgba(251,191,36,0.88)",
      "rgba(52,211,153,0.82)","rgba(248,113,113,0.82)","rgba(167,139,250,0.82)",
    ];
    this._onClick = this._onClick.bind(this);
    this._resize = this._resize.bind(this);
  }
  _resize() {
    const p = this.canvas.parentElement;
    this.canvas.width = p.offsetWidth || 600;
    this.canvas.height = p.offsetHeight || 130;
  }
  start() {
    this.active = true; this.score = 0; this.bubbles = [];
    this._resize();
    window.addEventListener("resize", this._resize);
    this.canvas.addEventListener("click", this._onClick);
    this.canvas.addEventListener("touchstart", this._onClick, { passive: true });
    for (let i = 0; i < 7; i++) this._spawn(true);
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
    this._update(); this._draw();
    this.animId = requestAnimationFrame(() => this._loop());
  }
  _update() {
    const W = this.canvas.width;
    this.spawnTimer++;
    if (this.spawnTimer >= 70) { this._spawn(false); this.spawnTimer = 0; }
    this.bubbles = this.bubbles.filter(b => {
      if (b.popping) { b.scale -= 0.09; b.alpha -= 0.1; return b.scale > 0.05; }
      b.y -= b.vy; b.x += b.vx; b.phase += 0.025; b.x += Math.sin(b.phase) * 0.4;
      if (b.x < -b.r) b.x = W + b.r;
      if (b.x > W + b.r) b.x = -b.r;
      return b.y + b.r > -10;
    });
  }
  _draw() {
    const ctx = this.ctx, W = this.canvas.width, H = this.canvas.height;
    ctx.clearRect(0, 0, W, H);
    this.bubbles.forEach(b => {
      ctx.save(); ctx.globalAlpha = b.alpha;
      ctx.translate(b.x, b.y); ctx.scale(b.scale, b.scale);
      ctx.shadowBlur = 14; ctx.shadowColor = b.color.replace("0.82","0.5").replace("0.88","0.5");
      ctx.beginPath(); ctx.arc(0,0,b.r,0,Math.PI*2); ctx.fillStyle = b.color; ctx.fill();
      ctx.shadowBlur = 0;
      ctx.beginPath(); ctx.arc(-b.r*.28,-b.r*.32,b.r*.22,0,Math.PI*2);
      ctx.fillStyle="rgba(255,255,255,0.38)"; ctx.fill();
      ctx.font=`${Math.round(b.r*1.05)}px serif`; ctx.textAlign="center"; ctx.textBaseline="middle";
      ctx.fillText(b.emoji,0,2); ctx.restore();
    });
  }
  _spawn(init) {
    const W = this.canvas.width, H = this.canvas.height, r = 22 + Math.random() * 18;
    this.bubbles.push({
      x: r + Math.random()*(W-2*r), y: init ? r+Math.random()*(H-r) : H+r,
      r, vy: 0.45+Math.random()*0.65, vx:(Math.random()-.5)*.35,
      phase:Math.random()*Math.PI*2,
      emoji:this.emojis[Math.floor(Math.random()*this.emojis.length)],
      color:this.colors[Math.floor(Math.random()*this.colors.length)],
      scale:1, alpha:1, popping:false,
    });
  }
  _onClick(e) {
    const rect = this.canvas.getBoundingClientRect();
    const sx = this.canvas.width/rect.width, sy = this.canvas.height/rect.height;
    let cx,cy;
    if (e.touches) { cx=(e.touches[0].clientX-rect.left)*sx; cy=(e.touches[0].clientY-rect.top)*sy; }
    else { cx=(e.clientX-rect.left)*sx; cy=(e.clientY-rect.top)*sy; }
    for (let i=this.bubbles.length-1;i>=0;i--) {
      const b=this.bubbles[i]; if(b.popping) continue;
      if(Math.hypot(cx-b.x,cy-b.y)<b.r*b.scale+4) {
        b.popping=true; this.score++;
        $("#gameScoreVal").text(this.score); break;
      }
    }
  }
}

function startGame() {
  const $g = $("#miniGame").removeClass("hidden");
  $g[0].style.opacity="0";
  requestAnimationFrame(()=>{ $g[0].style.transition="opacity 0.4s"; $g[0].style.opacity="1"; });
  if (!state.game) state.game = new BubblePop(document.getElementById("gameCanvas"));
  state.game.start();
}

function stopGame() {
  if (state.game) state.game.stop();
  const $g=$("#miniGame");
  $g[0].style.transition="opacity 0.4s"; $g[0].style.opacity="0";
  setTimeout(()=>$g.addClass("hidden"),420);
}

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------
function setBusy(busy, msg="") {
  $("#submitBtn").prop("disabled", busy);
  $("#progress").toggleClass("hidden", !busy);
  if (busy) { startProgress(); startTips(msg); startGame(); }
  else { stopProgress(); stopTips(); stopGame(); }
  setStatus(busy ? (msg || LOADING_TIPS[0]) : "");
}

function startProgress() {
  const $steps = $("#progress span");
  let idx=0; $steps.removeClass("active done").eq(0).addClass("active");
  clearInterval(state.progressTimer);
  state.progressTimer = setInterval(()=>{
    $steps.eq(idx).removeClass("active").addClass("done");
    idx=Math.min(idx+1,$steps.length-1);
    $steps.eq(idx).addClass("active");
  }, 1400);
}
function stopProgress() { clearInterval(state.progressTimer); }

function startTips(initial) {
  let i=0; clearInterval(state.statusTimer);
  setStatus(initial || LOADING_TIPS[0]);
  state.statusTimer = setInterval(()=>{
    i=(i+1)%LOADING_TIPS.length; setStatus(LOADING_TIPS[i]);
  },1600);
}
function stopTips() { clearInterval(state.statusTimer); }

function setStatus(msg) { $("#status").text(msg||""); }

// ---------------------------------------------------------------------------
// Rendering messages
// ---------------------------------------------------------------------------
function addUserMessage(text) {
  const $el = $("<div>").addClass("msgUser pop-in").text(text);
  $("#messages").append($el);
  scrollMessages();
}

function addAssistantMessage(data) {
  const $el = $("<div>").addClass("msgAssistant pop-in");
  const msgId = `msg_${Date.now()}`;
  $el.attr("id", msgId);

  // LLM-only banner (no wiki article found)
  if (data.llm_only) {
    $("<div>").addClass("msgLlmOnlyBanner")
      .text("Статьи в энциклопедии не нашлось — отвечает ИИ по своим знаниям")
      .appendTo($el);
  }

  // Main idea
  const $idea = $("<div>").addClass("msgMainIdea");
  $("<div>").addClass("msgMainIdeaLabel").text("Главная мысль").appendTo($idea);
  $("<div>").addClass("msgMainIdeaText").text(data.main_idea || "").appendTo($idea);
  $el.append($idea);

  // Body
  const $body = $("<div>").addClass("msgBody");

  // Explanation
  const $expSec = $("<div>").addClass("msgSection");
  $("<div>").addClass("msgSectionTitle").text("💡 Простое объяснение").appendTo($expSec);
  const $text = $("<div>").addClass("msgText").appendTo($expSec);
  $body.append($expSec);
  typeText($text, data.simplified_text || "", data.cached ? 4 : 14);

  // Analogies
  if (data.analogies?.length) {
    const $aSec = $("<div>").addClass("msgSection");
    $("<div>").addClass("msgSectionTitle").text("🎯 Аналогии").appendTo($aSec);
    const $aBox = $("<div>").addClass("msgAnalogies").appendTo($aSec);
    const $ul = $("<ul>").appendTo($aBox);
    data.analogies.forEach(a => $("<li>").text(a).appendTo($ul));
    $body.append($aSec);
  }

  // Glossary
  if (data.glossary?.length) {
    const $gSec = $("<div>").addClass("msgSection");
    $("<div>").addClass("msgSectionTitle").text("📖 Ключевые термины").appendTo($gSec);
    const $gBox = $("<div>").addClass("msgGlossary").appendTo($gSec);
    data.glossary.forEach(item => {
      let def = item.definition || "";
      // strip leading "Term — " or "Term: " if the LLM duplicated the term
      const termPrefix = new RegExp("^" + item.term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*[-—–:]\\s*", "i");
      def = def.replace(termPrefix, "");
      // lowercase first letter of definition since it follows an em-dash
      def = def.charAt(0).toLowerCase() + def.slice(1);
      // capitalize first letter of the term
      const term = item.term.charAt(0).toUpperCase() + item.term.slice(1);
      $("<div>").addClass("msgGlossaryItem")
        .append($("<span>").addClass("msgGlossaryTerm").text(term + " "))
        .append(document.createTextNode("— " + def))
        .appendTo($gBox);
    });
    $body.append($gSec);
  }

  // Theories
  if (data.theories?.length) {
    const $tSec = $("<div>").addClass("msgSection");
    $("<div>").addClass("msgSectionTitle").text("🔭 Версии и теории").appendTo($tSec);
    data.theories.forEach(t => {
      const $card = $("<div>").addClass("msgTheoryCard");
      $("<div>").addClass("msgTheoryTitle").text(t.title).appendTo($card);
      $("<div>").addClass("msgTheoryText").text(t.text).appendTo($card);
      $card.appendTo($tSec);
    });
    $body.append($tSec);
  }

  // Sources
  const allSources = [];
  if (data.source_url) {
    allSources.push({ label: "📰 " + (data.source_title || "Источник"), href: data.source_url });
  }
  if (data.glossary?.length) {
    data.glossary.slice(0,4).forEach(item => {
      if (item.term) {
        allSources.push({
          label: item.term,
          href: RUWIKI_BASE + encodeURIComponent(item.term),
        });
      }
    });
  }
  if (allSources.length) {
    const $sSec = $("<div>").addClass("msgSection");
    $("<div>").addClass("msgSectionTitle").text("🔗 Источники").appendTo($sSec);
    const $sRow = $("<div>").addClass("msgSources").appendTo($sSec);
    allSources.forEach(s => {
      $("<a>").addClass("msgSourceLink")
        .attr({ href: s.href, target: "_blank", rel: "noreferrer" })
        .text(s.label).appendTo($sRow);
    });
    $body.append($sSec);
  }

  $el.append($body);

  // Actions bar
  const $actions = $("<div>").addClass("msgActions");

  if (data.quiz?.length) {
    $("<button>").addClass("msgActionBtn quizBtn").text("🎮 Викторина")
      .on("click", () => openQuiz(data.quiz)).appendTo($actions);
  }

  $("<button>").addClass("msgActionBtn").text("🔊 Озвучить")
    .on("click", function() {
      speakWhenReady(data.simplified_text || "", $(this));
    }).appendTo($actions);

  $("<button>").addClass("msgActionBtn").text("📋 Копировать")
    .on("click", () => copyText(data.main_idea, data.simplified_text)).appendTo($actions);

  // Rating stars
  const $rating = $("<div>").addClass("msgRating");
  $("<span>").addClass("msgRatingLabel").text("Оценить:").appendTo($rating);
  const $stars = $("<span>").addClass("starRow").appendTo($rating);
  const $ratingMsg = $("<span>").addClass("ratingDone hidden").text("🎉 Спасибо!").appendTo($rating);
  [1,2,3,4,5].forEach(n => {
    $("<button>").addClass("starBtn").attr("data-stars", n).text("★").appendTo($stars);
  });
  $stars.on("mouseenter", ".starBtn", function() {
    const n = Number($(this).data("stars"));
    $stars.find(".starBtn").each(function() { $(this).toggleClass("hover", Number($(this).data("stars"))<=n); });
  }).on("mouseleave", ".starBtn", ()=>$stars.find(".starBtn").removeClass("hover"))
    .on("click", ".starBtn", function() {
      const n = Number($(this).data("stars"));
      $stars.find(".starBtn").each(function() { $(this).toggleClass("active", Number($(this).data("stars"))<=n); });
      submitRating(data.history_key||"unknown", n);
      $stars.off("click mouseenter mouseleave");
      setTimeout(()=>{ $stars.hide(); $ratingMsg.removeClass("hidden"); }, 300);
    });
  $actions.append($rating);
  $el.append($actions);

  $("#messages").append($el);
  scrollMessages();
}

function _isGibberish(text) {
  const s = text.toLowerCase().replace(/\s+/g, "");
  // Only judge Cyrillic-heavy strings (latin queries like "DNA" are fine)
  const cyr = (s.match(/[а-яё]/g) || []).length;
  if (cyr < 4 || cyr < s.length * 0.65) return false;
  const vowels = (s.match(/[аеёийоуыэюя]/g) || []).length;
  // Vowel ratio < 12% is impossible in real Russian text (normal: ~40%)
  if (vowels / cyr < 0.12) return true;
  // Impossible consonant cluster of 5+ characters in a row
  if (/[бвгджзклмнпрстфхцчшщ]{5,}/.test(s)) return true;
  return false;
}

function _isNotFoundError(detail) {
  const d = String(detail).toLowerCase();
  return (
    d.includes("no_relevant_article") ||
    d.includes("article_not_found") ||
    d.includes("article_too_short") ||
    d.includes("mw_search_no_results") ||
    (d.includes("ruwiki_fetch_failed") && d.includes("not_found"))
  );
}

function addGibberishMessage() {
  const $el = $("<div>").addClass("msgAssistant pop-in msgNotFound");
  $el.attr("id", `msg_${Date.now()}`);

  const $header = $("<div>").addClass("msgMainIdea msgNotFoundHeader");
  $("<div>").addClass("msgMainIdeaLabel").text("Не понял запрос").appendTo($header);
  $("<div>").addClass("msgMainIdeaText")
    .text("Похоже, это не настоящее слово — попробуй сформулировать иначе")
    .appendTo($header);
  $el.append($header);

  const $body = $("<div>").addClass("msgBody");
  const $sec = $("<div>").addClass("msgSection");
  $("<div>").addClass("msgSectionTitle").text("💡 Например, можно спросить").appendTo($sec);
  const $ul = $("<ul>").css({ paddingLeft: "18px", color: "var(--text2)", lineHeight: "1.8" });
  ["Что такое чёрная дыра?", "Как работает двигатель?", "Древний Египет"].forEach(l =>
    $("<li>").text(l).appendTo($ul));
  $sec.append($ul);
  $body.append($sec);
  $el.append($body);

  $("#messages").append($el);
  switchPanel("chat");
  scrollMessages();
}

function addNotFoundMessage(query) {
  const $el = $("<div>").addClass("msgAssistant pop-in msgNotFound");
  $el.attr("id", `msg_${Date.now()}`);

  const $header = $("<div>").addClass("msgMainIdea msgNotFoundHeader");
  $("<div>").addClass("msgMainIdeaLabel").text("Статья не найдена").appendTo($header);
  $("<div>").addClass("msgMainIdeaText")
    .text(`По запросу «${query}» подходящей статьи в энциклопедии не нашлось`)
    .appendTo($header);
  $el.append($header);

  const stripped = query
    .replace(/^(что такое|как работает|как устроен[аоы]?|почему|зачем|расскажи про|расскажи о|объясни|кто такой|кто такая)\s+/i, "")
    .replace(/[?!.…]+$/, "").trim();
  const suggestion = stripped || query;

  const $body = $("<div>").addClass("msgBody");
  const $sec = $("<div>").addClass("msgSection");
  $("<div>").addClass("msgSectionTitle").text("💡 Попробуй переформулировать").appendTo($sec);
  const lines = [
    `Используй ключевое слово: «${suggestion}»`,
    "Напиши тему как в учебнике, без вопросов",
    "Проверь орфографию",
  ];
  const $ul = $("<ul>").css({ paddingLeft: "18px", color: "var(--text2)", lineHeight: "1.8" });
  lines.forEach(l => $("<li>").text(l).appendTo($ul));
  $sec.append($ul);
  $body.append($sec);
  $el.append($body);

  $("#messages").append($el);
  switchPanel("chat");
  scrollMessages();
}

function scrollMessages() {
  const panel = document.getElementById("panelChat");
  // Double rAF: first frame updates DOM layout, second frame reads correct scrollHeight
  requestAnimationFrame(() => requestAnimationFrame(() => {
    panel.scrollTo({ top: panel.scrollHeight, behavior: "smooth" });
  }));
}

// ---------------------------------------------------------------------------
// Typing effect
// ---------------------------------------------------------------------------
function typeText($el, text, speed=14) {
  $el.text("");
  const tokens = String(text||"").split(/(\s+)/);
  let i=0, timer;
  timer = setInterval(()=>{
    if (i>=tokens.length) { clearInterval(timer); return; }
    $el.append(document.createTextNode(tokens[i])); i++;
  }, speed);
}

// ---------------------------------------------------------------------------
// Quiz modal
// ---------------------------------------------------------------------------
const quiz = { items: [], current: 0, score: 0, revealed: false };

function openQuiz(items) {
  quiz.items = items || []; quiz.current = 0; quiz.score = 0; quiz.revealed = false;
  renderQuizStep();
  $("#quizModal").removeClass("hidden");
}

function renderQuizStep() {
  const $c = $("#quizModalContainer").empty();
  if (!quiz.items.length) return;
  if (quiz.current >= quiz.items.length) { renderQuizResult($c); return; }

  const item = quiz.items[quiz.current];
  const $card = $("<div>").addClass("quizCard pop-in");
  $("<div>").addClass("quizProgress").text(`Вопрос ${quiz.current+1} из ${quiz.items.length}`).appendTo($card);
  $("<p>").addClass("quizQuestion").text(item.question||"Вопрос").appendTo($card);

  if (!quiz.revealed) {
    $("<button>").addClass("quizRevealBtn").text("Показать ответ")
      .on("click", ()=>{ quiz.revealed=true; renderQuizStep(); }).appendTo($card);
  } else {
    $("<div>").addClass("quizAnswer pop-in")
      .append($("<span>").addClass("quizAnswerLabel").text("Ответ: "))
      .append(document.createTextNode(item.answer||""))
      .appendTo($card);
    const $row = $("<div>").addClass("quizSelfRow");
    $("<button>").addClass("quizSelfBtn correct").text("✓ Знал!")
      .on("click",()=>{ quiz.score++; quiz.current++; quiz.revealed=false; renderQuizStep(); }).appendTo($row);
    $("<button>").addClass("quizSelfBtn wrong").text("✗ Не знал")
      .on("click",()=>{ quiz.current++; quiz.revealed=false; renderQuizStep(); }).appendTo($row);
    $card.append($row);
  }
  $c.append($card);
}

function renderQuizResult($c) {
  const {score,items} = quiz;
  const total = items.length;
  const [msg,emoji] = score===total?["Отлично! Всё правильно!","🏆"]:
    score>=Math.ceil(total*.6)?["Молодец! Почти всё.","🌟"]:["Неплохо! Попробуй ещё раз.","💪"];
  const $res = $("<div>").addClass("quizResult pop-in");
  $("<div>").addClass("quizResultEmoji").text(emoji).appendTo($res);
  $("<p>").addClass("quizResultText").text(`${msg} (${score} из ${total})`).appendTo($res);
  $("<button>").addClass("quizRestartBtn").text("Пройти снова")
    .on("click",()=>{ quiz.current=0;quiz.score=0;quiz.revealed=false; renderQuizStep(); }).appendTo($res);
  $c.append($res);
}

// ---------------------------------------------------------------------------
// Rating
// ---------------------------------------------------------------------------
async function submitRating(historyKey, stars) {
  try {
    await apiFetch("/rate", {
      method: "POST",
      body: JSON.stringify({ history_key: historyKey, stars }),
    }, 10000);
  } catch(_) {}
}

// ---------------------------------------------------------------------------
// Speech — server-side neural TTS (edge-tts, ru-RU-SvetlanaNeural)
// ---------------------------------------------------------------------------
let _ttsAudio = null;

function _stopTts() {
  if (_ttsAudio) {
    _ttsAudio.pause();
    _ttsAudio.src = "";
    _ttsAudio = null;
  }
}

async function speakWhenReady(text, $btn) {
  if (!text) return;

  // If already playing — stop
  if ($btn && $btn.hasClass("speaking")) {
    _stopTts();
    $btn.removeClass("speaking").text("🔊 Озвучить");
    return;
  }

  if ($btn) $btn.addClass("speaking").text("⏹ Стоп");

  try {
    const res = await fetch(BACKEND_URL + "/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, voice: "ru-RU-SvetlanaNeural", rate: "-5%" }),
    });

    if (!res.ok) throw new Error("tts_http_" + res.status);

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);

    _stopTts();
    _ttsAudio = new Audio(url);
    _ttsAudio.onended = _ttsAudio.onerror = () => {
      URL.revokeObjectURL(url);
      _ttsAudio = null;
      if ($btn) $btn.removeClass("speaking").text("🔊 Озвучить");
    };
    _ttsAudio.play();
  } catch (e) {
    console.warn("TTS failed, falling back to browser voice:", e);
    if ($btn) $btn.removeClass("speaking").text("🔊 Озвучить");
    // Graceful fallback to Web Speech API
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "ru-RU"; u.rate = 0.87;
    const ruVoice = window.speechSynthesis.getVoices().find(v => v.lang.startsWith("ru") && v.localService);
    if (ruVoice) u.voice = ruVoice;
    if ($btn) {
      $btn.addClass("speaking").text("⏹ Стоп");
      u.onend = u.onerror = () => $btn.removeClass("speaking").text("🔊 Озвучить");
    }
    window.speechSynthesis.speak(u);
  }
}

async function copyText(mainIdea, text) {
  try {
    await navigator.clipboard.writeText([mainIdea, "", text].join("\n"));
    setStatus("Скопировано в буфер обмена.");
    setTimeout(()=>setStatus(""), 2000);
  } catch { setStatus("Браузер не разрешил копирование."); }
}

// ---------------------------------------------------------------------------
// Main simplify
// ---------------------------------------------------------------------------
async function doSimplify() {
  const query = $("#query").val().trim();
  if (!query) { setStatus("Введите тему или вопрос."); return; }

  // Create a new chat lazily on the first message of a session
  if (state.user && !state.chatId) {
    await createNewChat();
  }

  const age = state.user?.age || 10;
  const mode = "balanced";

  addUserMessage(query);
  $("#query").val("").css("height", "");
  $("#welcomeScreen").addClass("hidden");

  if (_isGibberish(query)) {
    addGibberishMessage();
    return;
  }

  setBusy(true, "Ищу статью в Рувики...");

  const body = {
    query,
    age,
    mode,
    enable_metrics: state.enableMetrics,
    chat_id: state.chatId || null,
    model_id: state.selectedModelId || null,
  };

  try {
    const res = await apiFetch("/simplify", {
      method: "POST",
      body: JSON.stringify(body),
    }, 180000);
    const data = await res.json();
    if (!res.ok) {
      const detail = data.detail || "";
      setBusy(false);
      if (res.status === 400) {
        // 400 means backend couldn't find or process the article — always show friendly bubble
        addNotFoundMessage(query);
      } else {
        setStatus(`Ошибка ${res.status}: ${detail || "сервер недоступен"}`);
        setTimeout(() => setStatus(""), 6000);
      }
      return;
    }
    setBusy(false, data.cached ? "Готово: ответ из кэша." : "Готово!");
    setTimeout(()=>setStatus(""),3000);
    switchPanel("chat");
    addAssistantMessage(data);
    if (state.user) loadSidebarChats();
  } catch(e) {
    setBusy(false, `Ошибка: ${e.message}`);
    console.error(e);
  }
}

// ---------------------------------------------------------------------------
// Auth UI
// ---------------------------------------------------------------------------
function openAuthModal(tab="login") {
  showAuthTab(tab);
  $("#authModal").removeClass("hidden");
  setTimeout(()=>$("#loginEmail,#regEmail").first().trigger("focus"),100);
}

function closeAuthModal() { $("#authModal").addClass("hidden"); }

function showAuthTab(tab) {
  $(".authTab").removeClass("active").filter(`[data-tab="${tab}"]`).addClass("active");
  $(".authForm").addClass("hidden");
  $(`#${tab}Form`).removeClass("hidden");
}

async function doLogin(e) {
  e.preventDefault();
  const email = $("#loginEmail").val().trim();
  const password = $("#loginPassword").val();
  $("#loginError").addClass("hidden");
  try {
    const res = await apiFetch("/auth/login", { method:"POST", body:JSON.stringify({email,password}) });
    const data = await res.json();
    if (!res.ok) { $("#loginError").removeClass("hidden").text(data.detail||"Ошибка входа"); return; }
    setToken(data.access_token);
    state.user = data.user;
    closeAuthModal();
    afterLogin();
  } catch(err) {
    $("#loginError").removeClass("hidden").text(err.message);
  }
}

async function doRegister(e) {
  e.preventDefault();
  const email = $("#regEmail").val().trim();
  const display_name = $("#regName").val().trim();
  const birth_date = $("#regBirthDate").val() || null;
  const password = $("#regPassword").val();
  $("#registerError").addClass("hidden");
  try {
    const res = await apiFetch("/auth/register", { method:"POST", body:JSON.stringify({email,display_name,birth_date,password}) });
    const data = await res.json();
    if (!res.ok) { $("#registerError").removeClass("hidden").text(data.detail||"Ошибка регистрации"); return; }
    setToken(data.access_token);
    state.user = data.user;
    closeAuthModal();
    afterLogin();
  } catch(err) {
    $("#registerError").removeClass("hidden").text(err.message);
  }
}

function renderUserWidget() {
  const $w = $("#userWidget").empty();
  if (!state.user) {
    $("<button>").addClass("loginBtn").attr("id","loginBtn").text("Войти")
      .on("click",()=>openAuthModal()).appendTo($w);
    return;
  }
  const u = state.user;
  const initials = (u.display_name||u.email||"?").slice(0,1).toUpperCase();
  const $av = $("<div>").addClass("userAvatar");
  if (u.avatar_url) {
    $("<img>").addClass("avatarImg").attr({src:u.avatar_url,alt:u.display_name}).appendTo($av);
  } else {
    $("<div>").addClass("avatarPlaceholder").text(initials).appendTo($av);
  }
  $("<span>").text(u.display_name||u.email||"").appendTo($av);
  $av.appendTo($w);
  $("<button>").addClass("logoutBtn").text("Выйти")
    .on("click", doLogout).appendTo($w);
}

function doLogout() {
  clearAuth();
  renderUserWidget();
  updateSidebarChats([]);
  setStatus("Вы вышли из аккаунта.");
  setTimeout(()=>setStatus(""),2000);
}

async function loadCurrentUser() {
  const token = getToken();
  if (!token) return;
  try {
    const res = await apiFetch("/auth/me", {}, 8000);
    if (!res.ok) { clearAuth(); return; }
    state.user = await res.json();
    state.token = token;
  } catch { clearAuth(); }
}

async function afterLogin() {
  renderUserWidget();
  await loadSidebarChats();
}

// ---------------------------------------------------------------------------
// OAuth token from URL hash
// ---------------------------------------------------------------------------
function checkOAuthToken() {
  const hash = window.location.hash;
  if (!hash) return;
  const params = new URLSearchParams(hash.slice(1));
  const token = params.get("token");
  const err = params.get("auth_error");
  if (token) {
    setToken(token);
    history.replaceState(null, "", window.location.pathname);
    loadCurrentUser().then(()=>{ renderUserWidget(); loadSidebarChats(); });
  } else if (err) {
    setStatus("Ошибка авторизации: " + err);
    history.replaceState(null, "", window.location.pathname);
  }
}

// ---------------------------------------------------------------------------
// Chats
// ---------------------------------------------------------------------------
function newChat() {
  state.chatId = null;
  $("#messages").empty();
  $("#welcomeScreen").removeClass("hidden");
  $(".sidebarChatItem").removeClass("active");
  switchPanel("chat");
}

async function createNewChat() {
  if (!state.user) return;
  try {
    const res = await apiFetch("/chats", { method: "POST" }, 8000);
    if (!res.ok) return;
    const data = await res.json();
    state.chatId = data.chat_id;
  } catch {}
}

async function deleteChat(chatId) {
  try {
    const res = await apiFetch(`/chats/${chatId}`, { method: "DELETE" }, 8000);
    if (!res.ok) return;
    if (state.chatId === chatId) {
      state.chatId = null;
      $("#messages").empty();
      $("#welcomeScreen").removeClass("hidden");
    }
    await loadSidebarChats();
  } catch {}
}

async function loadSidebarChats() {
  if (!state.user) { updateSidebarChats([]); return; }
  try {
    const res = await apiFetch("/chats", {}, 8000);
    if (!res.ok) return;
    const chats = await res.json();
    updateSidebarChats(chats);
  } catch {}
}

function updateSidebarChats(chats) {
  const $list = $("#sidebarChatsList").empty();
  if (!state.user || !chats.length) {
    $("<p>").addClass("sidebarChatsEmpty")
      .text(state.user ? "Нет сохранённых чатов" : "Войдите, чтобы сохранять историю")
      .appendTo($list);
    return;
  }
  chats.forEach(chat => {
    const $row = $("<div>").addClass("sidebarChatRow");
    $("<button>").addClass("sidebarChatItem")
      .toggleClass("active", chat.id === state.chatId)
      .text(chat.title || "Чат")
      .attr("title", chat.title)
      .on("click", ()=>openChat(chat.id))
      .appendTo($row);
    $("<button>").addClass("sidebarChatDel").attr("title","Удалить чат").html("&times;")
      .on("click", e=>{ e.stopPropagation(); deleteChat(chat.id); })
      .appendTo($row);
    $row.appendTo($list);
  });
  // Also update full panel
  renderChatsPanel(chats);
}

function renderChatsPanel(chats) {
  const $list = $("#chatsListFull").empty();
  if (!chats.length) {
    $("<p>").css("color","#94a3b8").text("Нет сохранённых чатов. Начните разговор!").appendTo($list);
    return;
  }
  chats.forEach(chat => {
    const d = new Date(chat.last_message_at);
    const dateStr = d.toLocaleDateString("ru-RU",{day:"2-digit",month:"short"});
    const $card = $("<div>").addClass("chatListCard")
      .append($("<span>").addClass("chatListIcon").text("💬"))
      .append(
        $("<div>").addClass("chatListInfo")
          .append($("<div>").addClass("chatListTitle").text(chat.title||"Чат"))
          .append($("<div>").addClass("chatListDate").text(dateStr))
      )
      .on("click", e=>{ if (!$(e.target).closest(".chatListDel").length){ openChat(chat.id); switchPanel("chat"); } });
    $("<button>").addClass("chatListDel").attr("title","Удалить чат").text("🗑")
      .on("click", e=>{ e.stopPropagation(); deleteChat(chat.id); })
      .appendTo($card);
    $card.appendTo($list);
  });
}

async function openChat(chatId) {
  if (!state.user) return;
  state.chatId = chatId;
  try {
    const res = await apiFetch(`/chats/${chatId}`, {}, 10000);
    if (!res.ok) return;
    const messages = await res.json();
    // Render chat history
    $("#messages").empty();
    $("#welcomeScreen").addClass("hidden");
    messages.forEach(msg => {
      if (msg.role === "assistant" && msg.response?.main_idea) {
        addUserMessage(msg.query);
        addAssistantMessage(msg.response);
      }
    });
    // Update sidebar active state
    $(".sidebarChatItem").removeClass("active");
    $(`.sidebarChatItem`).filter(function(){ return $(this).text().trim() === (messages[0]?.query||"").slice(0,60); }).addClass("active");
  } catch {}
}

// ---------------------------------------------------------------------------
// Health check & model setup
// ---------------------------------------------------------------------------
async function checkHealth() {
  try {
    const res = await fetch(BACKEND_URL + "/health", { cache:"no-store" });
    if (!res.ok) return;
    const data = await res.json();
    renderModelDropdown(data);
    renderOAuthButtons(data);
    $("#infoModelText").text(`Провайдер: ${data.provider}, модель: ${data.model}`);
  } catch {}
}

function renderModelDropdown(data) {
  const $dd = $("#modelDropdown").empty();
  // Fast / Quality based on enable_metrics
  const opts = [
    { label:"⚡ Быстрый режим", sub:"без метрик — чистая скорость", metrics:false, modelId:"" },
    { label:"✨ Качественный", sub:"с проверкой фактов", metrics:true, modelId:"" },
  ];
  // Add actual models if multiple available
  if (data.available_models?.length > 1) {
    data.available_models.forEach(m => {
      opts.push({ label:`🤖 ${m.label}`, sub:m.description, metrics:true, modelId:m.id });
    });
  }
  opts.forEach((o,i) => {
    const $btn = $("<button>").addClass("modelOption" + (i===0?" active":""))
      .attr({ "data-metrics": o.metrics, "data-model-id": o.modelId })
      .on("click", ()=>selectModel(o.label, o.metrics, o.modelId))
      .appendTo($dd);
    $("<span>").text(o.label).appendTo($btn);
    $("<span>").addClass("modelOptionSub").text(o.sub).appendTo($btn);
  });
  // Init state
  state.enableMetrics = false;
  state.selectedModelId = "";
  $("#modelBtnLabel").text(opts[0].label);
}

function selectModel(label, metrics, modelId) {
  state.enableMetrics = metrics;
  state.selectedModelId = modelId;
  $("#modelBtnLabel").text(label);
  $(".modelOption").removeClass("active");
  $(".modelOption").filter(function(){
    return $(this).find("span:first").text() === label;
  }).addClass("active");
  $("#modelDropdown").addClass("hidden");
}

function renderOAuthButtons(data) {
  const $ob = $("#oauthButtons");
  if (data.vk_enabled || data.yandex_enabled) {
    $ob.removeClass("hidden");
    if (!data.vk_enabled) $("#vkBtn").addClass("hidden");
    if (!data.yandex_enabled) $("#yandexBtn").addClass("hidden");
  } else {
    $ob.addClass("hidden");
  }
}

// ---------------------------------------------------------------------------
// Panel switching
// ---------------------------------------------------------------------------
function switchPanel(name) {
  state.currentPanel = name;
  $(".panel").addClass("hidden");
  $(`#panel${name.charAt(0).toUpperCase()+name.slice(1)}`).removeClass("hidden");
  $(".navItem").removeClass("active");
  $(`.navItem[data-panel="${name}"]`).addClass("active");
  if (name === "chats") loadSidebarChats();
}

// ---------------------------------------------------------------------------
// Speech recognition — with pause / resume
// ---------------------------------------------------------------------------
function _updateMicBtn() {
  const $btn = $("#micBtn");
  if (state.micPhase === "listening") {
    $btn.html("⏸").removeClass("micPaused").addClass("micListening")
        .prop("disabled", false).attr("title", "Пауза");
  } else if (state.micPhase === "paused") {
    $btn.html("▶").removeClass("micListening").addClass("micPaused")
        .prop("disabled", false).attr("title", "Продолжить запись");
  } else {
    $btn.html("🎤").removeClass("micListening micPaused")
        .prop("disabled", false).attr("title", "Голосовой ввод");
  }
}

function _micStop() {
  try { state.recognition.stop(); } catch(_) {}
}

function _micAbort() {
  try { state.recognition.abort(); } catch(_) {}
}

function setupSpeech() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { $("#micBtn").prop("disabled", true).html("🚫"); return; }

  function createRecognition() {
    const rec = new SR();
    rec.lang = "ru-RU";
    rec.interimResults = true;
    rec.continuous = false;      // keep false for broadest browser compat

    rec.onstart = () => {
      setStatus("Слушаю… 🔴");
      _updateMicBtn();
    };

    rec.onresult = (e) => {
      let interimPart = "";
      let finalPart = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) {
          finalPart += e.results[i][0].transcript;
        } else {
          interimPart += e.results[i][0].transcript;
        }
      }
      if (finalPart) {
        state.micAccumulated = (state.micAccumulated + " " + finalPart).trim();
      }
      const display = (state.micAccumulated + (interimPart ? " " + interimPart : "")).trim();
      $("#query").val(display);
      autoGrow($("#query")[0]);
    };

    rec.onerror = (e) => {
      // "aborted" fires when we call abort() intentionally — ignore it
      if (e.error !== "aborted" && e.error !== "no-speech") {
        setStatus("Не удалось распознать голос.");
      }
      if (e.error !== "aborted") {
        state.micPhase = "idle";
        state.micAccumulated = "";
        _updateMicBtn();
      }
    };

    rec.onend = () => {
      if (state.micPhase === "listening") {
        // Natural end (silence timeout) — auto-restart to continue recording
        try {
          state.recognition.start();
        } catch (_) {
          // Restart failed (e.g. page hidden) — gracefully stop
          state.micPhase = "idle";
          setStatus("Распознано.");
          setTimeout(() => setStatus(""), 2000);
          _updateMicBtn();
        }
      } else if (state.micPhase === "paused") {
        setStatus("Запись на паузе — нажми ▶ чтобы продолжить");
        _updateMicBtn();
      } else {
        setStatus("");
        _updateMicBtn();
      }
    };

    return rec;
  }

  state.recognition = createRecognition();
}

function autoGrow(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 140) + "px";
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
$(async function () {
  // Resolve backend URL before any requests
  await detectBackend();

  checkOAuthToken();
  await loadCurrentUser();
  renderUserWidget();

  if (state.user) {
    await loadSidebarChats();
  }

  checkHealth();
  setupSpeech();

  // Show local-mode indicator
  if (IS_LOCAL_MODE) {
    $("#localModeIndicator").removeClass("hidden");
  }

  // Submit — also reset mic if it was active
  $("#submitBtn").on("click", () => {
    if (state.micPhase !== "idle") {
      state.micPhase = "idle";
      state.micAccumulated = "";
      _micAbort();
      _updateMicBtn();
    }
    doSimplify();
  });
  $("#query").on("keydown", e=>{
    if (e.key==="Enter" && !e.shiftKey) {
      e.preventDefault();
      if (state.micPhase !== "idle") {
        state.micPhase = "idle"; state.micAccumulated = ""; _micAbort(); _updateMicBtn();
      }
      doSimplify();
    }
  }).on("input", function(){ autoGrow(this); });

  // Mic — idle → listening → paused → listening → …
  $("#micBtn").on("click", () => {
    if (!state.recognition) return;
    if (state.micPhase === "idle") {
      state.micAccumulated = "";
      state.micPhase = "listening";
      _updateMicBtn();
      try { state.recognition.start(); } catch(_) {}
    } else if (state.micPhase === "listening") {
      // Pause: set phase first so onend knows not to restart
      state.micPhase = "paused";
      _micStop();
    } else if (state.micPhase === "paused") {
      // Resume: start a fresh session; results append to micAccumulated
      state.micPhase = "listening";
      _updateMicBtn();
      try { state.recognition.start(); } catch(_) {}
    }
  });

  // Example chips
  $(document).on("click", ".exampleChip", function(){
    $("#query").val($(this).data("query"));
    autoGrow($("#query")[0]);
    doSimplify();
  });

  // Nav
  $(".navItem").on("click", function(){ switchPanel($(this).data("panel")); });

  // New chat
  $("#newChatBtn").on("click", newChat);

  // Sidebar toggle
  function closeMobileSidebar() {
    $("#sidebar").removeClass("mobileOpen");
    $("#sidebarBackdrop").addClass("hidden");
  }

  $("#sidebarToggle").on("click", ()=>{
    const $s=$("#sidebar");
    if (window.innerWidth<=640) {
      const opening = !$s.hasClass("mobileOpen");
      $s.toggleClass("mobileOpen");
      $("#sidebarBackdrop").toggleClass("hidden", !opening);
    } else {
      $s.toggleClass("collapsed");
    }
  });

  // Tap backdrop to close sidebar
  $("#sidebarBackdrop").on("click", closeMobileSidebar);

  // Close sidebar on mobile after nav/new-chat click
  $(".navItem, #newChatBtn").on("click", ()=>{
    if (window.innerWidth<=640) closeMobileSidebar();
  });

  // Model selector
  $("#modelBtn").on("click", e=>{
    e.stopPropagation();
    $("#modelDropdown").toggleClass("hidden");
  });
  $(document).on("click", ()=>$("#modelDropdown").addClass("hidden"));
  $("#modelDropdown").on("click", e=>e.stopPropagation());

  // Auth modal
  $("#loginBtn, #userWidget").on("click", ".loginBtn", ()=>openAuthModal());
  $("#authModalClose").on("click", closeAuthModal);
  $("#authModal").on("click", e=>{ if(e.target===e.currentTarget) closeAuthModal(); });
  $(".authTab").on("click", function(){ showAuthTab($(this).data("tab")); });
  $("#loginForm").on("submit", doLogin);
  $("#registerForm").on("submit", doRegister);

  // VK / Yandex OAuth
  $("#vkBtn").on("click", ()=>{ window.location.href = BACKEND_URL + "/auth/vk"; });
  $("#yandexBtn").on("click", ()=>{ window.location.href = BACKEND_URL + "/auth/yandex"; });

  // Quiz modal
  $("#quizModalClose").on("click", ()=>$("#quizModal").addClass("hidden"));
  $("#quizModal").on("click", e=>{ if(e.target===e.currentTarget) $("#quizModal").addClass("hidden"); });

  // ── Sidebar footer buttons ──────────────────────────────────────────────
  $("#sidebarLogoutBtn").on("click", ()=>{
    doLogout();
    if (window.innerWidth<=640) closeMobileSidebar();
  });

  $("#openAboutBtn").on("click", ()=>{
    switchPanel("info");
    if (window.innerWidth<=640) closeMobileSidebar();
  });


  $("#deleteAllChatsBtn").on("click", async ()=>{
    if (!state.user) { openAuthModal(); return; }
    if (!confirm("Удалить все чаты? Это действие необратимо.")) return;
    try {
      const res = await apiFetch("/chats", {}, 8000);
      if (!res.ok) return;
      const chats = await res.json();
      await Promise.all(chats.map(c => apiFetch(`/chats/${c.id}`, { method:"DELETE" }, 5000)));
      state.chatId = null;
      $("#messages").empty();
      $("#welcomeScreen").removeClass("hidden");
      updateSidebarChats([]);
      switchPanel("chat");
    } catch {}
    if (window.innerWidth<=640) closeMobileSidebar();
  });

  // Escape closes modals
  $(document).on("keydown", e=>{
    if (e.key==="Escape") {
      closeAuthModal();
      $("#quizModal").addClass("hidden");
      $("#modelDropdown").addClass("hidden");
    }
  });
});

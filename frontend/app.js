const INTEREST_PRESETS = [
  { id: "nature", label: "Природа и животные", emoji: "🐾" },
  { id: "space", label: "Космос и наука", emoji: "🚀" },
  { id: "sport", label: "Спорт и игры", emoji: "⚽" },
  { id: "art", label: "Рисование и музыка", emoji: "🎨" },
  { id: "tech", label: "Компы и роботы", emoji: "🤖" },
  { id: "history", label: "История и путешествия", emoji: "🌍" },
  { id: "books", label: "Книги и сказки", emoji: "📖" },
  { id: "food", label: "Еда и кухня", emoji: "🍎" },
];

const STORAGE_INTERESTS = "ruwiki_interests_v1";
const STORAGE_NOTES = "ruwiki_child_notes_v1";
const LOADING_TIPS = [
  "Ищем хорошее объяснение...",
  "Проверяем кэш похожих вопросов...",
  "Выбираем возрастной стиль...",
  "Придумываем понятную аналогию...",
  "Сверяем факты с источником...",
  "Считаем ROUGE и BLEURT‑proxy...",
  "Почти готово!",
];

/** Таймаут health (мс). Увеличьте при очень медленной сети; без таймаута запрос «висит» минутами. */
const HEALTH_FETCH_MS = 15000;
const CACHE_FETCH_MS = 30000;
/** Запрос к /simplify может долго ждать LLM — отдельный лимит. */
const SIMPLIFY_FETCH_MS = 180000;

const state = {
  simplifiedText: "",
  recognition: null,
  progressTimer: null,
  statusTimer: null,
  typingTimer: null,
  backendUrl: "",
};

async function fetchWithTimeout(url, options = {}, timeoutMs = 100000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: ctrl.signal });
  } finally {
    clearTimeout(t);
  }
}

/**
 * Без схемы http(s) браузер воспринимает строку как ПУТЬ на текущем сайте
 * (например запрос уходит на порт 5173 вместо 8000 и вечно «не коннектится»).
 */
function normalizeBackendUrl(raw) {
  let s = String(raw ?? "").trim();
  if (!s) return "http://127.0.0.1:8000";
  if (!/^https?:\/\//i.test(s)) {
    s = "http://" + s.replace(/^\/+/, "");
  }
  try {
    const u = new URL(s);
    if (!u.hostname) throw new Error("no host");
    return `${u.protocol}//${u.host}`;
  } catch {
    return "http://127.0.0.1:8000";
  }
}

/** Приводит поле к каноническому виду и сохраняет в localStorage. */
function syncBackendUrlInput() {
  const n = normalizeBackendUrl($("#backendUrl").val());
  $("#backendUrl").val(n);
  localStorage.setItem("ruwiki_backend_url", n);
  state.backendUrl = n;
  return n;
}

function initializeBackendUrl() {
  const savedUrl = localStorage.getItem("ruwiki_backend_url");
  const url = normalizeBackendUrl(savedUrl || "http://127.0.0.1:8000");
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

function renderInterestChips() {
  const $wrap = $("#interestChips").empty();
  const saved = new Set(
    (localStorage.getItem(STORAGE_INTERESTS) || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
  );
  INTEREST_PRESETS.forEach((item) => {
    const active = saved.has(item.label);
    $("<button>")
      .attr("type", "button")
      .addClass("interestChip")
      .toggleClass("is-selected", active)
      .attr("data-topic", item.label)
      .append($("<span>").addClass("chipEmoji").text(item.emoji))
      .append($("<span>").text(item.label))
      .appendTo($wrap);
  });
}

function getSelectedInterests() {
  const topics = [];
  $("#interestChips .interestChip.is-selected").each(function () {
    topics.push($(this).data("topic"));
  });
  return topics;
}

function persistKidPrefs() {
  localStorage.setItem(STORAGE_INTERESTS, getSelectedInterests().join(","));
  localStorage.setItem(STORAGE_NOTES, $("#childNotes").val().trim());
}

function setBusy(isBusy, message = "") {
  $("#submitBtn").prop("disabled", isBusy);
  $("#clearBtn").prop("disabled", isBusy);
  $("#submitBtn").text(isBusy ? "Упрощаю..." : "Упростить текст");
  $(".controls").toggleClass("isBusy", isBusy);
  $("#progress").toggleClass("hidden", !isBusy);
  $("#status").text(message);
  if (isBusy) {
    startProgress();
    startLoadingTips(message);
  } else {
    stopProgress();
    stopLoadingTips();
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
  }, 1200);
}

function stopProgress() {
  clearInterval(state.progressTimer);
  state.progressTimer = null;
}

function startLoadingTips(initialMessage) {
  let i = 0;
  clearInterval(state.statusTimer);
  $("#status").text(initialMessage || LOADING_TIPS[0]);
  state.statusTimer = setInterval(() => {
    i = (i + 1) % LOADING_TIPS.length;
    $("#status").text(LOADING_TIPS[i]);
  }, 1500);
}

function stopLoadingTips() {
  clearInterval(state.statusTimer);
  state.statusTimer = null;
}

function renderList($el, items, emptyText, renderItem) {
  $el.empty();
  if (!items || items.length === 0) {
    $el.append($("<li>").text(emptyText));
    return;
  }
  items.forEach((item) => $el.append(renderItem(item)));
}

function renderGlossaryItem(item) {
  return $("<li>")
    .append($("<strong>").text(item.term || "Термин"))
    .append(document.createTextNode(`: ${item.definition || "простое объяснение появится здесь"}`));
}

function renderTextItem(text) {
  return $("<li>").text(text);
}

function renderQuiz(items) {
  const $quiz = $("#quiz").empty();
  if (!items || items.length === 0) {
    $quiz.append($("<p>").addClass("emptyText").text("Вопросы появятся после следующего ответа."));
    return;
  }
  items.forEach((item, index) => {
    $("<details>")
      .addClass("quizItem pop-in")
      .css("animation-delay", `${index * 0.06}s`)
      .append($("<summary>").text(`${index + 1}. ${item.question || "Вопрос"}`))
      .append($("<p>").text(item.answer || "Ответ появится здесь."))
      .appendTo($quiz);
  });
}

function formatPct(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return null;
  return `${Math.round(Number(v))}%`;
}

function setAccuracyUI(acc) {
  const pct = typeof acc?.percent === "number" ? acc.percent : null;
  const label = acc?.metric_label || "Достоверность к источнику";
  const hint = acc?.hint || "";
  const detail = acc?.detail_summary || "";
  $("#accuracyLabel").text(label);
  $("#accuracyHint").text(hint);
  $("#accuracyDetail").text(detail);

  const $pills = $("#accuracyPills").empty();
  const b = acc?.breakdown || {};
  const ne = b.named_entities;
  const yr = b.years;
  const ta = b.term_anchors;
  if (ne?.total) {
    $("<span>")
      .addClass("accuracyPill")
      .text(`Имена и места ${ne.kept}/${ne.total}${formatPct(ne.percent) ? ` · ${formatPct(ne.percent)}` : ""}`)
      .appendTo($pills);
  }
  if (yr?.total) {
    $("<span>")
      .addClass("accuracyPill")
      .text(`Годы ${yr.kept}/${yr.total}${formatPct(yr.percent) ? ` · ${formatPct(yr.percent)}` : ""}`)
      .appendTo($pills);
  }
  if (ta?.total) {
    $("<span>")
      .addClass("accuracyPill")
      .text(`Ключевые слова ${ta.kept}/${ta.total}${formatPct(ta.percent) ? ` · ${formatPct(ta.percent)}` : ""}`)
      .appendTo($pills);
  }
  const kt = acc?.key_terms || acc?.evaluation?.key_terms;
  if (kt?.total) {
    $("<span>")
      .addClass("accuracyPill")
      .text(`Обязательные термины ${kt.kept}/${kt.total}${formatPct(kt.percent) ? ` · ${formatPct(kt.percent)}` : ""}`)
      .appendTo($pills);
  }

  if (pct === null || Number.isNaN(pct)) {
    $("#accuracyPercent").text("—");
    $("#accuracyArc").attr("stroke-dasharray", "0, 100");
    return;
  }
  const clamped = Math.max(0, Math.min(100, pct));
  $("#accuracyPercent").text(`${clamped}%`);
  $("#accuracyArc").attr("stroke-dasharray", `${clamped}, ${100 - clamped}`);
}

function setEvaluationUI(data) {
  const ev = data?.evaluation || {};
  const timings = data?.timings_ms || {};
  const pct = (v) => (typeof v === "number" ? `${Math.round(v * 100)}%` : "—");
  $("#rouge1Score").text(pct(ev.rouge_1));
  $("#rougeLScore").text(pct(ev.rouge_l));
  $("#bleurtScore").text(pct(ev.bleurt_proxy));
  $("#latencyScore").text(data?.cached ? "<50 мс" : timings.total ? `${timings.total} мс` : "—");
  $(".qualityPanel").toggleClass("isGood", Boolean(ev.ok || data?.cached));
  if (data?.accuracy) {
    data.accuracy.evaluation = ev;
  }
}

function typeText($el, text, speed = 16) {
  clearInterval(state.typingTimer);
  $el.text("");
  const words = String(text || "").split(/(\s+)/);
  let i = 0;
  state.typingTimer = setInterval(() => {
    if (i >= words.length) {
      clearInterval(state.typingTimer);
      state.typingTimer = null;
      return;
    }
    $el.append(document.createTextNode(words[i]));
    i += 1;
  }, speed);
}

function renderNumberedList($ol, items, emptyClass) {
  $ol.empty();
  if (!items || items.length === 0) {
    $(emptyClass).addClass("hidden");
    return false;
  }
  $(emptyClass).removeClass("hidden");
  items.forEach((text, i) => {
    $("<li>")
      .addClass("pop-in")
      .css("animation-delay", `${i * 0.05}s`)
      .text(text)
      .appendTo($ol);
  });
  return true;
}

function renderResult(data) {
  state.simplifiedText = data.simplified_text || "";
  $("#result").removeClass("hidden");
  setEvaluationUI(data);
  setAccuracyUI(data.accuracy);

  $("#sourceTitle").text(data.source_title);
  $("#sourceUrl").attr("href", data.source_url).text(data.source_url);
  $("#mainIdea").text(data.main_idea || "Главная мысль выделена в объяснении ниже.");
  $("#originalText").text(data.original_text || "");
  typeText($("#simplifiedText"), state.simplifiedText, data.cached ? 4 : 14);

  renderNumberedList($("#learningSteps"), data.learning_steps, "#learningBlock");

  if (data.reasoning_steps && data.reasoning_steps.length) {
    $("#reasoningDetails").removeClass("hidden");
    $("#reasoningSteps").empty();
    data.reasoning_steps.forEach((text, i) => {
      $("<li>")
        .addClass("pop-in")
        .css("animation-delay", `${i * 0.04}s`)
        .text(text)
        .appendTo("#reasoningSteps");
    });
  } else {
    $("#reasoningDetails").addClass("hidden");
  }

  renderList($("#glossary"), data.glossary, "Термины не понадобились.", renderGlossaryItem);
  renderList($("#analogies"), data.analogies, "Пример не понадобился.", renderTextItem);
  renderQuiz(data.quiz);
  $("#meta").text(
    JSON.stringify(
      {
        cached: data.cached,
        model: data.model,
        quality: data.quality,
        accuracy: data.accuracy,
        evaluation: data.evaluation,
        age_group: data.age_group,
        verifier: data.verifier,
        timings_ms: data.timings_ms,
      },
      null,
      2
    )
  );
  requestAnimationFrame(() => {
    document.getElementById("result").scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

async function simplify() {
  const backendUrl = syncBackendUrlInput();
  const query = $("#query").val().trim();
  const age = Number($("#age").val());
  const mode = $("#mode").val();
  const interest_topics = getSelectedInterests();
  const child_notes = $("#childNotes").val().trim();

  if (!query) {
    $("#status").text("Введите название статьи или темы.");
    $("#query").trigger("focus");
    return;
  }

  saveBackendUrl(backendUrl);
  persistKidPrefs();

  setBusy(true, "Ищу статью в Рувики...");
  try {
    const body = {
      query,
      age,
      mode,
      interest_topics,
      child_notes,
    };
    const res = await fetchWithTimeout(
      `${backendUrl}/simplify`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        mode: "cors",
        cache: "no-store",
      },
      SIMPLIFY_FETCH_MS
    );
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || `Ошибка сервера: ${res.status}`);
    }
    renderResult(data);
    setBusy(false, data.cached ? "Готово: ответ взят из кэша." : "Готово: текст стал проще!");
  } catch (e) {
    setBusy(false, `Ошибка: ${e.message}`);
    console.error("Simplify error:", e);
  }
}

async function checkHealth() {
  const backendUrl = syncBackendUrlInput();
  const $b = $("#healthBadge");
  const sec = Math.round(HEALTH_FETCH_MS / 1000);
  $b.removeClass("offline").addClass("checking").text("Связь с API…");
  try {
    const res = await fetchWithTimeout(
      `${backendUrl}/health`,
      { method: "GET", cache: "no-store", mode: "cors" },
      HEALTH_FETCH_MS
    );
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    const label = data.api_key_configured
      ? `${data.provider}: ${data.model}`
      : "API-ключ не настроен";
    $b.removeClass("offline checking").text(label);
  } catch (e) {
    const abort = e && e.name === "AbortError";
    const failedFetch = e && String(e.message || "").toLowerCase().includes("failed to fetch");
    let msg;
    if (abort) {
      msg = `Нет ответа за ${sec} с — проверьте адрес и что uvicorn слушает этот хост/порт`;
    } else if (failedFetch) {
      msg =
        "Сеть/CORS: откройте сайт через http://127.0.0.1:PORT (не file://), адрес API — полный с http://";
    } else {
      const detail = e && e.message ? String(e.message).slice(0, 96) : "";
      msg = detail ? `Ошибка: ${detail}` : "Backend недоступен";
    }
    $b.addClass("offline").text(msg);
    console.error("Health check error:", backendUrl, e);
  } finally {
    $b.removeClass("checking");
  }
}

async function clearCache() {
  const backendUrl = syncBackendUrlInput();
  $("#clearBtn").prop("disabled", true).text("Очищаю...");
  try {
    const res = await fetchWithTimeout(
      `${backendUrl}/cache`,
      { method: "DELETE", mode: "cors", cache: "no-store" },
      CACHE_FETCH_MS
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "Ошибка очистки");
    }
    $("#status").text("Кэш очищен. Следующий ответ будет сгенерирован заново.");
  } catch (e) {
    $("#status").text(`Не удалось очистить кэш: ${e.message}`);
  } finally {
    $("#clearBtn").prop("disabled", false).text("Очистить кэш");
  }
}

async function copyResult() {
  if (!state.simplifiedText) {
    return;
  }
  const text = [$("#mainIdea").text(), "", state.simplifiedText].join("\n");
  try {
    await navigator.clipboard.writeText(text);
    $("#status").text("Результат скопирован в буфер обмена.");
  } catch {
    $("#status").text("Браузер не разрешил копирование. Выделите текст вручную.");
  }
}

function setupSpeechRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    $("#micBtn").prop("disabled", true).text("Микрофон недоступен");
    return;
  }

  state.recognition = new SpeechRecognition();
  state.recognition.lang = "ru-RU";
  state.recognition.interimResults = false;
  state.recognition.onstart = () => $("#status").text("Слушаю...");
  state.recognition.onresult = (event) => {
    const text = event.results[0][0].transcript;
    $("#query").val(text);
    $("#status").text("Распознано.");
  };
  state.recognition.onerror = () => $("#status").text("Не удалось распознать голос.");
  state.recognition.onend = () => $("#micBtn").prop("disabled", false);
}

function speak() {
  if (!state.simplifiedText) {
    return;
  }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(state.simplifiedText);
  utterance.lang = "ru-RU";
  utterance.rate = 0.92;
  window.speechSynthesis.speak(utterance);
}

$(function () {
  initializeBackendUrl();
  renderInterestChips();
  const notesSaved = localStorage.getItem(STORAGE_NOTES);
  if (notesSaved) {
    $("#childNotes").val(notesSaved);
  }

  setupSpeechRecognition();
  checkHealth();

  $("#interestChips").on("click", ".interestChip", function () {
    $(this).toggleClass("is-selected");
    persistKidPrefs();
  });

  $("#submitBtn").on("click", simplify);
  $("#copyBtn").on("click", copyResult);
  $("#clearBtn").on("click", clearCache);
  $("#backendUrl").on("change blur", function () {
    saveBackendUrl($(this).val());
    checkHealth();
  });

  $(".exampleBtn").on("click", function () {
    $("#query").val($(this).data("query"));
    simplify();
  });

  $("#query").on("keydown", (e) => {
    if (e.ctrlKey && e.key === "Enter") {
      simplify();
    }
  });

  $("#micBtn").on("click", () => {
    if (!state.recognition) {
      return;
    }
    $("#micBtn").prop("disabled", true);
    state.recognition.start();
  });

  $("#speakBtn").on("click", speak);

  $("#query").focus();

  if (!localStorage.getItem("ruwiki_first_visit")) {
    $("#status").text("Выбери интересы, введи тему или нажми пример.");
    localStorage.setItem("ruwiki_first_visit", "true");
  }
});

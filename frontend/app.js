const state = {
  simplifiedText: "",
  recognition: null,
  progressTimer: null,
  backendUrl: "",
};

function getDefaultBackendUrl() {
  // Для production используем Railway
  return "https://ruwiki-backend-production.up.railway.app";
}

function initializeBackendUrl() {
  const savedUrl = localStorage.getItem('ruwiki_backend_url');
  const defaultUrl = getDefaultBackendUrl();
  const url = savedUrl || defaultUrl;
  $('#backendUrl').val(url);
  state.backendUrl = url;
  return url;
}

function saveBackendUrl(url) {
  localStorage.setItem('ruwiki_backend_url', url);
  state.backendUrl = url;
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
  } else {
    stopProgress();
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
      .addClass("quizItem")
      .append($("<summary>").text(`${index + 1}. ${item.question || "Вопрос"}`))
      .append($("<p>").text(item.answer || "Ответ появится здесь."))
      .appendTo($quiz);
  });
}

function renderResult(data) {
  state.simplifiedText = data.simplified_text || "";
  $("#result").removeClass("hidden");
  $("#sourceTitle").text(data.source_title);
  $("#sourceUrl").attr("href", data.source_url).text(data.source_url);
  $("#mainIdea").text(data.main_idea || "Главная мысль выделена в объяснении ниже.");
  $("#originalText").text(data.original_text || "");
  $("#simplifiedText").text(state.simplifiedText);
  renderList($("#glossary"), data.glossary, "Термины не понадобились.", renderGlossaryItem);
  renderList($("#analogies"), data.analogies, "Пример не понадобился.", renderTextItem);
  renderQuiz(data.quiz);
  $("#meta").text(JSON.stringify({
    cached: data.cached,
    model: data.model,
    provider: data.provider,
    quality: data.quality,
    verifier: data.verifier,
    timings_ms: data.timings_ms,
  }, null, 2));
  document.getElementById("result").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function simplify() {
  const backendUrl = $("#backendUrl").val().replace(/\/$/, "");
  const query = $("#query").val().trim();
  const age = Number($("#age").val());
  const mode = $("#mode").val();

  if (!query) {
    $("#status").text("Введите название статьи или темы.");
    $("#query").trigger("focus");
    return;
  }

  // Сохраняем URL backend
  saveBackendUrl(backendUrl);

  setBusy(true, "Ищу статью в Рувики...");
  try {
    const res = await fetch(`${backendUrl}/simplify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, age, mode }),
    });
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
  const backendUrl = $("#backendUrl").val().replace(/\/$/, "");
  try {
    const res = await fetch(`${backendUrl}/health`);
    if (!res.ok) {
      throw new Error("healthcheck failed");
    }
    const data = await res.json();
    const label = data.api_key_configured
      ? `${data.provider}: ${data.model}`
      : "API-ключ не настроен";
    $("#healthBadge").removeClass("offline").text(label);
  } catch (e) {
    $("#healthBadge").addClass("offline").text("Backend недоступен");
    console.error("Health check error:", e);
  }
}

async function clearCache() {
  const backendUrl = $("#backendUrl").val().replace(/\/$/, "");
  $("#clearBtn").prop("disabled", true).text("Очищаю...");
  try {
    const res = await fetch(`${backendUrl}/cache`, { method: "DELETE" });
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
  const text = [
    $("#mainIdea").text(),
    "",
    state.simplifiedText,
  ].join("\n");
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

function showNotification(message, type = "info") {
  $("#status").text(message);
}

$(function () {
  // Инициализация backend URL
  initializeBackendUrl();
  
  setupSpeechRecognition();
  checkHealth();
  
  $("#submitBtn").on("click", simplify);
  $("#copyBtn").on("click", copyResult);
  $("#clearBtn").on("click", clearCache);
  $("#backendUrl").on("change", function() {
    saveBackendUrl($(this).val().replace(/\/$/, ""));
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
  
  // Автофокус на поле ввода
  $("#query").focus();
  
  // Показать подсказку при первом посещении
  if (!localStorage.getItem('ruwiki_first_visit')) {
    $("#status").text("Введите тему или нажмите на пример выше. Backend развернут на Railway.");
    localStorage.setItem('ruwiki_first_visit', 'true');
  }
});
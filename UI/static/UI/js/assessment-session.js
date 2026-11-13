(() => {
  "use strict";

  const root = document.getElementById("r9-assessment");
  if (!root) return;

  const csrf = document.getElementById("r9-csrf").value;
  const title = document.getElementById("r9-assessment-title");
  const status = document.getElementById("r9-assessment-status");
  const body = document.getElementById("r9-assessment-body");
  const nav = document.getElementById("r9-question-nav");
  const actions = document.getElementById("r9-assessment-actions");
  const prev = document.getElementById("r9-prev");
  const next = document.getElementById("r9-next");
  const complete = document.getElementById("r9-complete");
  const abandon = document.getElementById("r9-abandon");

  let assessment = null;
  let currentIndex = 0;

  function qUrl(template, id) {
    return template.replace("/0/", `/${id}/`);
  }

  async function requestJSON(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
    });
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) {
      throw new Error(data.detail || data.error || `HTTP ${response.status}`);
    }
    return data;
  }

  function postJSON(url, payload = {}) {
    return requestJSON(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf,
      },
      body: JSON.stringify(payload),
    });
  }

  async function markViewed(question) {
    try {
      await fetch(qUrl(root.dataset.viewedTemplate, question.session_question_id), {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrf },
      });
    } catch (_) {
      // Viewing telemetry must not make the assessment UI unusable.
    }
  }

  function buildNav() {
    nav.replaceChildren();
    assessment.questions.forEach((question, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = String(index + 1);
      button.classList.toggle("is-current", index === currentIndex);
      button.classList.toggle("is-answered", Boolean(question.current_answer));
      button.addEventListener("click", () => {
        currentIndex = index;
        renderQuestion();
      });
      nav.appendChild(button);
    });
  }

  function renderQuestion() {
    const row = assessment.questions[currentIndex];
    title.textContent = `Câu ${currentIndex + 1} / ${assessment.question_count}`;
    status.textContent = `${assessment.answered_question_count} câu đã trả lời`;
    body.replaceChildren();

    const prompt = document.createElement("h2");
    prompt.textContent = row.question.question_text;
    body.appendChild(prompt);

    const options = document.createElement("div");
    options.className = "r9-question-options";

    row.question.options.forEach((option) => {
      const label = document.createElement("label");
      label.className = "r9-option";
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "answer";
      input.value = option.option_key;
      input.checked = row.current_answer?.selected_option_key === option.option_key;
      const text = document.createElement("span");
      text.textContent = `${option.option_key}. ${option.option_text}`;
      input.addEventListener("change", async () => {
        input.disabled = true;
        try {
          await postJSON(
            qUrl(root.dataset.answerTemplate, row.session_question_id),
            { selected_option_key: option.option_key }
          );
          if (!row.current_answer) assessment.answered_question_count += 1;
          row.current_answer = { selected_option_key: option.option_key };
          status.textContent = `${assessment.answered_question_count} câu đã trả lời`;
          buildNav();
        } catch (error) {
          status.textContent = error.message;
        } finally {
          input.disabled = false;
        }
      });
      label.append(input, text);
      options.appendChild(label);
    });

    body.appendChild(options);
    prev.disabled = currentIndex === 0;
    next.disabled = currentIndex >= assessment.questions.length - 1;
    buildNav();
    markViewed(row);
  }

  async function showResult() {
    try {
      const result = await requestJSON(root.dataset.resultUrl);
      const summary = result.summary || {};
      title.textContent = "Đã hoàn thành bài đánh giá";
      body.innerHTML = "";
      const box = document.createElement("div");
      box.className = "r9-success";
      const accuracy = summary.evidence_accuracy == null
        ? "—"
        : `${Math.round(Number(summary.evidence_accuracy) * 100)}%`;
      box.textContent = `Đúng ${summary.correct_count ?? 0}/${summary.answered_count ?? 0} câu đã trả lời · Độ chính xác evidence: ${accuracy}`;
      body.appendChild(box);
      nav.replaceChildren();
      actions.hidden = true;
      status.textContent = "Kết quả đã được ghi vào learner model.";
    } catch (error) {
      status.textContent = error.message;
    }
  }

  async function load() {
    if (root.dataset.state === "completed") {
      await showResult();
      return;
    }
    if (root.dataset.state === "abandoned") {
      title.textContent = "Bài đánh giá đã kết thúc";
      status.textContent = "Phiên này đã được bỏ dở.";
      return;
    }

    try {
      assessment = await requestJSON(root.dataset.detailUrl);
      if (!assessment.questions?.length) {
        title.textContent = "Không có câu hỏi khả dụng";
        return;
      }
      actions.hidden = false;
      renderQuestion();
    } catch (error) {
      status.textContent = error.message;
    }
  }

  prev.addEventListener("click", () => {
    if (currentIndex > 0) {
      currentIndex -= 1;
      renderQuestion();
    }
  });

  next.addEventListener("click", () => {
    if (assessment && currentIndex < assessment.questions.length - 1) {
      currentIndex += 1;
      renderQuestion();
    }
  });

  complete.addEventListener("click", async () => {
    if (!window.confirm("Nộp bài đánh giá này?")) return;
    complete.disabled = true;
    status.textContent = "Đang nộp bài…";
    try {
      await postJSON(root.dataset.completeUrl, { request_id: crypto.randomUUID() });
      root.dataset.state = "completed";
      await showResult();
    } catch (error) {
      status.textContent = error.message;
      complete.disabled = false;
    }
  });

  abandon.addEventListener("click", async () => {
    if (!window.confirm("Bỏ dở bài đánh giá hiện tại?")) return;
    abandon.disabled = true;
    try {
      await postJSON(root.dataset.abandonUrl, { request_id: crypto.randomUUID() });
      window.location.assign(root.dataset.dashboardUrl);
    } catch (error) {
      status.textContent = error.message;
      abandon.disabled = false;
    }
  });

  load();
})();

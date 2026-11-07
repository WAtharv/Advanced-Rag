/* K8s SRE Copilot web UI.
 *
 * Talks to the FastAPI service over HTTP rather than holding any pipeline state
 * of its own - the approval gate depends on both sides sharing one
 * checkpointer, so the only thing this file remembers about a parked run is its
 * thread_id.
 *
 * Served from the API's own origin by default, so the base URL is empty. Set
 * window.RAG_API_BASE before this script to point it elsewhere (the API needs
 * CORS enabled for that).
 */
(function () {
  "use strict";

  var API_BASE = window.RAG_API_BASE || "";
  var TIMEOUT_MS = 300000;

  var EXAMPLES = [
    "Why is my pod stuck in CrashLoopBackOff with exit code 137?",
    "Our ingress started returning 502s right after a deploy. Where do I look?",
    "How many sev1 incidents did production clusters have since June 2026?",
    "Which services had the most failed deployments this quarter?",
    "What does the change control policy require before draining nodes?",
  ];

  // USWDS has no emoji vocabulary; status reads as a tag with a real word, so
  // it survives greyscale printing and a screen reader.
  var STATUS = {
    allow: { label: "Pass", tag: "usa-tag--success" },
    redact: { label: "Redacted", tag: "usa-tag--info" },
    block: { label: "Blocked", tag: "usa-tag--error" },
    skip: { label: "Skipped", tag: "usa-tag--warning" },
  };

  var state = {
    history: [], // [{question, payload}]
    pending: null, // {question, note} while a request is in flight
    awaiting: null, // {thread_id, question, sql}
    busy: false,
  };

  // ------------------------------------------------------------- templating

  /** Marks a string as already-escaped HTML so `html` will not re-escape it. */
  function Safe(value) {
    this.value = value;
  }
  Safe.prototype.toString = function () {
    return this.value;
  };
  function safe(value) {
    return new Safe(value);
  }

  function esc(value) {
    if (value instanceof Safe) return value.value;
    return md.escape(value);
  }

  /** Tagged template that escapes every interpolation unless it is Safe(). */
  function html(strings) {
    var out = strings[0];
    for (var i = 1; i < arguments.length; i += 1) {
      var value = arguments[i];
      if (Array.isArray(value)) value = value.map(esc).join("");
      else value = esc(value);
      out += value + strings[i];
    }
    return safe(out);
  }

  function $(id) {
    return document.getElementById(id);
  }

  function setHtml(node, content) {
    node.innerHTML = String(content);
  }

  function show(node, visible) {
    node.classList.toggle("hidden", !visible);
  }

  // -------------------------------------------------------------- transport

  function request(path, options) {
    var controller = new AbortController();
    var timer = setTimeout(function () {
      controller.abort();
    }, TIMEOUT_MS);

    return fetch(API_BASE + path, Object.assign({ signal: controller.signal }, options))
      .then(function (response) {
        return response.text().then(function (text) {
          var body = null;
          try {
            body = text ? JSON.parse(text) : null;
          } catch (err) {
            body = null;
          }
          if (!response.ok) {
            // FastAPI puts the real reason in `detail`; a bare status code sends
            // the reader to the wrong place.
            var detail = body && body.detail ? body.detail : text || response.statusText;
            if (typeof detail !== "string") detail = JSON.stringify(detail);
            throw new Error(response.status + " " + detail);
          }
          return body;
        });
      })
      .finally(function () {
        clearTimeout(timer);
      });
  }

  function apiGet(path) {
    return request(path, { method: "GET" });
  }

  function apiPost(path, payload) {
    return request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
  }

  // ----------------------------------------------------------------- pieces

  function expander(label, body, options) {
    var opts = options || {};
    var classes = opts.className ? "usa-accordion " + opts.className : "usa-accordion";
    return html`<details class="${classes}"${safe(opts.open ? " open" : "")}>
      <summary class="usa-accordion__button">${label}</summary>
      <div class="usa-accordion__content">${body}</div>
    </details>`;
  }

  function citations(list) {
    if (!list || !list.length) return safe("");
    var rows = list.map(function (citation, index) {
      var where = citation.title || citation.source;
      var section = citation.section ? html` <span class="v">› ${citation.section}</span>` : "";
      return html`<div class="row-line">
        <span class="k">[${index + 1}] ${where}</span>${section}<br>
        <span class="v"><code>${citation.source}</code> · score
        ${citation.score.toFixed(3)}</span>
      </div>`;
    });
    return expander(html`Sources (${list.length})`, html`<div class="rows">${rows}</div>`);
  }

  function guardrails(outcomes) {
    if (!outcomes || !outcomes.length) return safe("");

    var blocked = outcomes.filter(function (o) {
      return o.action === "block";
    });
    var skipped = outcomes.filter(function (o) {
      return o.action === "skip";
    });

    var label;
    if (blocked.length) {
      label = html`Guardrails — BLOCKED`;
    } else {
      var ran = outcomes.length - skipped.length;
      label = html`Guardrails — ${ran} of ${outcomes.length} layers ran`;
      if (skipped.length) {
        // Never summarise a failed-open layer as a pass: the request got less
        // scrutiny than the layer count suggests, and the reader must know.
        label = html`${label}, ${skipped.length} SKIPPED`;
      }
    }

    var warning = "";
    if (skipped.length) {
      var names = skipped
        .map(function (o) {
          return o.layer;
        })
        .join(", ");
      warning = html`<div class="usa-alert usa-alert--warning">
        <p class="usa-alert__heading">Reduced checking</p>
        <p>${skipped.length} layer(s) could not run and failed open: ${names}. This answer
        received less checking than a full pass.</p>
      </div>`;
    }

    var rows = outcomes.map(function (outcome) {
      var status = STATUS[outcome.action] || { label: outcome.action, tag: "" };
      var detail = outcome.detail ? html` <span class="v">— ${outcome.detail}</span>` : "";
      return html`<div class="row-line">
        <span class="usa-tag ${status.tag}">${status.label}</span>
        <span class="k">${outcome.layer}</span>${detail}
      </div>`;
    });

    return expander(label, html`${warning}<div class="rows">${rows}</div>`, {
      open: Boolean(blocked.length || skipped.length),
      className: blocked.length
        ? "usa-accordion--error"
        : skipped.length
          ? "usa-accordion--warning"
          : "",
    });
  }

  function trace(steps) {
    if (!steps || !steps.length) return safe("");
    var total = steps.reduce(function (sum, step) {
      return sum + step.elapsed_ms;
    }, 0);
    var rows = steps.map(function (step) {
      return html`<div class="row-line">
        <span class="k">${step.node}</span> <code>${step.elapsed_ms} ms</code>
        <span class="v">— ${step.detail}</span>
      </div>`;
    });
    return expander(
      html`Pipeline trace — ${steps.length} steps, ${total} ms`,
      html`<div class="rows">${rows}</div>`
    );
  }

  function sqlResult(sql) {
    if (!sql || !sql.rows || !sql.rows.length) return safe("");
    var head = sql.columns.map(function (column) {
      return html`<th>${column}</th>`;
    });
    var body = sql.rows.map(function (row) {
      var cells = sql.columns.map(function (_, index) {
        var cell = row[index];
        return html`<td>${cell === null || cell === undefined ? "—" : String(cell)}</td>`;
      });
      return html`<tr>${cells}</tr>`;
    });
    return expander(
      html`Query result (${sql.row_count} rows)`,
      html`<pre><code class="language-sql">${sql.sql}</code></pre>
        <div class="table-wrap">
          <table class="usa-table">
            <caption>Rows returned by the approved query</caption>
            <thead><tr>${head}</tr></thead>
            <tbody>${body}</tbody>
          </table>
        </div>`,
      { open: true }
    );
  }

  function answerCard(payload) {
    var bodyHtml = payload.blocked
      ? html`<div class="usa-alert usa-alert--error">
          <p class="usa-alert__heading">Request blocked</p>
          <p>${payload.answer}</p>
        </div>`
      : safe(md.render(payload.answer));

    var pills = [
      html`<span class="usa-tag">route <code>${payload.route}</code></span>`,
      html`<span class="usa-tag">${payload.latency_ms} ms</span>`,
    ];
    if (payload.extractive) {
      // An extractive answer spent no tokens, so a 0→0 tag would just be noise;
      // what the reader needs to know is that nothing was generated.
      pills.push(html`<span class="usa-tag usa-tag--warning">extractive · no LLM</span>`);
    } else {
      pills.push(
        html`<span class="usa-tag">${payload.input_tokens}→${payload.output_tokens} tokens</span>`
      );
    }
    if (payload.cached) {
      pills.push(
        html`<span class="usa-tag usa-tag--success">cache hit (${payload.cache_kind})</span>`
      );
    }

    return html`<div class="answer${safe(payload.blocked ? " is-blocked" : "")}">
      <div class="prose">${bodyHtml}</div>
      <div class="meta">${pills}</div>
    </div>
    ${citations(payload.citations)}
    ${guardrails(payload.guardrails)}
    ${trace(payload.trace)}
    ${sqlResult(payload.sql)}`;
  }

  /** A user message as a right-aligned chat bubble. */
  function askBubble(question) {
    return html`<div class="chat-row chat-row--user">
      <div class="bubble bubble--user">${question}</div>
    </div>`;
  }

  /** A bot message: the helm avatar plus a body column (answer card, or the
   *  spinner while a reply is in flight). */
  function botRow(body) {
    return html`<div class="chat-row chat-row--bot">
      <div class="avatar" aria-hidden="true">⎈</div>
      <div class="bot-content">${body}</div>
    </div>`;
  }

  function turn(entry) {
    return html`<div class="turn">
      ${askBubble(entry.question)}
      ${botRow(answerCard(entry.payload))}
    </div>`;
  }

  /** The in-flight question: echoed immediately, with the spinner as the bot's
   *  reply so processing reads as part of this turn rather than a stray bar
   *  below the previous answer. */
  function pendingTurn(pending) {
    var busy = html`<div class="busy" role="status">
      <span class="spinner" aria-hidden="true"></span>
      <span>${pending.note}</span>
    </div>`;
    return html`<div class="turn is-pending">
      ${askBubble(pending.question)}
      ${botRow(busy)}
    </div>`;
  }

  // ------------------------------------------------------------- ask panel

  function renderHistory() {
    var parts = state.history.map(turn);
    if (state.pending) parts.push(pendingTurn(state.pending));
    setHtml($("history"), parts.join(""));
    show(
      $("empty-state"),
      state.history.length === 0 && !state.pending && !state.awaiting
    );
  }

  function renderApproval() {
    var node = $("approval");
    var awaiting = state.awaiting;
    // Hide the gate while its own query is running - the pending turn carries
    // the spinner, and a live approval box beside it invites a double submit.
    if (!awaiting || state.busy) {
      setHtml(node, "");
      return;
    }

    var rationale = awaiting.sql.rationale
      ? html`<p class="rationale muted">${awaiting.sql.rationale}</p>`
      : "";

    setHtml(
      node,
      html`<div class="approval">
        <div class="usa-alert usa-alert--warning">
          <p class="usa-alert__heading">Approval required</p>
          <p>This question needs a database query. It will not run until you approve it.</p>
          <pre><code class="language-sql">${awaiting.sql.sql || "(no query)"}</code></pre>
          ${rationale}
          <div class="actions">
            <button class="usa-button" type="button" data-approve="yes">
              Approve and run query
            </button>
            <button class="usa-button usa-button--secondary" type="button" data-approve="no">
              Reject
            </button>
          </div>
        </div>
      </div>`
    );

    node.querySelectorAll("[data-approve]").forEach(function (button) {
      button.addEventListener("click", function () {
        decide(button.getAttribute("data-approve") === "yes");
      });
    });
  }

  function setBusy(busy) {
    state.busy = busy;
    $("send").disabled = busy;
    $("question").disabled = busy;
    // Clearing mid-flight would race the landing answer; grey it out to match.
    $("clear-chat").disabled = busy;
  }

  function askError(message) {
    var node = $("ask-error");
    node.textContent = message || "";
    show(node, Boolean(message));
  }

  function scrollDown() {
    var scroller = $("scroller");
    scroller.scrollTop = scroller.scrollHeight;
  }

  /** True when the user has the "With LLM" mode selected. Defaults to true if
   *  the control is missing so behaviour is unchanged from before the toggle. */
  function useLlm() {
    var extractive = $("mode-extractive");
    return !(extractive && extractive.checked);
  }

  /** Refresh the answer-mode note under the toggle to describe what the current
   *  selection does. Left alone when the LLM is offline, since that case owns
   *  the note (see configureModeControls). */
  function updateModeNote() {
    var llmRadio = $("mode-llm");
    if (!llmRadio || llmRadio.disabled) return;
    $("mode-note").textContent = useLlm()
      ? "Answers are generated from the retrieved sources."
      : "No model is called — answers quote the matching runbook passages.";
  }

  /** Point the toggle at what the service can actually do. With no model
   *  configured, generation is impossible, so lock the choice to "Without LLM"
   *  and say why rather than letting a request fall back silently. */
  function configureModeControls(liveLlm) {
    var llmRadio = $("mode-llm");
    var extractiveRadio = $("mode-extractive");
    if (!llmRadio || !extractiveRadio) return;

    if (liveLlm) {
      llmRadio.disabled = false;
      updateModeNote();
      return;
    }
    llmRadio.disabled = true;
    llmRadio.checked = false;
    extractiveRadio.checked = true;
    $("mode-note").textContent =
      "No language model is configured, so only the without-LLM mode is available.";
  }

  function ask(question) {
    if (!question || state.busy || state.awaiting) return;
    askError("");
    // Echo the question and show the spinner under it before the round trip, so
    // this turn is on screen immediately rather than after the answer lands.
    state.pending = { question: question, note: "Running the pipeline…" };
    setBusy(true);
    renderHistory();
    scrollDown();

    apiPost("/ask", { question: question, use_llm: useLlm() })
      .then(function (payload) {
        if (payload.awaiting_approval) {
          state.awaiting = {
            thread_id: payload.thread_id,
            question: question,
            sql: payload.sql || { sql: "", rationale: "" },
          };
        } else {
          state.history.push({ question: question, payload: payload });
        }
      })
      .catch(function (error) {
        askError("Request failed: " + error.message);
      })
      .finally(function () {
        state.pending = null;
        setBusy(false);
        renderHistory();
        renderApproval();
        scrollDown();
      });
  }

  /** Drop everything on screen: the Ask conversation and the Retrieval lab's
   *  results, so the button clears whichever tab the user is looking at.
   *  Client-side only - the server keeps no session for us to reset, and any
   *  parked approval thread is simply abandoned. Refuses while an ask is in
   *  flight so a clear cannot race the answer that is about to land. */
  function clearChat() {
    if (state.busy) return;
    // Ask panel.
    state.history = [];
    state.awaiting = null;
    state.pending = null;
    askError("");
    renderHistory();
    renderApproval();
    // Retrieval lab panel.
    setHtml($("lab-results"), "");
    show($("lab-error"), false);
    show($("lab-busy"), false);
    $("question").focus();
  }

  function decide(approved) {
    var awaiting = state.awaiting;
    if (!awaiting || state.busy) return;
    askError("");
    state.pending = {
      question: awaiting.question,
      note: approved ? "Running the approved query…" : "Recording the rejection…",
    };
    setBusy(true);
    renderApproval(); // hide the gate now that it is running
    renderHistory();
    scrollDown();

    apiPost("/approve", { thread_id: awaiting.thread_id, approved: approved })
      .then(function (payload) {
        state.history.push({ question: awaiting.question, payload: payload });
        state.awaiting = null;
      })
      .catch(function (error) {
        askError("Approval failed: " + error.message);
      })
      .finally(function () {
        state.pending = null;
        setBusy(false);
        renderHistory();
        renderApproval();
        scrollDown();
      });
  }

  // ------------------------------------------------------------- retrieval

  function labHit(hit, index, authoritative) {
    var retrieval = html`retrieval <b>${hit.retrieval_score.toFixed(3)}</b>`;
    var score;
    if (hit.rerank_score === null || hit.rerank_score === undefined) {
      score = retrieval;
    } else if (authoritative) {
      score = html`rerank <b>${hit.rerank_score.toFixed(3)}</b> · retrieval
        ${hit.retrieval_score.toFixed(3)}`;
    } else {
      score = html`${retrieval} · rerank ${hit.rerank_score.toFixed(3)}
        (not used for ordering)`;
    }

    var title = html`<span class="hit-title">
      <span class="src">${index + 1}. ${hit.source}</span>
      <span class="sec">› ${hit.section}</span>
      <span class="score">— ${score}</span>
    </span>`;
    return expander(title, html`<pre class="chunk">${hit.text}</pre>`);
  }

  function renderLab(result) {
    // Which score actually determined this order? Showing the rerank score
    // first when it did not drive the sort makes a correct list look broken.
    var authoritative = Boolean(result.rerank_is_authoritative);
    var parts = [];

    if (result.hyde_document) {
      parts.push(
        expander(
          "Generated HyDE passage (embedded, never shown to users)",
          html`<p class="muted">${result.hyde_document}</p>`
        )
      );
    }

    parts.push(
      html`<p class="lab-summary"><b>${result.results.length} results</b> — ordered by
      <code>${authoritative ? "rerank" : "retrieval"}</code> score</p>`
    );

    if (result.reranked && !authoritative) {
      parts.push(
        html`<div class="usa-alert usa-alert--info">
          <p class="usa-alert__heading">Reranker is score-only</p>
          <p>No cross-encoder is available, so the <code>rerank</code> figure below is a
          lexical-overlap signal shown for comparison, and deliberately does <em>not</em>
          reorder the list. Measured, letting it reorder dropped precision@5 from 0.76 to
          0.64.</p>
        </div>`
      );
    }

    var hits = result.results.map(function (hit, index) {
      return labHit(hit, index, authoritative);
    });
    parts.push(html`<div class="lab-hits">${hits}</div>`);

    setHtml($("lab-results"), parts.join(""));
  }

  function runLab(event) {
    event.preventDefault();
    var mode = $("lab-mode").value;
    var payload = {
      query: $("lab-query").value,
      mode: mode,
      fusion: $("lab-fusion").value,
      use_hyde: $("lab-hyde").checked,
      rerank: $("lab-rerank").checked,
      top_k: Number($("lab-topk").value),
    };
    if (!payload.query.trim()) return;

    show($("lab-error"), false);
    setHtml($("lab-results"), "");
    show($("lab-busy"), true);
    $("lab-run").disabled = true;

    apiPost("/retrieve", payload)
      .then(renderLab)
      .catch(function (error) {
        var node = $("lab-error");
        node.textContent = "Retrieval failed: " + error.message;
        show(node, true);
      })
      .finally(function () {
        show($("lab-busy"), false);
        $("lab-run").disabled = false;
      });
  }

  // ---------------------------------------------------------------- health

  function renderHealth() {
    apiGet("/health")
      .then(function (health) {
        configureModeControls(health.llm_mode === "live");
        var tag = health.status === "ok" ? "usa-tag--success" : "usa-tag--warning";
        // Retrieval can be perfectly healthy with no model configured. Saying so
        // once, up front, is the difference between a working offline mode and a
        // UI that looks broken.
        var offline =
          health.llm_mode === "offline"
            ? html`<div class="usa-alert usa-alert--warning">
                <p class="usa-alert__heading">No language model configured</p>
                <p>Answers are runbook passages quoted verbatim, not generated prose,
                and database questions are unavailable. Set an API key and restart to
                enable generation.</p>
              </div>`
            : "";
        var features = Object.keys(health.features).map(function (name) {
          var on = health.features[name];
          return html`<li class="${on ? "on" : "off"}">
            <span class="mark" aria-hidden="true">${on ? "✓" : "—"}</span>${name}
            <span class="usa-sr-only">${on ? "enabled" : "disabled"}</span>
          </li>`;
        });
        setHtml(
          $("health"),
          html`<h2>Service status</h2>
          <div class="health-row">
            <span class="label">API</span>
            <span class="value"><span class="usa-tag ${tag}">${health.status}</span></span>
          </div>
          <div class="health-row">
            <span class="label">Indexed chunks</span>
            <span class="value">${health.indexed_chunks}</span>
          </div>
          <div class="health-row">
            <span class="label">Model</span><span class="value">${health.model}</span>
          </div>
          <div class="health-row">
            <span class="label">Cache</span><span class="value">${health.cache}</span>
          </div>
          <div class="health-row">
            <span class="label">SQL dialect</span><span class="value">${health.sql_dialect}</span>
          </div>
          ${offline}
          ${expander("Pipeline layers", html`<ul class="features">${features}</ul>`)}`
        );
      })
      .catch(function (error) {
        setHtml(
          $("health"),
          html`<h2>Service status</h2>
          <div class="health-row">
            <span class="label">API</span>
            <span class="value"><span class="usa-tag usa-tag--error">unreachable</span></span>
          </div>
          <div class="usa-alert usa-alert--error">
            <p>${error.message}</p>
            <p>Start it with <code>rag-api</code> (or
            <code>uvicorn advanced_rag.api.main:app</code>).</p>
          </div>`
        );
      });
  }

  // ------------------------------------------------------------------ wire

  function autoGrow(textarea) {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 190) + "px";
  }

  function init() {
    setHtml(
      $("examples"),
      EXAMPLES.map(function (example) {
        return html`<li class="usa-sidenav__item">
          <button class="example" type="button">${example}</button>
        </li>`;
      }).join("")
    );
    $("examples")
      .querySelectorAll(".example")
      .forEach(function (button, index) {
        button.addEventListener("click", function () {
          selectTab("ask");
          ask(EXAMPLES[index]);
        });
      });

    $("clear-chat").addEventListener("click", clearChat);

    $("clear-cache").addEventListener("click", function () {
      var button = $("clear-cache");
      button.disabled = true;
      apiPost("/cache/clear")
        .then(function () {
          $("cache-status").textContent = "Cache cleared.";
        })
        .catch(function (error) {
          $("cache-status").textContent = "Failed: " + error.message;
        })
        .finally(function () {
          button.disabled = false;
        });
    });

    document.querySelectorAll(".tab").forEach(function (tab) {
      tab.addEventListener("click", function () {
        selectTab(tab.getAttribute("data-tab"));
      });
    });

    document.querySelectorAll('input[name="answer-mode"]').forEach(function (radio) {
      radio.addEventListener("change", updateModeNote);
    });
    updateModeNote();

    var question = $("question");
    question.addEventListener("input", function () {
      autoGrow(question);
    });
    question.addEventListener("keydown", function (event) {
      // Enter sends, Shift+Enter is a newline - the shape everyone expects from
      // a chat box.
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        $("ask-form").requestSubmit();
      }
    });

    $("ask-form").addEventListener("submit", function (event) {
      event.preventDefault();
      var text = question.value.trim();
      if (!text) return;
      question.value = "";
      autoGrow(question);
      ask(text);
    });

    var mode = $("lab-mode");
    mode.addEventListener("change", function () {
      $("lab-fusion").disabled = mode.value !== "hybrid";
    });
    var topk = $("lab-topk");
    topk.addEventListener("input", function () {
      $("lab-topk-value").textContent = topk.value;
    });
    $("lab-form").addEventListener("submit", runLab);

    renderHealth();
    renderHistory();
  }

  function selectTab(name) {
    document.querySelectorAll(".tab").forEach(function (tab) {
      var active = tab.getAttribute("data-tab") === name;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    show($("panel-ask"), name === "ask");
    show($("panel-lab"), name === "lab");
  }

  document.addEventListener("DOMContentLoaded", init);
})();

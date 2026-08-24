const app = document.querySelector("#app");
const snapshotLabel = document.querySelector("#snapshot-label");
let metadata = null;
let selectedTier = "";
let selectedManufacturer = "";
let searchTimer = null;

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const formatNumber = (value) => new Intl.NumberFormat("en-US").format(value ?? 0);
const humanCause = (value) => String(value ?? "unknown").replaceAll("_", " ");
const causeLabel = (value, unknownReason = null) => {
  if (value === "unknown" && unknownReason === "reserved_for_evaluation") {
    return "unknown (reserved for evaluation)";
  }
  if (value === "unknown" && unknownReason === "fda_reason_not_provided") {
    return "unknown (no reason provided by FDA)";
  }
  if (value === "unknown" && unknownReason === "needs_teacher_labeling") {
    return "unknown (needs teacher labeling)";
  }
  return humanCause(value);
};
const causePill = (value, unknownReason = null) => {
  let kind = "classified";
  let title = "Classified root cause from the available structured cause label.";
  if (value === "unknown" && unknownReason === "reserved_for_evaluation") {
    kind = "evaluation";
    title = "Held out from teacher labeling for human evaluation; the human label is not used in scoring.";
  } else if (value === "unknown" && unknownReason === "fda_reason_not_provided") {
    kind = "fda-gap";
    title = "FDA shortage_reason was missing or 'Other'; Phase 9 policy does not infer a cause.";
  } else if (value === "unknown" && unknownReason === "needs_teacher_labeling") {
    kind = "needs-labeling";
    title = "FDA supplied cause text, but this new record has not been reviewed by the optional teacher-labeling phase.";
  } else if (value === "unknown") {
    kind = "unclassified";
    title = "No classified cause is available in the current inputs.";
  }
  return `<span class="cause-pill cause-${kind}" title="${escapeHtml(title)}">${escapeHtml(causeLabel(value, unknownReason))}</span>`;
};
const tierBadge = (tier) => `<span class="tier tier-${escapeHtml(tier)}">${escapeHtml(tier)}</span>`;
const confidenceBadge = (level) => `<span class="confidence confidence-${escapeHtml(level)}">${escapeHtml(level)} confidence</span>`;
const evaluationBadge = (count) => {
  if (!count) return "";
  const noun = count === 1 ? "record" : "records";
  return `<span class="evaluation-badge" title="Held out from teacher labeling for human evaluation; the human label is not used in scoring.">${formatNumber(count)} evaluation-reserved ${noun}</span>`;
};
const fdaGapBadge = (count) => {
  if (!count) return "";
  const noun = count === 1 ? "record" : "records";
  return `<span class="fda-gap-badge" title="FDA shortage_reason was missing or 'Other'; Phase 9 policy does not infer a cause.">${formatNumber(count)} FDA-no-reason ${noun}</span>`;
};

async function api(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json();
}

function renderError(error) {
  app.innerHTML = `<section class="error" role="alert"><strong>Unable to load the dashboard.</strong><br>${escapeHtml(error.message)}</section>`;
}

function listShell() {
  const tiers = ["", "high", "elevated", "moderate", "low"];
  const buttons = tiers.map((tier) => {
    const label = tier || "all";
    return `<button class="filter-button" data-tier="${tier}" aria-pressed="${tier === selectedTier}">${label}</button>`;
  }).join("");
  const manufacturerOptions = metadata.manufacturers.map((manufacturer) =>
    `<option value="${escapeHtml(manufacturer)}">${escapeHtml(manufacturer)}</option>`
  ).join("");
  return `
    <section class="page-heading">
      <div>
        <p class="eyebrow">FDA supply-chain intelligence</p>
        <h1>Current shortage signals</h1>
        <p class="lede">Ranked, explainable fragility signals grouped by drug, manufacturer, and initial posting date—not raw package rows.</p>
      </div>
    </section>
    <section class="summary-grid" aria-label="Snapshot summary">
      <article class="summary-card"><strong>${formatNumber(metadata.group_count)}</strong><span>grouped shortage signals</span></article>
      <article class="summary-card"><strong>${formatNumber(metadata.tier_counts.high || 0)}</strong><span>high-risk signals</span></article>
      <article class="summary-card"><strong>${metadata.coverage.current_shortage_score_coverage_pct.toFixed(1)}%</strong><span>current-shortage score coverage</span></article>
      <article class="summary-card"><strong>${metadata.recall_linkage.overall_recall_linkage_pct.toFixed(1)}%</strong><span>overall recall linkage</span></article>
    </section>
    <section aria-label="Shortage filters">
      <div class="controls">
        <div class="filter-fields">
          <label class="search-wrap">
            <span aria-hidden="true">⌕</span>
            <input id="search" type="search" placeholder="Search drug or manufacturer" autocomplete="off" aria-label="Search drug name or manufacturer" />
          </label>
          <label class="manufacturer-filter">
            <select id="manufacturer" aria-label="Filter by manufacturer">
              <option value="">All manufacturers</option>
              ${manufacturerOptions}
            </select>
          </label>
        </div>
        <div class="tier-filter-row">
          <span class="filter-label">Risk tier</span>
          <div class="tier-filters" aria-label="Filter by risk tier">${buttons}</div>
        </div>
      </div>
      <div id="results" aria-live="polite"></div>
    </section>`;
}

function renderRows(payload) {
  const target = document.querySelector("#results");
  if (!payload.records.length) {
    target.innerHTML = `<div class="empty">No grouped shortages match this search and risk filter.</div>`;
    return;
  }
  const drugGroups = new Map();
  payload.records.forEach((record) => {
    const key = record.generic_name || "Unnamed drug";
    if (!drugGroups.has(key)) drugGroups.set(key, []);
    drugGroups.get(key).push(record);
  });
  const rows = Array.from(drugGroups.entries()).map(([drugName, records]) => {
    const shownManufacturers = new Set(records.map((record) => record.manufacturer)).size;
    const totalManufacturers = records[0].related_manufacturer_count;
    const packagesShown = records.reduce((total, record) => total + record.package_count, 0);
    const relationship = totalManufacturers > shownManufacturers
      ? `${shownManufacturers} shown of ${totalManufacturers} manufacturers`
      : `${shownManufacturers} manufacturer${shownManufacturers === 1 ? "" : "s"}`;
    const heading = `
      <tr class="drug-group-row">
        <td colspan="6">
          <div class="drug-group-heading">
            <strong>${escapeHtml(drugName)}</strong>
            <span class="relationship-badge">${escapeHtml(relationship)}</span>
            <span class="package-badge">${formatNumber(packagesShown)} package record${packagesShown === 1 ? "" : "s"} shown</span>
          </div>
        </td>
      </tr>`;
    const manufacturerRows = records.map((record) => `
      <tr class="signal-row" data-href="${escapeHtml(record.detail_url)}" tabindex="0" role="link" aria-label="View ${escapeHtml(drugName)} from ${escapeHtml(record.manufacturer)}">
        <td class="score-cell"><div class="signal-score score-${escapeHtml(record.risk_tier)}"><strong>${record.score}</strong><span>/100</span></div></td>
        <td>
          <a class="drug-link" href="${escapeHtml(record.detail_url)}">${escapeHtml(record.manufacturer || "Unknown manufacturer")}</a>
          <span class="subtext">View full breakdown →</span>
        </td>
        <td>${record.package_count}<span class="subtext">package record${record.package_count === 1 ? "" : "s"}</span></td>
        <td>${tierBadge(record.risk_tier)}</td>
        <td>
          ${confidenceBadge(record.recall_linkage_confidence)}
          <span class="review-flag">requires review</span>
        </td>
        <td>
          ${causePill(record.primary_cause, record.unknown_reason)}
          ${evaluationBadge(record.evaluation_reserved_count && !record.reserved_for_evaluation ? record.evaluation_reserved_count : 0)}
          ${fdaGapBadge(record.fda_no_reason_count && record.unknown_reason !== "fda_reason_not_provided" ? record.fda_no_reason_count : 0)}
        </td>
      </tr>`).join("");
    return heading + manufacturerRows;
  }).join("");
  target.innerHTML = `
    <div class="result-count">${formatNumber(payload.count)} grouped shortage signal${payload.count === 1 ? "" : "s"}</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Score</th><th>Manufacturer</th><th>Packages</th><th>Risk tier</th><th>Recall data</th><th>Cause</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  bindClickableRows();
}

async function loadList() {
  const input = document.querySelector("#search");
  const params = new URLSearchParams();
  if (input?.value.trim()) params.set("q", input.value.trim());
  if (selectedTier) params.set("tier", selectedTier);
  if (selectedManufacturer) params.set("manufacturer", selectedManufacturer);
  const payload = await api(`/api/shortages?${params}`);
  renderRows(payload);
}

function bindList() {
  const input = document.querySelector("#search");
  const manufacturer = document.querySelector("#manufacturer");
  input.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => loadList().catch(renderError), 180);
  });
  manufacturer.addEventListener("change", () => {
    selectedManufacturer = manufacturer.value;
    loadList().catch(renderError);
  });
  document.querySelectorAll(".filter-button").forEach((button) => {
    button.addEventListener("click", () => {
      selectedTier = button.dataset.tier;
      document.querySelectorAll(".filter-button").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
      loadList().catch(renderError);
    });
  });
}

function bindClickableRows() {
  document.querySelectorAll(".signal-row").forEach((row) => {
    row.addEventListener("click", (event) => {
      if (event.target.closest("a")) return;
      window.location.assign(row.dataset.href);
    });
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        window.location.assign(row.dataset.href);
      }
    });
  });
}

function limitedList(items, render, limit = 10) {
  if (!items?.length) return '<p class="explanation">None listed in the current graph snapshot.</p>';
  const visible = items.slice(0, limit).map(render).join("");
  const note = items.length > limit ? `<p class="list-limit-note">Showing ${limit} of ${items.length}.</p>` : "";
  return `<ul class="name-list">${visible}</ul>${note}`;
}

function componentCard(title, points, maxPoints, metric, explanation, extra = "", wide = false) {
  return `<article class="component-card ${wide ? "wide" : ""}">
    <div class="component-title"><div><h2>${escapeHtml(title)}</h2></div><span class="points">${points}/${maxPoints} pts</span></div>
    <div class="metric">${metric}</div>
    <p class="explanation">${escapeHtml(explanation)}</p>
    ${extra}
  </article>`;
}

function renderDetail(payload) {
  const { group, score, warnings } = payload;
  const c = score.components;
  const overlap = score.recall_overlap;
  const alternatives = score.alternative_availability;
  const concentration = score.manufacturer_concentration;
  const recalledProducts = overlap.overlapping_products || [];

  const manufacturerList = limitedList(concentration.available_manufacturers, (name) => `<li>${escapeHtml(name)}</li>`);
  const alternativeList = limitedList(alternatives.available_alternatives, (item) => `<li><strong>${escapeHtml(item.generic_name || item.brand_name)}</strong><br><span class="subtext">${escapeHtml(item.manufacturer)} · ${escapeHtml(item.product_ndc)}</span></li>`);
  const recallList = limitedList(recalledProducts, (item) => `<li><strong>${escapeHtml(item.manufacturer)}</strong> · ${escapeHtml(item.product_ndc)}<br><span class="subtext">${escapeHtml(item.classification || "Unclassified")} · ${escapeHtml(item.recall_number || item.recall_id)}${item.also_in_shortage ? " · also in shortage" : ""}</span></li>`);
  const warningCards = warnings.map((warning) => `<div class="warning-card"><strong>Requires human review:</strong> ${escapeHtml(warning)}</div>`).join("");

  app.innerHTML = `
    <a class="back-link" href="/">← All shortage signals</a>
    <section class="detail-hero">
      <div>
        <p class="eyebrow">Supply-chain research signal</p>
        <h1>${escapeHtml(group.generic_name || "Unnamed drug")}</h1>
        <div class="hero-meta">
          <span><strong>Manufacturer:</strong> ${escapeHtml(group.manufacturer)}</span>
          <span><strong>Representative NDC:</strong> ${escapeHtml(group.package_ndc)}</span>
          <span><strong>Grouped packages:</strong> ${formatNumber(group.package_count)}</span>
          <span><strong>Initial posting:</strong> ${escapeHtml(group.initial_posting_date)}</span>
        </div>
      </div>
      <div class="score-block">
        <div class="score-number">${score.score}<span>/100</span></div>
        <div class="score-gauge gauge-${escapeHtml(score.risk_tier)}" role="img" aria-label="Fragility score ${score.score} out of 100">
          <span style="width: ${Math.max(0, Math.min(100, score.score))}%"></span>
        </div>
        ${tierBadge(score.risk_tier)}
        <span class="review-flag">requires human review</span>
      </div>
    </section>
    <div class="warning-stack">${warningCards}</div>
    <section class="components-grid" aria-label="Fragility score components">
      ${componentCard(
        "Manufacturer concentration",
        c.manufacturer_concentration.points,
        c.manufacturer_concentration.max_points,
        `${formatNumber(c.manufacturer_concentration.observed)} available`,
        concentration.availability_definition,
        manufacturerList
      )}
      ${componentCard(
        "Shortage duration",
        c.shortage_duration.points,
        c.shortage_duration.max_points,
        `${formatNumber(c.shortage_duration.observed_days)} days`,
        `Ongoing from ${score.shortage.initial_posting_date}; measured at snapshot ${score.as_of}.`
      )}
      ${componentCard(
        "Recall overlap",
        c.recall_overlap.points,
        c.recall_overlap.max_points,
        `${c.recall_overlap.observed ? "Overlap found" : "No linked overlap"} ${confidenceBadge(c.recall_overlap.linkage_confidence.level)}`,
        c.recall_overlap.linkage_confidence.basis,
        recallList,
        true
      )}
      ${componentCard(
        "Available alternatives",
        c.alternative_availability.points,
        c.alternative_availability.max_points,
        `${formatNumber(c.alternative_availability.observed)} available`,
        `${formatNumber(alternatives.candidate_equivalent_count)} proxy-equivalent candidates. Not FDA Orange Book-rated equivalence.`,
        alternativeList
      )}
      ${componentCard(
        "Root cause",
        c.manufacturing_root_cause.points,
        c.manufacturing_root_cause.max_points,
        causePill(c.manufacturing_root_cause.observed, c.manufacturing_root_cause.unknown_reason),
        c.manufacturing_root_cause.rule
      )}
    </section>`;
}

async function start() {
  metadata = await api("/api/meta");
  snapshotLabel.innerHTML = `<span aria-hidden="true">◷</span> Data snapshot ${escapeHtml(metadata.snapshot)} · observed ${escapeHtml(metadata.as_of)}`;
  snapshotLabel.title = `Data freshness: snapshot ${metadata.snapshot}, observed ${metadata.as_of}`;
  snapshotLabel.setAttribute("aria-label", snapshotLabel.title);
  const detailMatch = window.location.pathname.match(/^\/drug\/(\d+)\/?$/);
  if (detailMatch) {
    const payload = await api(`/api/shortages/${detailMatch[1]}`);
    renderDetail(payload);
    document.title = `${payload.group.generic_name} · MediSupply`;
    return;
  }
  app.innerHTML = listShell();
  bindList();
  await loadList();
}

start().catch(renderError);

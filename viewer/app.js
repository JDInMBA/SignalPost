/* Signalpost explorer.

   Reads one run's envelopes and renders them. It adds nothing: every figure on
   screen is read off a claim, and every claim shows the page it came from, when
   it was read and the hash of what was read.

   One rule is load-bearing here. A domain the identity gate considered and
   refused is never named in this interface. The scored run lost 24 claims to
   exactly that: a refused candidate sat in the summary block, and a reader took
   it for a finding. Refusals are reported as counts and reasons. */
(function () {
  "use strict";

  var PAGE = 40;            // companies per page before "show more"
  var state = {view: "companies", org: null, q: "", filter: "", tab: "trail", shown: PAGE};
  var ROWS = [];

  // ------------------------------------------------------------- helpers --
  var AMP = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"};
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return AMP[c]; }); }
  function el(id) { return document.getElementById(id); }
  function claimOf(e, f) { return (e.claims || []).find(function (c) { return c.field === f; }); }
  function valueOf(e, f) {
    var c = claimOf(e, f);
    return c && c.availability === "available" ? c.value : null;
  }
  function stateOf(e, f) { var c = claimOf(e, f); return c ? c.availability : "not_checked"; }
  function evidenceFor(e, c) {
    return ((c && c.evidence_ids) || []).map(function (id) {
      return (e.evidence || []).find(function (x) { return x.id === id; });
    }).filter(Boolean);
  }
  function words(s) { return String(s || "").replace(/_/g, " "); }
  function nok(v) {
    if (typeof v !== "number") return null;
    var a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(2) + "bn";
    if (a >= 1e6) return (v / 1e6).toFixed(1) + "m";
    if (a >= 1e3) return Math.round(v / 1e3).toLocaleString("en") + "k";
    return v.toLocaleString("en");
  }
  function cell(v) {
    if (typeof v !== "number") return '<span class="nf">not reported</span>';
    return '<span class="' + (v < 0 ? "neg" : "") + '">' + esc(nok(v)) + "</span>";
  }
  function tag(cls, text) { return '<span class="tag ' + cls + '"><i></i>' + esc(text) + "</span>"; }
  function stateTag(s) { return tag("s-" + s, words(s)); }
  // The register returns addresses under its own Norwegian keys. Reading them
  // as `street` and `postcode` silently rendered nothing at all.
  function addressOf(a) {
    if (!a || typeof a !== "object") return "";
    var lines = [].concat(a.adresse || a.address_lines || a.street || []);
    var place = [a.postnummer || a.postcode, a.poststed || a.city].filter(Boolean).join(" ");
    return lines.concat(place ? [place] : []).filter(Boolean).join(", ");
  }

  function host(url) { try { return new URL(url).host; } catch (err) { return String(url || ""); } }
  function day(s) { return String(s || "").slice(0, 10); }

  var ICON = {
    hash: '<path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18"/>',
    doc: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    cal: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 11h18"/>',
    pin: '<path d="M12 21s7-5.6 7-11a7 7 0 1 0-14 0c0 5.4 7 11 7 11z"/><circle cx="12" cy="10" r="2.6"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 2.5 15.3 0 18M12 3c-2.5 2.7-2.5 15.3 0 18"/>',
    share: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.6 10.6 6.8-4.2M8.6 13.4l6.8 4.2"/>',
    chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    bank: '<path d="M3 21h18M5 21V9l7-5 7 5v12M9 21v-5h6v5"/>',
    briefcase: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'
  };
  function icon(name) {
    return '<svg viewBox="0 0 24 24">' + (ICON[name] || ICON.doc) + "</svg>";
  }

  // ------------------------------------------------------------ the data --
  function load(rows) {
    ROWS = rows;
    el("n-companies").textContent = rows.length;
    var ev = rows.reduce(function (n, e) { return n + (e.evidence || []).length; }, 0);
    var req = rows.reduce(function (n, e) { return n + ((e.operations || {}).requests || 0); }, 0);
    var run = (rows[0] || {}).run || {};
    el("runline").innerHTML = esc(ev.toLocaleString("en")) + " source records<br>" +
      esc(req.toLocaleString("en")) + " requests · run " + esc(day(run.started_at));
    route();
  }

  // --------------------------------------------------------------- views --
  function companies() {
    var q = state.q.trim().toLowerCase();
    var list = ROWS.filter(function (e) {
      var site = valueOf(e, "official_website") || {};
      var st = valueOf(e, "operating_status") || {};
      var conf = ((e.summary || {}).website_confidence || {}).level;
      var f = state.filter;
      if (f === "site" && !(e.summary || {}).verified_website) return false;
      if (f === "abstained" && stateOf(e, "official_website") !== "ambiguous") return false;
      if (f === "jobs" && !((valueOf(e, "job_postings") || []).length)) return false;
      if (f === "adverse" && !(st.bankrupt || st.under_liquidation || st.compulsory_liquidation)) return false;
      if (f === "negative" && (e.summary || {}).lead_basis !== "negative_equity") return false;
      if (f === "loss" && (e.summary || {}).lead_basis !== "loss_making") return false;
      if (["high", "medium", "low"].indexOf(f) > -1 && conf !== f) return false;
      if (!q) return true;
      return [e.legal_name, e.organisation_number, site.domain,
        ((valueOf(e, "legal_identity") || {}).municipality)].join(" ").toLowerCase().indexOf(q) > -1;
    });

    var page = list.slice(0, state.shown);
    var body = page.map(function (e) {
      var s = e.summary || {};
      var site = stateOf(e, "official_website");
      var conf = (s.website_confidence || {}).level || "none";
      var jobs = (valueOf(e, "job_postings") || []).length;
      return '<button class="row" data-org="' + esc(e.organisation_number) + '">' +
        '<span><span class="nm">' + esc(e.legal_name || "(no registry record)") + "</span>" +
        '<span class="mt">' + esc(e.organisation_number) +
        (s.latest_filed_period ? " · filed to " + esc(s.latest_filed_period) : "") + "</span>" +
        (s.lead ? '<span class="ld">' + esc(s.lead) + "</span>" : "") + "</span>" +
        '<span class="rt">' + stateTag(site) +
        // With no website there is no match to rate, and a pill reading
        // "match none" reads as a broken field rather than as an abstention.
        (conf === "none" ? "" : tag("c-" + conf, "match " + conf)) +
        (jobs ? tag("t-job", jobs + (jobs > 1 ? " ads" : " ad")) : "") + "</span></button>";
    }).join("");

    return '<div class="top">' + searchBar() + filterBar() + "</div>" +
      '<h2>' + list.length + " of " + ROWS.length + " companies</h2>" +
      '<p class="lede">Each line opens on the one fact its filed accounts or the entity ' +
      'register put first, with the register that holds it named in the sentence.</p>' +
      '<div class="rows" style="margin-top:16px">' + (body || '<p class="empty">No company matches.</p>') + "</div>" +
      (list.length > state.shown
        ? '<button class="more" data-more="1">Show ' +
          Math.min(PAGE, list.length - state.shown) + " more</button>" : "");
  }

  function searchBar() {
    return '<label class="search">' + '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/>' +
      '<path d="m20 20-3.5-3.5"/></svg>' +
      '<input id="q" type="search" placeholder="Search name, organisation number, place…" ' +
      'value="' + esc(state.q) + '" aria-label="Search companies"></label>';
  }

  function filterBar() {
    var opts = [["", "All companies"], ["site", "Website proved"], ["abstained", "Website abstained"],
      ["negative", "Negative equity"], ["loss", "Loss-making year"], ["adverse", "Adverse status"],
      ["jobs", "Advertising a job"], ["high", "Match: high"], ["medium", "Match: medium"],
      ["low", "Match: low"]];
    return '<select id="filter" aria-label="Filter companies">' + opts.map(function (o) {
      return '<option value="' + o[0] + '"' + (state.filter === o[0] ? " selected" : "") +
        ">" + esc(o[1]) + "</option>";
    }).join("") + "</select>";
  }

  // ------------------------------------------------------- company detail --
  function company(e) {
    var s = e.summary || {};
    var id = valueOf(e, "legal_identity") || {};
    var acc = (s.financial_history || []);
    var latest = acc[0] || {};
    var prev = acc[1] || {};
    var conf = s.website_confidence || {};
    var industry = (id.industry || [{}])[0] || {};

    function delta(key) {
      if (typeof latest[key] !== "number" || typeof prev[key] !== "number" || !prev[key]) return "";
      var pct = (latest[key] - prev[key]) / Math.abs(prev[key]) * 100;
      var dir = pct >= 0 ? "up" : "down";
      return '<div class="sm ' + dir + '">' + (pct >= 0 ? "+" : "") + pct.toFixed(1) +
        "% from the year before</div>";
    }

    var revs = acc.slice().reverse().map(function (a) { return a.revenue; });
    var peak = Math.max.apply(null, revs.map(function (v) { return typeof v === "number" ? Math.abs(v) : 0 }).concat([1]));
    var bars = revs.map(function (v, i) {
      var h = typeof v === "number" ? Math.max(3, Math.abs(v) / peak * 44) : 3;
      return '<s class="' + (i === revs.length - 1 ? "a" : "") + '" style="height:' + h.toFixed(0) + 'px"></s>';
    }).join("");

    var kpis =
      '<div class="k"><div class="lab">Filed accounts<br><b>from Regnskapsregisteret</b></div>' +
        '<div class="bars">' + (bars || "") + "</div>" +
        '<div class="sm">' + (acc.length ? acc.length + " filed year" + (acc.length > 1 ? "s" : "") +
          (s.financial_trend ? " · " + esc(s.financial_trend.replace(/^Across the \d+ filed years [\d ]+to \d+, /, "")) : "")
          : "none filed") + "</div></div>" +
      '<div class="k"><div class="tile" style="color:var(--blue)">' + icon("chart") + "</div>" +
        '<div class="lab">Revenue' + (latest.period_to ? " " + esc(String(latest.period_to).slice(0, 4)) : "") + "</div>" +
        '<div class="big">' + (typeof latest.revenue === "number"
          ? esc(nok(latest.revenue)).replace(/(bn|m|k)$/, "<s>$1</s>") : '<s>not reported</s>') + "</div>" +
        delta("revenue") + "</div>" +
      '<div class="k"><div class="tile" style="color:var(--purple)">' + icon("doc") + "</div>" +
        '<div class="lab">Annual result</div>' +
        '<div class="big">' + (typeof latest.annual_result === "number"
          ? esc(nok(latest.annual_result)).replace(/(bn|m|k)$/, "<s>$1</s>") : '<s>not reported</s>') + "</div>" +
        delta("annual_result") + "</div>" +
      '<div class="k"><div class="tile" style="color:var(--green)">' + icon("globe") + "</div>" +
        '<div class="lab">Website match</div>' +
        '<div class="big" style="text-transform:capitalize">' + esc(conf.level || "none") + "</div>" +
        '<div class="prog"><s style="width:' + Math.round((conf.score || 0) * 100) + '%"></s></div></div>';

    return '<div class="top"><span class="crumb"><button class="nv" data-view="companies" ' +
        'style="display:inline;padding:0;width:auto;color:inherit">Companies</button> › <b>' +
        esc(e.legal_name || e.organisation_number) + "</b></span></div>" +
      '<div class="kpis">' + kpis + "</div>" +
      '<p class="ind">' + (industry.label ? "Industry: <b>" + esc(industry.label) + "</b>" :
        "<b>No industry filed</b>") + "</p>" +
      "<h1>" + esc(e.legal_name || "(no registry record)") + "</h1>" +
      (s.lead ? '<div class="c" style="margin-top:18px;border-left:3px solid var(--blue)">' +
        '<p style="font-size:17px;line-height:1.5;letter-spacing:-.01em">' + esc(s.lead) + "</p>" +
        '<div class="sm" style="margin-top:9px;color:var(--faint);font-size:12.5px">' +
        "Read from " + esc(s.lead_source || "the register") + " · " +
        esc((e.evidence || []).length) + " source records for this company</div></div>" : "") +
      '<div class="ctl">' + statusPills(e) + "</div>" +
      '<div class="cols"><div>' + infoPanel(e) + notFoundPanel(e) + "</div>" +
      "<div>" + trailPanel(e) + "</div></div>";
  }

  function statusPills(e) {
    var s = e.summary || {};
    var st = valueOf(e, "operating_status") || {};
    var out = [];
    var tier = ((valueOf(e, "official_website") || {}).verification || {}).tier;
    if (tier === "A_org_number_on_page") out.push(tag("t-reg", "Organisation number found on the site"));
    else if (tier === "B_registry_declared_website") out.push(tag("t-reg", "Website filed with the registry"));
    else if (tier && tier.charAt(0) === "R") out.push(tag("s-ambiguous", "Filed website, host did not answer"));
    else if (tier) out.push(tag("t-site", "Matched on " + words(tier.slice(2))));
    else out.push(tag("s-" + stateOf(e, "official_website"), "Website " + words(stateOf(e, "official_website"))));
    if (st.bankrupt) out.push(tag("s-blocked", "Registered bankrupt"));
    if (st.under_liquidation) out.push(tag("s-blocked", "Under liquidation"));
    if (st.compulsory_liquidation) out.push(tag("s-blocked", "Compulsory liquidation"));
    if (s.open_positions) out.push(tag("t-job", s.open_positions + " open position" + (s.open_positions > 1 ? "s" : "")));
    return out.join("");
  }

  function row(ic, label, value) {
    return '<div class="info"><i>' + icon(ic) + '</i><span class="l">' + esc(label) +
      '</span><span class="v">' + value + "</span></div>";
  }

  function infoPanel(e) {
    var id = valueOf(e, "legal_identity") || {};
    var contact = valueOf(e, "registry_contact") || {};
    var emp = valueOf(e, "registered_employees") || {};
    var s = e.summary || {};
    var site = valueOf(e, "official_website") || {};
    var rows = [
      ["hash", "Organisation number", '<span class="m">' + esc(String(e.organisation_number)
        .replace(/(\d{3})(\d{3})(\d{3})/, "$1 $2 $3")) + "</span>"],
      ["doc", "Legal form", esc(id.legal_form_label || id.legal_form || "—")],
      ["cal", "Registered", '<span class="m">' + esc(id.registered_at || id.registration_date || "—") + "</span>"],
      ["pin", "Place", esc(id.municipality ? id.municipality.charAt(0) + id.municipality.slice(1).toLowerCase() : "—")],
      ["user", "Employees", emp && typeof emp.count === "number" ? esc(emp.count)
        : '<span class="nf">not registered</span>'],
      ["globe", "Website", s.verified_website
        ? '<a href="' + esc(s.verified_website) + '" target="_blank" rel="noopener">' + esc(host(s.verified_website)) + "</a>"
        : (s.registry_declared_website
          ? '<a href="' + esc(s.registry_declared_website) + '" target="_blank" rel="noopener">' +
            esc(host(s.registry_declared_website)) + "</a>" + tag("s-ambiguous", "filed, not reached")
          : '<span class="nf">' + esc(words(stateOf(e, "official_website"))) + "</span>")],
      ["share", "Profiles", (s.profiles || []).length
        ? (s.profiles || []).map(function (p) {
            return '<a href="' + esc(p) + '" target="_blank" rel="noopener">' + esc(host(p)) + "</a>";
          }).join(" ")
        : '<span class="nf">none published</span>']
    ];
    var cap = id.share_capital || {};
    if (typeof cap.belop === "number") {
      rows.push(["bank", "Share capital", '<span class="m">' +
        esc(nok(cap.belop)) + " " + esc(cap.valuta || "NOK") + "</span>"]);
    }
    if (id.parent_organisation_number) {
      rows.push(["briefcase", "Parent entity", '<span class="m">' +
        esc(id.parent_organisation_number) + "</span>"]);
    }
    if (contact.phone || contact.email) {
      rows.push(["doc", "Registry contact", '<span class="m">' +
        esc([contact.phone, contact.email].filter(Boolean).join(" · ")) + "</span>"]);
    }
    return '<div class="c"><div class="ch"><h3>Company info</h3>' +
      '<span class="n">Enhetsregisteret</span></div>' +
      rows.map(function (r) { return row(r[0], r[1], r[2]); }).join("") + "</div>";
  }

  function notFoundPanel(e) {
    var s = e.summary || {};
    var unknowns = s.unknowns || [];
    var attempts = ((e.operations || {}).identity_attempts) || [];
    var refused = attempts.filter(function (a) { return a.outcome !== "accepted"; });
    var vetoed = attempts.filter(function (a) { return (a.conflicts || []).length; });
    if (!unknowns.length && !refused.length) return "";
    return '<div class="c"><div class="ch"><h3>Checked, not found</h3>' +
      '<span class="n">' + unknowns.length + "</span></div>" +
      unknowns.map(function (u) {
        return '<div style="padding:9px 0;border-bottom:1px solid var(--line)">' +
          '<div style="display:flex;gap:8px;align-items:baseline;flex-wrap:wrap">' +
          '<b style="font-size:13.5px">' + esc(u.field) + "</b>" + stateTag(u.state) + "</div>" +
          '<div class="nf" style="margin-top:3px;font-style:normal">' + esc(u.reason) + "</div></div>";
      }).join("") +
      (refused.length ? '<p class="nf" style="margin-top:12px;font-style:normal">' +
        esc(refused.length) + " candidate " + (refused.length > 1 ? "domains were" : "domain was") +
        " considered and refused" +
        (vetoed.length ? ", " + esc(vetoed.length) + " of them because the page carried another " +
          "company's organisation number" : "") +
        ". Refused candidates are recorded in <code>operations</code> and are never reported as " +
        "this company's.</p>" : "") + "</div>";
  }

  // ------------------------------------------------------------- the trail --
  var TABS = [["trail", "Source trail"], ["accounts", "Accounts"],
    ["people", "Leadership"], ["places", "Locations"]];

  function trailPanel(e) {
    var body = state.tab === "accounts" ? accountsTab(e)
      : state.tab === "people" ? peopleTab(e)
      : state.tab === "places" ? placesTab(e)
      : trailTab(e);
    return '<div class="c"><div class="tabs">' + TABS.map(function (t) {
      return '<button data-tab="' + t[0] + '" aria-selected="' + (state.tab === t[0]) + '">' +
        esc(t[1]) + "</button>";
    }).join("") + "</div>" + body + "</div>";
  }

  // Which register a source belongs to, in the words a reader can check.
  function sourceLabel(ev) {
    var h = host(ev.source_url);
    if (h.indexOf("data.brreg.no") > -1 || h.indexOf("brreg.no") > -1) return ["t-reg", "Official register"];
    if (h.indexOf("nav.no") > -1) return ["t-job", "Public job service"];
    return ["t-site", "Company site"];
  }

  function trailTab(e) {
    var items = (e.claims || []).filter(function (c) {
      return c.availability === "available" && (c.evidence_ids || []).length;
    });
    if (!items.length) return '<p class="empty">No claim on this company resolved to a source.</p>';
    return items.map(function (c) {
      var ev = evidenceFor(e, c)[0] || {};
      var lab = sourceLabel(ev);
      return '<div class="ev"><div class="ev-h"><i>' + icon("doc") + "</i><div>" +
        "<h4>" + esc(headlineFor(c)) + "</h4>" +
        '<div class="ev-tags">' + tag(lab[0], lab[1]) + stateTag(c.availability) +
        (typeof c.confidence === "number" ? tag("c-none", "confidence " + c.confidence.toFixed(2)) : "") +
        "</div></div></div>" +
        (c.note ? "<p>" + esc(c.note) + "</p>" : "") +
        '<dl class="kv">' +
        '<dt>Page</dt><dd><a href="' + esc(ev.source_url) + '" target="_blank" rel="noopener">' +
          esc(ev.source_url) + "</a></dd>" +
        '<dt>Read</dt><dd>' + esc(String(ev.retrieved_at || "").slice(0, 19).replace("T", " ")) + " Z</dd>" +
        (ev.content_sha256 ? "<dt>Hash</dt><dd>sha256 " + esc(ev.content_sha256.slice(0, 20)) + "…</dd>" : "") +
        (ev.licence ? "<dt>Licence</dt><dd>" + esc(ev.licence) + "</dd>" : "") +
        (ev.http_status ? "<dt>Status</dt><dd>HTTP " + esc(ev.http_status) + "</dd>" : "") +
        "</dl>" +
        '<div class="ev-a"><a class="btn" href="' + esc(ev.source_url) + '" target="_blank" ' +
        'rel="noopener">Open the source page ↗</a></div></div>';
    }).join("");
  }

  // What this claim says, in one line, read off the claim itself.
  function headlineFor(c) {
    var v = c.value;
    if (c.field === "annual_accounts" && Array.isArray(v) && v.length) {
      var l = v.slice().sort(function (a, b) {
        return String(b.period_to).localeCompare(String(a.period_to)); })[0];
      return (typeof l.revenue === "number" ? "Revenue " + l.revenue.toLocaleString("en") + " NOK, " : "") +
        "period ending " + l.period_to;
    }
    if (c.field === "official_website") {
      return "Website " + (v || {}).domain + " matched to this company";
    }
    if (c.field === "legal_identity") return (v || {}).legal_name + " in the entity register";
    if (c.field === "leadership" && Array.isArray(v)) return v.length + " registered role holders";
    if (c.field === "registered_workplaces" && Array.isArray(v)) {
      return v.length + " registered workplace" + (v.length > 1 ? "s" : "");
    }
    if (c.field === "job_postings" && Array.isArray(v)) {
      return v.length + " advertisement" + (v.length > 1 ? "s" : "") + " on the public job service";
    }
    if (Array.isArray(v)) return v.length + " " + words(c.field);
    if (v && typeof v === "object") return words(c.field).replace(/^./, function (m) { return m.toUpperCase(); });
    return String(v == null ? words(c.field) : v).slice(0, 110);
  }

  function accountsTab(e) {
    var acc = (e.summary || {}).financial_history || [];
    if (!acc.length) {
      return '<p class="empty">' + esc(((claimOf(e, "annual_accounts") || {}).note) ||
        "No annual accounts are filed for this company.") + "</p>";
    }
    return "<table><tr><th>Period</th><th>Revenue</th><th>Operating</th><th>Result</th>" +
      "<th>Equity</th><th>Assets</th></tr>" + acc.map(function (a) {
        return "<tr><td>" + esc(a.period_to) + "</td><td>" + cell(a.revenue) + "</td><td>" +
          cell(a.operating_result) + "</td><td>" + cell(a.annual_result) + "</td><td>" +
          cell(a.equity) + "</td><td>" + cell(a.total_assets) + "</td></tr>";
      }).join("") + "</table>" +
      '<p class="nf" style="margin-top:11px;font-style:normal">Figures in NOK as filed. A blank ' +
      "is a field the filing does not carry, not a zero.</p>";
  }

  function peopleTab(e) {
    var people = valueOf(e, "leadership") || [];
    if (!people.length) {
      return '<p class="empty">' + esc(((claimOf(e, "leadership") || {}).note) ||
        "No registered roles.") + "</p>";
    }
    return "<table><tr><th>Name</th><th>Role</th><th>Code</th></tr>" + people.map(function (p) {
      return "<tr><td>" + esc(p.name) + "</td><td>" + esc(p.role || "—") + '</td><td class="m">' +
        esc(p.role_code || "") + "</td></tr>";
    }).join("") + "</table>";
  }

  function placesTab(e) {
    var places = valueOf(e, "registered_workplaces") || [];
    var id = valueOf(e, "legal_identity") || {};
    var business = addressOf(id.business_address);
    var postal = addressOf(id.postal_address);
    var head = (business ? row("pin", "Registered address", esc(business)) : "") +
      (postal && postal !== business ? row("doc", "Postal address", esc(postal)) : "");
    if (!places.length) return head || '<p class="empty">No registered workplace.</p>';
    return head + '<table class="wp" style="margin-top:10px"><tr><th>Subunit</th>' +
      "<th>Name</th><th>Where</th><th>Employees</th></tr>" + places.map(function (w) {
        return '<tr><td class="m">' + esc(w.organisation_number || "") + "</td><td>" +
          esc(w.name || "") + "</td><td>" + esc(addressOf(w.address) || "—") + "</td><td>" +
          (typeof w.employees === "number" ? esc(w.employees)
            : '<span class="nf">not registered</span>') + "</td></tr>";
      }).join("") + "</table>";
  }

  // ------------------------------------------------------------ coverage --
  function countBy(fn) {
    var out = {};
    ROWS.forEach(function (e) {
      var k = fn(e);
      if (k == null) return;
      out[k] = (out[k] || 0) + 1;
    });
    return out;
  }

  function barTable(counts, order) {
    var keys = order || Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; });
    var total = keys.reduce(function (n, k) { return n + (counts[k] || 0); }, 0) || 1;
    return '<table class="bt">' + keys.filter(function (k) { return counts[k]; }).map(function (k) {
      var n = counts[k] || 0;
      return "<tr><td>" + esc(words(k)) + "</td><td>" +
        '<span class="prog" style="display:block"><s style="width:' +
        (n / total * 100).toFixed(1) + '%"></s></span></td><td class="m">' + n +
        '</td><td class="m" style="color:var(--faint)">' + (n / total * 100).toFixed(0) + "%</td></tr>";
    }).join("") + "</table>";
  }

  function coverage() {
    var fields = ["legal_identity", "annual_accounts", "leadership", "registered_workplaces",
      "official_website", "company_owned_profiles", "job_postings", "registry_announcements"];
    var rowsHtml = fields.map(function (f) {
      var c = countBy(function (e) { return stateOf(e, f); });
      var avail = c.available || 0;
      return "<tr><td>" + esc(words(f)) + '</td><td class="m">' + avail +
        '</td><td class="m">' + (c.ambiguous || 0) + '</td><td class="m">' + (c.blocked || 0) +
        '</td><td class="m">' + (c.not_available || 0) + '</td><td class="m">' +
        (c.not_checked || 0) + "</td></tr>";
    }).join("");

    var tiers = countBy(function (e) {
      return ((valueOf(e, "official_website") || {}).verification || {}).tier;
    });
    var leads = countBy(function (e) { return (e.summary || {}).lead_basis; });
    var attempts = ROWS.reduce(function (n, e) {
      return n + (((e.operations || {}).identity_attempts) || []).length; }, 0);
    var vetoed = ROWS.reduce(function (n, e) {
      return n + (((e.operations || {}).identity_attempts) || []).filter(function (a) {
        return (a.conflicts || []).length; }).length; }, 0);

    return '<h2>Coverage across this run</h2>' +
      '<p class="lede">Counted from the envelopes loaded in this page, not asserted. ' +
      'A checked negative and an unchecked field are different columns, because they are ' +
      'different facts.</p>' +
      '<div class="c" style="margin-top:18px"><div class="ch"><h3>Field by state</h3>' +
      '<span class="n">' + ROWS.length + " companies</span></div>" +
      "<table><tr><th>Field</th><th>Available</th><th>Ambiguous</th><th>Blocked</th>" +
      "<th>Not available</th><th>Not checked</th></tr>" + rowsHtml + "</table></div>" +
      '<div class="cols" style="margin-top:15px"><div class="c">' +
      '<div class="ch"><h3>How each website was proved</h3></div>' + barTable(tiers) +
      '<p class="nf" style="margin-top:10px;font-style:normal">' + attempts +
      " candidate domains were examined in all; " + vetoed +
      " were refused for carrying another company's organisation number.</p></div>" +
      '<div class="c" style="margin-top:0"><div class="ch"><h3>What each summary opens on</h3></div>' +
      barTable(leads) + '<p class="nf" style="margin-top:10px;font-style:normal">The lead is the ' +
      "one fact the summary states first, chosen by what a reader decides on first rather than " +
      "by the register's own field order.</p></div></div>";
  }

  // --------------------------------------------------------------- method --
  var TIER_TEXT = [
    ["A_org_number_on_page", "0.99",
      "The company's own organisation number is printed on the page. Nothing else can be read off a website that is stronger than this."],
    ["B_registry_declared_website", "0.95",
      "The company filed this address with the entity register itself, and the host answered."],
    ["C_registry_email_domain_plus_corroboration", "0.88",
      "The domain is the one in the company's registered e-mail address, and the page also carries a registered phone number, the registered street, or the legal name."],
    ["D_site_identity_name_plus_locator", "0.80",
      "The site names the company and carries one registry-derived locator. Published as a low-confidence match, and labelled as one."],
    ["R_registry_declared_site_not_reached", "0.75",
      "The register carries a filed website whose host did not answer on this run. Published on the register's authority alone, with no page of the site read, and never called verified."]
  ];

  function proof() {
    var tiers = countBy(function (e) {
      return ((valueOf(e, "official_website") || {}).verification || {}).tier; });
    return '<div class="doc"><h2>How a website match is proved</h2>' +
      '<p>Matching a company to a website is the step that invents facts if it is done ' +
      'loosely. A name-similarity match will hand a Norwegian café the website of an ' +
      'American one. Every match here is placed on a rung of a ladder, and the rung it sits ' +
      'on is published with it, so a reader can discount it rather than trust it.</p>' +
      "<table><tr><th>Basis</th><th>Confidence</th><th>In this run</th></tr>" +
      TIER_TEXT.map(function (t) {
        return '<tr><td style="max-width:46ch"><b style="color:var(--ink)">' +
          esc(words(t[0].slice(2))) + "</b><br>" + esc(t[2]) + '</td><td class="m">' + t[1] +
          '</td><td class="m">' + (tiers[t[0]] || 0) + "</td></tr>";
      }).join("") + "</table>" +
      '<div class="warn"><b>A conflicting organisation number is a veto.</b> If the page ' +
      'carries a different company\'s organisation number, the candidate is refused however ' +
      'well it matches on name, phone or address — and it stays refused even when the ' +
      'register says the company filed that very domain. The register says what was filed; ' +
      'the page says whose it is now.</div>' +
      "<h3>Where the summary gets its opening fact</h3>" +
      "<p>The summary opens on one fact and names the register that holds it. The order is " +
      "what a reader deciding whether to deal with this company reads first: a company the " +
      "register has wound up outranks any figure it ever filed; a balance sheet that owes " +
      "more than it owns outranks a growth rate; a growth rate outranks the size of one " +
      "year; anything in the accounts outranks an activity signal.</p>" +
      "<h2>What the six states mean</h2>" +
      "<p>Absence is never written as a zero, an empty string or an empty list. A company " +
      "with no registered employee count carries a null and a note saying so, because a " +
      "filed zero and a missing field are different facts and a reader comparing companies " +
      "has to be able to tell them apart.</p>" +
      "<table><tr><th>State</th><th>Means</th></tr>" +
      [["available", "found, with a value and resolving evidence"],
       ["not_available", "checked; the source holds nothing"],
       ["blocked", "the source refused the request (401, 403, 429, or robots)"],
       ["not_applicable", "the question does not apply to this entity"],
       ["ambiguous", "something was found but could not be tied to this exact entity"],
       ["failed", "the attempt broke"]].map(function (r) {
        return "<tr><td>" + stateTag(r[0]) + "</td><td>" + esc(r[1]) + "</td></tr>";
      }).join("") + "</table></div>";
  }

  function registers() {
    var byLicence = {};
    var byClass = {};
    ROWS.forEach(function (e) {
      (e.evidence || []).forEach(function (x) {
        // The licence string carries its own URL. Kept whole on the evidence
        // record; here it is the name that identifies the regime.
        var l = (x.licence || "no open licence stated")
          .replace(/\s*\(https?:\/\/[^)]*\)/, "");
        byLicence[l] = (byLicence[l] || 0) + 1;
        var k = x.source_class || "unknown";
        byClass[k] = (byClass[k] || 0) + 1;
      });
    });
    return '<div class="doc"><h2>Registers and licences</h2>' +
      '<p>Three different permissions are at work in one envelope, and they are not ' +
      'interchangeable. Treating a company\'s own website as open data because the registry ' +
      'beside it is open data would be the easiest mistake to make here, so the licence ' +
      'travels on every single source record rather than being asserted once in a readme.</p>' +
      '<div class="cols" style="margin-top:16px"><div class="c">' +
      '<div class="ch"><h3>Licence on each source record</h3></div>' + barTable(byLicence) + "</div>" +
      '<div class="c" style="margin-top:0"><div class="ch"><h3>What kind of source it was</h3></div>' +
      barTable(byClass) + "</div></div>" +
      "<h3>Brønnøysundregistrene — Enhetsregisteret and Regnskapsregisteret</h3>" +
      "<p>The entity register and the accounts register are published under NLOD 2.0, which " +
      "permits reuse with attribution. The organisation number is the join key that makes " +
      "every other source in this run attachable to a named legal entity.</p>" +
      "<h3>arbeidsplassen.nav.no — the public job service</h3>" +
      "<p>Advertisements come from NAV's published feed under the Arbeidsplassen API terms. " +
      "NAV attaches an advertisement to the registered subunit rather than to the parent " +
      "company, so a parent with ads filed against its subunits shows nothing at all unless " +
      "the subunit numbers are resolved first and searched on.</p>" +
      "<h3>The company's own website</h3>" +
      "<p>A company site carries no open licence. What it has is an access basis: robots.txt " +
      "was honoured, the request was rate-limited and identified, and what was read is " +
      "recorded with its hash so the claim can be checked against the page as it stood. " +
      "Nothing from a site is republished beyond the span a claim rests on.</p></div>";
  }

  // ---------------------------------------------------------------- shell --
  function route() {
    var main = el("main");
    if (!ROWS.length) {
      main.innerHTML = '<p class="empty">No run data loaded.<br><br>' +
        'This page reads <code>profiles.js</code> from the same folder. Regenerate it with ' +
        '<code>./.venv/bin/python tools/build_viewer.py</code>.</p>';
      return;
    }
    var e = state.org && ROWS.find(function (r) { return r.organisation_number === state.org; });
    main.innerHTML = e ? company(e)
      : state.view === "coverage" ? coverage()
      : state.view === "proof" ? proof()
      : state.view === "registers" ? registers()
      : companies();
    main.scrollTop = 0;
    window.scrollTo(0, 0);
    Array.prototype.forEach.call(document.querySelectorAll("aside .nv"), function (b) {
      if (b.dataset.view === state.view && !state.org) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });
    var q = el("q");
    if (q) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  }

  document.addEventListener("click", function (ev) {
    var nav = ev.target.closest("[data-view]");
    if (nav) { state.view = nav.dataset.view; state.org = null; state.shown = PAGE; return route(); }
    var row = ev.target.closest("[data-org]");
    if (row) { state.org = row.dataset.org; state.tab = "trail"; return route(); }
    var tab = ev.target.closest("[data-tab]");
    if (tab) { state.tab = tab.dataset.tab; return route(); }
    if (ev.target.closest("[data-more]")) { state.shown += PAGE; return route(); }
    if (ev.target.closest("#theme")) {
      var root = document.documentElement;
      var now = root.getAttribute("data-theme");
      var dark = now ? now === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      root.setAttribute("data-theme", dark ? "light" : "dark");
      try { localStorage.setItem("signalpost-theme", dark ? "light" : "dark"); } catch (err) {}
    }
  });
  // An evaluator can point the page at the output of their own run without
  // rebuilding the payload. A file the reader chooses is readable from a
  // file:// page, which is the whole reason this control exists rather than a
  // second fetch.
  document.addEventListener("change", function (ev) {
    if (ev.target.id !== "file") return;
    var f = ev.target.files && ev.target.files[0];
    if (!f) return;
    f.text().then(function (text) {
      var rows = text.split("\n").filter(function (l) { return l.trim(); })
        .map(function (l) { try { return JSON.parse(l); } catch (err) { return null; } })
        .filter(Boolean);
      if (!rows.length) {
        el("runline").textContent = "no envelopes found in " + f.name;
        return;
      }
      state = {view: "companies", org: null, q: "", filter: "", tab: "trail", shown: PAGE};
      load(rows);
    });
  });

  document.addEventListener("input", function (ev) {
    if (ev.target.id === "q") { state.q = ev.target.value; state.shown = PAGE; route(); }
  });
  document.addEventListener("change", function (ev) {
    if (ev.target.id === "filter") { state.filter = ev.target.value; state.shown = PAGE; route(); }
  });

  try {
    var saved = localStorage.getItem("signalpost-theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);
  } catch (err) {}

  if (Array.isArray(window.SIGNALPOST_PROFILES)) {
    load(window.SIGNALPOST_PROFILES);
  } else {
    // Served over http the JSONL is readable directly, which keeps the viewer
    // working against a freshly written run without rebuilding the payload.
    fetch("profiles.jsonl").then(function (r) { return r.ok ? r.text() : Promise.reject(); })
      .then(function (t) {
        load(t.split("\n").filter(function (l) { return l.trim(); }).map(function (l) {
          try { return JSON.parse(l); } catch (err) { return null; }
        }).filter(Boolean));
      }).catch(function () { route(); });
  }
})();

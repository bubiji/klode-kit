/*
 * klode-kit confirm page exporter (WI-08b).
 *
 * One script, two hosts:
 *   - embedded verbatim in confirm.html: wires the radio buttons and the export button, and downloads
 *     choices.json (a blob: URL; the page loads nothing from the network);
 *   - run by node:  node confirm_export.js <confirm.html> [<selections.json>]
 *     prints choices.json on stdout. selections.json is a list of clicks
 *     {field_id, kind: "route"|"manual"|"none", route?, value?, reason?: "not_a_field" | "acknowledged"}; the last click on a
 *     field wins. Exit 0 ok, 2 usage / a click the page cannot produce, 30 the page was edited (its bytes no
 *     longer match its sha256 stamp).
 *
 * The export reads only the page's data block (klode-kit-confirm-data). kb-verifier advice is shown in the
 * page but is not in the data block, so it can never reach choices.json. klode_kit.audit.confirm_page has
 * the same exporter in Python (export_choices); the cli_integration lane checks both give the same output.
 */
(function () {
  "use strict";

  var SCHEMA = "klode-kit.choices/1";
  var STAMP_NAME = "klode-kit-confirm-sha256";
  var DATA_ID = "klode-kit-confirm-data";
  // blank = only white space as Python's str.isspace() defines it (the Python exporter uses the same set)
  var BLANK = /^[\t\n\u000b\u000c\r\u001c-\u001f \u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*$/;
  var KEYS = { route: ["field_id", "kind", "route"], manual: ["field_id", "kind", "value"],
               none: ["field_id", "kind", "reason"] };

  function ExportError(message, code) {
    this.message = message;
    this.code = code || 2;
  }

  function own(obj, key) {
    return Object.prototype.hasOwnProperty.call(obj, key);
  }

  function index(data) {
    var out = {};
    data.records.forEach(function (rec) {
      rec.fields.forEach(function (f) {
        out[f.field_id] = { rec: rec, field: f };
      });
    });
    return out;
  }

  function choiceOf(sel, entry) {
    if (sel === null || typeof sel !== "object" || Array.isArray(sel)) {
      throw new ExportError("a click must be an object");
    }
    var kind = sel.kind;
    if (!own(KEYS, kind)) {
      throw new ExportError("unknown choice kind " + JSON.stringify(kind) + " for " + sel.field_id);
    }
    Object.keys(sel).forEach(function (k) {
      if (KEYS[kind].indexOf(k) < 0) {
        throw new ExportError("key " + JSON.stringify(k) + " does not belong to a " + kind + " choice");
      }
    });
    var f = entry.field;
    var pageItem = f.page_item === true;
    if (pageItem && kind === "route") {
      throw new ExportError(f.field_id + " is a page item: acknowledge it or type a value, a route cannot be chosen");
    }
    if (kind === "route") {
      var r = sel.route;
      if (typeof r !== "string" || !own(f.seen, r) || typeof f.seen[r] !== "string") {
        throw new ExportError("route " + JSON.stringify(r) + " has no value for " + f.field_id);
      }
      return { choice: { kind: "route", route: r } };
    }
    if (kind === "manual") {
      if (typeof sel.value !== "string") {
        throw new ExportError("a typed value must be a string (" + f.field_id + ")");
      }
      if (BLANK.test(sel.value)) {
        if (pageItem) {
          throw new ExportError("a typed value for the page item " + f.field_id + " must not be blank");
        }
        return { choice: { kind: "none" } };
      }
      return { choice: { kind: "manual", value: sel.value } };
    }
    var allowed = pageItem ? "acknowledged" : "not_a_field";
    if (pageItem && sel.reason !== allowed) {
      throw new ExportError("a page item is acknowledged (reason 'acknowledged') or typed (" + f.field_id + ")");
    }
    if (own(sel, "reason") && sel.reason !== allowed) {
      throw new ExportError("the only reason for 'none' is " + allowed + " (" + f.field_id + ")");
    }
    var out = { choice: { kind: "none" } };
    if (sel.reason === allowed) {
      out.evidence = { reason: allowed };
    }
    return out;
  }

  function buildChoices(data, selections, sha) {
    if (!Array.isArray(selections)) {
      throw new ExportError("selections must be a list of clicks");
    }
    var idx = index(data);
    var picked = {};
    selections.forEach(function (sel) {
      var fid = sel && sel.field_id;
      if (typeof fid !== "string" || !own(idx, fid)) {
        throw new ExportError("field " + JSON.stringify(fid) + " is not on this confirm page");
      }
      if (!idx[fid].rec.clickable) {
        throw new ExportError("field " + fid + " belongs to a page with a missing route: not selectable");
      }
      picked[fid] = choiceOf(sel, idx[fid]);
    });
    var items = [];
    data.records.forEach(function (rec) {
      rec.fields.forEach(function (f) {
        if (!own(picked, f.field_id)) {
          return;
        }
        var item = { rec_id: rec.rec_id, rev_base: rec.rev_base, field_id: f.field_id, cell: f.cell,
                     choice: picked[f.field_id].choice, seen: f.seen };
        if (picked[f.field_id].evidence) {
          item.evidence = picked[f.field_id].evidence;
        }
        items.push(item);
      });
    });
    return { schema: SCHEMA, batch_id: data.batch_id, audit_rev: data.audit_rev, confirm_sha256: sha,
             fingerprints: data.fingerprints, items: items };
  }

  // ------------------------------------------------------------------ node

  function nodeMain() {
    var fs = require("fs");
    var crypto = require("crypto");
    var args = process.argv.slice(2);
    try {
      if (args.length < 1 || args.length > 2) {
        throw new ExportError("usage: node confirm_export.js <confirm.html> [<selections.json>]");
      }
      var buf;
      try {
        buf = fs.readFileSync(args[0]);
      } catch (e) {
        throw new ExportError("cannot read " + args[0] + ": " + e.message);
      }
      var prefix = Buffer.from("<meta name=\"" + STAMP_NAME + "\" content=\"", "utf8");
      var at = buf.indexOf(prefix);
      var start = at + prefix.length;
      var stamp = at < 0 ? "" : buf.slice(start, start + 64).toString("latin1");
      if (!/^[0-9a-f]{64}$/.test(stamp)) {
        throw new ExportError(args[0] + " is not a klode-kit confirm page (no sha256 stamp)", 30);
      }
      var zeroed = Buffer.concat([buf.slice(0, start), Buffer.alloc(64, "0"), buf.slice(start + 64)]);
      if (crypto.createHash("sha256").update(zeroed).digest("hex") !== stamp) {
        throw new ExportError(args[0] + " was edited after it was written (its bytes do not match its stamp)", 30);
      }
      var text = buf.toString("utf8");
      var open = "<script type=\"application/json\" id=\"" + DATA_ID + "\">";
      var i = text.indexOf(open);
      var j = i < 0 ? -1 : text.indexOf("</" + "script>", i);
      if (i < 0 || j < 0) {
        throw new ExportError(args[0] + " has no data block", 30);
      }
      var data = JSON.parse(text.slice(i + open.length, j));
      var selections = [];
      if (args.length === 2) {
        try {
          selections = JSON.parse(fs.readFileSync(args[1], "utf8"));
        } catch (e) {
          throw new ExportError("cannot read selections " + args[1] + ": " + e.message);
        }
      }
      process.stdout.write(JSON.stringify(buildChoices(data, selections, stamp), null, 1) + "\n");
      return 0;
    } catch (e) {
      if (!(e instanceof ExportError)) {
        throw e;
      }
      process.stderr.write("confirm_export: " + e.message + "\n");
      return e.code;
    }
  }

  // ------------------------------------------------------------------ browser

  function browserMain() {
    var meta = document.querySelector("meta[name=\"" + STAMP_NAME + "\"]");
    var data = JSON.parse(document.getElementById(DATA_ID).textContent);
    var status = document.getElementById("kk-status");
    var button = document.getElementById("kk-export");
    var all = [];
    data.records.forEach(function (rec) {
      if (rec.clickable) {
        rec.fields.forEach(function (f) { all.push(f.field_id); });
      }
    });

    function selections() {
      var out = [];
      Array.prototype.forEach.call(document.querySelectorAll("fieldset.kk-item"), function (fs) {
        var fid = fs.getAttribute("data-field-id");
        var on = fs.querySelector("input[type=radio]:checked");
        if (!on) {
          return;
        }
        var v = on.value;
        if (v.indexOf("route:") === 0) {
          out.push({ field_id: fid, kind: "route", route: v.slice(6) });
        } else if (v === "manual") {
          out.push({ field_id: fid, kind: "manual", value: fs.querySelector("input.kk-manual").value });
        } else if (v === "none:not_a_field") {
          out.push({ field_id: fid, kind: "none", reason: "not_a_field" });
        } else if (v === "none:acknowledged") {
          out.push({ field_id: fid, kind: "none", reason: "acknowledged" });
        } else {
          out.push({ field_id: fid, kind: "none" });
        }
      });
      return out;
    }

    function progress() {
      if (status) {
        status.textContent = "已选 " + selections().length + " / " + all.length;
      }
    }

    Array.prototype.forEach.call(document.querySelectorAll("input.kk-manual"), function (box) {
      box.addEventListener("input", function () {
        var radio = box.closest("li").querySelector("input[type=radio]");
        if (radio) {
          radio.checked = true;
        }
        progress();
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll("input[type=radio]"), function (r) {
      r.addEventListener("change", progress);
    });
    if (button) {
      button.addEventListener("click", function () {
        try {
          var choices = buildChoices(data, selections(), meta.getAttribute("content"));
          var blob = new Blob([JSON.stringify(choices, null, 1) + "\n"], { type: "application/json" });
          var a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = "choices.json";
          document.body.appendChild(a);
          a.click();
          a.remove();
          status.textContent = "已导出 " + choices.items.length + " 条（未点完的记录写回时会被拒绝）";
        } catch (e) {
          status.textContent = "导出失败：" + (e && e.message ? e.message : e);
        }
      });
    }
    progress();
  }

  if (typeof module === "object" && module && module.exports) {
    module.exports = { buildChoices: buildChoices, ExportError: ExportError };
    if (typeof require === "function" && require.main === module) {
      process.exitCode = nodeMain();
    }
  } else if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", browserMain);
    } else {
      browserMain();
    }
  }
})();

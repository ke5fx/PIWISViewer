// PIWISViewer web engine: JavaScript port of the translation and
// rendering logic in piwis_val_viewer.py. The dictionaries, report CSS,
// and report JS are NOT duplicated here -- they are extracted from the
// Python file by make_web.py and passed to PIWIS.init() as data, so the
// Python file remains the single source of truth.
//
// The renderer consumes a plain object tree:
//   node = { tag: str, attrib: {name: value}, text: str|null,
//            children: [node, ...] }
// which the browser builds from DOMParser output and the Node parity
// harness builds from a JSON dump of Python's ElementTree.

(function (global) {
    'use strict';

    var PIWIS = {};
    var D = null;            // injected data (dicts, CSS, JS, APP_NAME)
    var TRANSLIT = null;     // codepoint -> replacement string
    var PHRASE_RE = null;
    var STEMS = null;
    var LABEL_CACHE = null;
    var VALUE_CACHE = null;

    // ---------------- small utilities ----------------

    function reEscape(s) {
        return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    // mirror of Python html.escape(s, quote=True)
    function esc(text) {
        if (text === null || text === undefined) return '';
        return String(text).trim()
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
            .replace(/'/g, '&#x27;');
    }

    // mirror of norm_de(): transliterate, collapse whitespace
    function normDe(text) {
        if (text === null || text === undefined || text === '') return '';
        var out = [];
        var s = String(text);
        for (var i = 0; i < s.length; i++) {
            var cp = s.charCodeAt(i);
            var rep = TRANSLIT[cp];
            out.push(rep !== undefined ? rep : s.charAt(i));
        }
        return out.join('').replace(/\s+/g, ' ').trim();
    }

    // Python round() (banker's rounding) for unit conversion
    function pyRound(x) {
        var f = Math.floor(x);
        var d = x - f;
        if (d < 0.5) return f;
        if (d > 0.5) return f + 1;
        return (f % 2 === 0) ? f : f + 1;
    }

    // ---------------- tree accessors (ElementTree subset) ----------------

    function matchSeg(node, seg) {
        var m = /^([^\[]+)(?:\[@([A-Za-z_]+)='([^']*)'\])?$/.exec(seg);
        if (!m) return false;
        if (node.tag !== m[1]) return false;
        if (m[2] !== undefined) {
            return (node.attrib[m[2]] || null) === m[3];
        }
        return true;
    }

    function findall(node, path) {
        if (path.slice(0, 3) === './/') {
            var tag = path.slice(3);
            var acc = [];
            (function walk(n) {
                for (var i = 0; i < n.children.length; i++) {
                    var c = n.children[i];
                    if (c.tag === tag) acc.push(c);
                    walk(c);
                }
            })(node);
            return acc;
        }
        var segs = path.split('/');
        var cur = [node];
        for (var s = 0; s < segs.length; s++) {
            var next = [];
            for (var i = 0; i < cur.length; i++) {
                var ch = cur[i].children;
                for (var j = 0; j < ch.length; j++) {
                    if (matchSeg(ch[j], segs[s])) next.push(ch[j]);
                }
            }
            cur = next;
        }
        return cur;
    }

    function find(node, path) {
        var all = findall(node, path);
        return all.length ? all[0] : null;
    }

    function findtext(node, path) {
        var n = find(node, path);
        if (n === null) return null;
        return n.text === null || n.text === undefined ? '' : n.text;
    }

    // ---------------- translation engine ----------------

    function buildPhraseRe() {
        var dotted = [], plain = [], k;
        for (k in D.TRANS_PHRASE) {
            if (k.slice(-1) === '.') dotted.push(k);
            else plain.push(k);
        }
        var byLen = function (a, b) { return b.length - a.length; };
        dotted.sort(byLen);
        plain.sort(byLen);
        var pat = '(?<![A-Za-z])(' + dotted.map(reEscape).join('|')
            + (dotted.length ? '|' : '')
            + '(?:' + plain.map(reEscape).join('|')
            + ')(?![A-Za-z]))';
        return new RegExp(pat, 'gi');
    }

    function buildStems() {
        var stems = {}, k;
        for (k in D.EXTRA_STEMS) stems[k] = D.EXTRA_STEMS[k];
        var excl = {};
        for (var i = 0; i < D.STEM_EXCLUDE.length; i++) {
            excl[D.STEM_EXCLUDE[i]] = true;
        }
        for (k in D.TRANS_PHRASE) {
            if (/^[a-z]+$/.test(k) && k.length >= 3 && !excl[k]) {
                stems[k] = D.TRANS_PHRASE[k];
            }
        }
        return stems;
    }

    function decompose(word, depth) {
        depth = depth || 0;
        if (depth > 3 || word.length < 6) return null;
        for (var end = word.length - 3; end > 2; end--) {
            var eng = STEMS[word.slice(0, end)];
            if (eng === undefined) continue;
            var rest = word.slice(end);
            var links = ['', 's', 'es', 'n', 'en', 'e'];
            for (var li = 0; li < links.length; li++) {
                var link = links[li];
                if (rest.slice(0, link.length) !== link) continue;
                var r2 = rest.slice(link.length);
                if (r2.length < 3) continue;
                var sub = STEMS[r2];
                if (sub === undefined) sub = decompose(r2, depth + 1);
                if (sub) return eng + ' ' + sub;
            }
        }
        return null;
    }

    function underscoreStyle(offset, matched, whole, eng) {
        var before = offset > 0 ? whole.charAt(offset - 1) : '';
        var after = offset + matched.length < whole.length
            ? whole.charAt(offset + matched.length) : '';
        if (before === '_' || after === '_') {
            return eng.split(/\s+/).join('_');
        }
        return eng;
    }

    function phrasePass(seg) {
        // pass 1: known words and phrases, whole-word
        var out = seg.replace(PHRASE_RE,
            function (matched, p1, offset, whole) {
                var eng = D.TRANS_PHRASE[matched.toLowerCase()];
                eng = underscoreStyle(offset, matched, whole, eng);
                if (matched.slice(-1) === '.'
                        && offset + matched.length < whole.length
                        && /[A-Za-z]/.test(
                            whole.charAt(offset + matched.length))) {
                    eng += ' ';
                }
                return eng;
            });

        // pass 2: decompose remaining long words into known stems
        out = out.replace(/[A-Za-z]{6,}/g,
            function (word, offset, whole) {
                var lw = word.toLowerCase();
                var dec = STEMS[lw] || decompose(lw, 0);
                if (!dec) {
                    var sufs = ['es', 'en', 's', 'n', 'e'];
                    for (var i = 0; i < sufs.length; i++) {
                        var suf = sufs[i];
                        if (lw.slice(-suf.length) === suf
                                && lw.length - suf.length >= 6) {
                            var base = lw.slice(0, lw.length - suf.length);
                            dec = STEMS[base] || decompose(base, 0);
                            if (dec) break;
                        }
                    }
                }
                if (!dec) return word;
                return underscoreStyle(offset, word, whole, dec);
            });

        // collapse doubled articles from stacked replacements
        out = out.replace(/\b(?:of the|the) the\b/g, 'the');
        if (out !== seg && /^[A-Z]/.test(seg) && /^[a-z]/.test(out)) {
            out = out.charAt(0).toUpperCase() + out.slice(1);
        }
        return out;
    }

    function translateSegment(seg) {
        if (!seg) return seg;
        var key = seg.replace(/\d+/g, '#');
        var tmpl = D.TRANS_SEGMENT[key];
        if (tmpl !== undefined) {
            var nums = seg.match(/\d+/g) || [];
            var hashes = (tmpl.match(/#/g) || []).length;
            if (hashes === nums.length) {
                for (var i = 0; i < nums.length; i++) {
                    tmpl = tmpl.replace('#', nums[i]);
                }
                return tmpl;
            }
        }
        return phrasePass(seg);
    }

    function translateText(text, exactFirst) {
        var norm = normDe(text);
        if (!norm) return norm;
        if (exactFirst) {
            var hit0 = exactFirst[norm];
            if (hit0 !== undefined) return hit0;
        }
        var hit = D.TRANS_LABEL[norm];
        if (hit !== undefined) return hit;
        var parts = norm.split(/(\s*:\s+|\s+-\s+)/);
        for (var i = 0; i < parts.length; i += 2) {
            parts[i] = translateSegment(parts[i]);
        }
        return parts.join('');
    }

    function translateLabel(text) {
        var norm = normDe(text);
        if (!(norm in LABEL_CACHE)) {
            LABEL_CACHE[norm] = translateText(norm, null);
        }
        return LABEL_CACHE[norm];
    }

    function translateValue(text) {
        var norm = normDe(text);
        if (!(norm in VALUE_CACHE)) {
            VALUE_CACHE[norm] = translateText(norm, D.TRANS_VALUE);
        }
        return VALUE_CACHE[norm];
    }

    // ---------------- rendering ----------------

    function trHtml(orig, translated) {
        orig = (orig === null || orig === undefined) ? '' : String(orig).trim();
        if (!orig) return '';
        if (translated === normDe(orig)) return esc(orig);
        return '<span title="' + esc(orig) + '">' + esc(translated)
            + '</span>';
    }

    function labelHtml(orig) { return trHtml(orig, translateLabel(orig)); }
    function valueHtml(orig) { return trHtml(orig, translateValue(orig)); }

    function unitConversion(txt, unit) {
        var factor, cunit;
        if (unit === 'km') { factor = 0.621371; cunit = 'mi'; }
        else if (unit === 'bar') { factor = 14.5038; cunit = 'psi'; }
        else return '';
        var t = txt.replace(/,/g, '.');
        if (!/^\s*[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?\s*$/.test(t)) {
            return '';
        }
        var val = Number(t);
        if (isNaN(val)) return '';
        var conv = val * factor;
        var s;
        if (txt.indexOf('.') >= 0 || txt.indexOf(',') >= 0) {
            s = conv.toFixed(1);
        } else {
            s = String(pyRound(conv));
        }
        return ' (' + s + '&nbsp;' + cunit + ')';
    }

    function valueCell(v) {
        var txt = (v.text || '').trim();
        var unit = (v.attrib.UNIT || '').trim();
        var body = valueHtml(txt);
        if (unit) {
            var tu = D.TRANS_UNIT[normDe(unit)];
            var u = esc(tu !== undefined ? tu : unit);
            var cell = body ? (body + '&nbsp;' + u) : u;
            return cell + unitConversion(txt, unit);
        }
        return body;
    }

    function kvRow(label, value) {
        return '<tr><td class="lbl">' + label + '</td><td>' + value
            + '</td></tr>';
    }

    function headerCards(root) {
        var rh = find(root, 'RESULTSHEADER');
        var res = find(root, 'RESULT');
        var hd = res !== null ? find(res, 'HEADER') : null;
        var eq = hd !== null ? find(hd, 'EQUIPMENT') : null;

        function gt(parent, path) {
            return parent !== null ? esc(findtext(parent, path)) : '';
        }
        function gtu(parent, path) {
            if (parent === null) return '';
            var el = find(parent, path);
            if (el === null) return '';
            return valueCell(el);
        }

        var veh = rh !== null ? find(rh, 'VEHICLE') : null;
        var proto = gt(hd, 'PROTOKOLLTYPE');
        var protoDisp = D.PROTOCOL_TYPES[proto] !== undefined
            ? D.PROTOCOL_TYPES[proto]
            : (proto ? 'Special-VAL (' + proto + ')' : '');

        var vehicleRows = [
            ['Creation date', gt(hd, 'END_TEST')],
            ['Test started', gt(hd, 'START_TEST')],
            ['Vehicle identification number', gt(veh, 'IDENT/VIN')],
            ['Model line', gt(veh, 'DATA/MODELTYPE')],
            ['Order type', gt(veh, 'DATA/ORDERTYPE')],
            ['Mileage', gtu(veh, 'DATA/ODOMETER')],
            ['Operating hours counter', gtu(veh, 'DATA/OPERATINGTIME')],
            ['Transmission', gt(veh, 'DATA/GEARBOXTYPE')],
            ['Engine type', gt(veh, 'DATA/ENGINETYPE')],
            ['Country', gt(veh, 'DATA/COUNTRYCODE')],
            ['Log type', protoDisp],
            ['Vehicle electrical system voltage',
             gtu(veh, 'DATA/ONBOARDVOLTAGE')]
        ];
        var testerRows = [
            ['Dealer number', gt(rh, 'CARDEALER/DEALERNO')],
            ['Tester ID', gt(eq, 'SERIAL_NO')],
            ['Tester version', gt(eq, 'VERSION')],
            ['PT3G version', gt(eq, 'PT2GVERSION')],
            ['Model lines PDX', gt(eq, 'BR_PDX')],
            ['VCI', gt(eq, 'MODEL')],
            ['PDU API version', gt(eq, 'PDU_API')],
            ['Operating system', gt(eq, 'SYSTEM')],
            ['JAVA', gt(eq, 'JAVA')],
            ['User mode', gt(eq, 'MODE')],
            ['Time zone', gt(hd, 'TIMEZONE')]
        ];
        var out = ['<div class="cards">'];
        var groups = [['Vehicle data', vehicleRows], ['Tester', testerRows]];
        for (var g = 0; g < groups.length; g++) {
            out.push('<div class="card"><h2>' + groups[g][0]
                + '</h2><table class="kv">');
            var rows = groups[g][1];
            for (var r = 0; r < rows.length; r++) {
                out.push(kvRow(rows[r][0], rows[r][1]));
            }
            out.push('</table></div>');
        }
        out.push('</div>');
        return out.join('\n');
    }

    function ecuDisplay(sec) {
        var raw = (findtext(sec, 'TITLE') || '').trim();
        var gloss = D.ECU_GLOSS[normDe(raw)];
        if (gloss) {
            return '<span title="' + esc(raw) + '">'
                + esc(gloss.charAt(0).toUpperCase() + gloss.slice(1))
                + '</span>';
        }
        return esc(raw);
    }

    function identValue(sec, label) {
        var meas = findall(sec, "MEAS[@OBJECT='Identifikation']");
        for (var i = 0; i < meas.length; i++) {
            var vals = findall(meas[i], 'VALUE');
            for (var j = 0; j < vals.length; j++) {
                if ((vals[j].attrib.LABEL || null) === label) {
                    return esc(vals[j].text);
                }
            }
        }
        return '';
    }

    function faultValues(sec) {
        var faults = [];
        var meas = findall(sec, "MEAS[@OBJECT='Fehler']");
        for (var i = 0; i < meas.length; i++) {
            var vals = findall(meas[i], 'VALUE');
            for (var j = 0; j < vals.length; j++) {
                faults.push([(vals[j].text || '').trim(),
                             (vals[j].attrib.TEXT || '').trim()]);
            }
        }
        return faults;
    }

    function overviewTable(sections, secIds) {
        var out = ['<table class="grid"><tr>'
            + '<th>Control unit</th><th>Part number</th>'
            + '<th>Serial number</th>'
            + '<th>DSN</th><th>Software</th><th>Hardware</th>'
            + '<th>Fault codes</th></tr>'];
        for (var i = 0; i < sections.length; i++) {
            var sec = sections[i], sid = secIds[i];
            var title = ecuDisplay(sec);
            var sw = identValue(sec, 'PIF') || identValue(sec, 'ZIF');
            var faults = faultValues(sec);
            var fparts = [];
            for (var f = 0; f < faults.length; f++) {
                fparts.push('<span title="'
                    + esc(translateLabel(faults[f][1])) + '">'
                    + esc(faults[f][0]) + '</span>');
            }
            var fcell = fparts.length ? fparts.join(', ') : '&mdash;';
            var cls = faults.length ? ' class="frow hasfault"'
                                    : ' class="frow"';
            out.push('<tr' + cls + '><td><a href="#' + sid + '">' + title
                + '</a></td>'
                + '<td>' + identValue(sec, 'SGIDK2') + '</td><td>'
                + identValue(sec, 'SERNR') + '</td><td>'
                + identValue(sec, 'SGIDK1') + '</td><td>' + sw
                + '</td><td>' + identValue(sec, 'BRIF') + '</td>'
                + '<td>' + fcell + '</td></tr>');
        }
        out.push('</table>');
        return out.join('\n');
    }

    function renderMeasValues(meas) {
        var rows = [];
        var vals = findall(meas, 'VALUE');
        for (var i = 0; i < vals.length; i++) {
            rows.push('<tr class="frow"><td class="lbl">'
                + labelHtml(vals[i].attrib.TEXT || null)
                + '</td><td class="val">' + valueCell(vals[i])
                + '</td></tr>');
        }
        if (!rows.length) return '';
        return '<table class="vals">' + rows.join('') + '</table>';
    }

    function measHeading(meas) {
        var obj = meas.attrib.OBJECT || '';
        var title = D.MEAS_OBJECT_TITLES[obj];
        if (title === undefined) {
            title = esc(findtext(meas, 'TITLE')) || esc(obj);
        }
        var native = esc(findtext(meas, 'TITLE'));
        if (native && native.toLowerCase() !== title.toLowerCase()) {
            return title
                + " <span style='color:#999;font-weight:normal'>("
                + native + ')</span>';
        }
        return title;
    }

    function renderFaultMeas(meas) {
        var out = [];
        var nested = findall(meas, 'MEAS');
        var vals = findall(meas, 'VALUE');
        for (var i = 0; i < vals.length; i++) {
            out.push('<div class="faultitem">');
            out.push('<span class="fcode">' + esc(vals[i].text) + '</span>'
                + labelHtml(vals[i].attrib.TEXT || null));
            for (var s = 0; s < nested.length; s++) {
                var body = renderMeasValues(nested[s]);
                if (body) {
                    out.push('<div class="subtable"><h3 class="meas">'
                        + measHeading(nested[s]) + '</h3>' + body
                        + '</div>');
                }
            }
            out.push('</div>');
        }
        if (!vals.length) {
            for (var s2 = 0; s2 < nested.length; s2++) {
                var body2 = renderMeasValues(nested[s2]);
                if (body2) {
                    out.push('<div class="subtable"><h3 class="meas">'
                        + measHeading(nested[s2]) + '</h3>' + body2
                        + '</div>');
                }
            }
        }
        return out.join('\n');
    }

    function codingTable(sections, secIds) {
        var out = ['<h2 class="sect" id="coding">Coding overview</h2>'];
        var haveAny = false;
        for (var i = 0; i < sections.length; i++) {
            var sec = sections[i], sid = secIds[i];
            var rows = [];
            var meas = findall(sec, "MEAS[@OBJECT='Codierung']");
            for (var m = 0; m < meas.length; m++) {
                var vals = findall(meas[m], 'VALUE');
                for (var v = 0; v < vals.length; v++) {
                    rows.push('<tr class="frow"><td class="lbl">'
                        + labelHtml(vals[v].attrib.TEXT || null)
                        + '</td><td class="val">' + valueCell(vals[v])
                        + '</td></tr>');
                }
            }
            if (!rows.length) continue;
            haveAny = true;
            out.push('<details class="ecu" id="cod-' + i + '">'
                + '<summary>' + ecuDisplay(sec)
                + ' <span class="badge count">' + rows.length
                + ' codings</span>'
                + '<a style="margin-left:auto;font-weight:normal;'
                + 'font-size:12px" href="#' + sid
                + '">control unit details</a></summary>'
                + '<div class="body"><table class="vals">' + rows.join('')
                + '</table></div></details>');
        }
        if (!haveAny) return '';
        return out.join('\n');
    }

    function faultsSection(sections, secIds, nfaults) {
        var items = [];
        for (var i = 0; i < sections.length; i++) {
            var sec = sections[i], sid = secIds[i];
            var fmeas = [];
            var all = findall(sec, "MEAS[@OBJECT='Fehler']");
            for (var m = 0; m < all.length; m++) {
                if (findall(all[m], 'VALUE').length
                        || findall(all[m], 'MEAS').length) {
                    fmeas.push(all[m]);
                }
            }
            if (!fmeas.length) continue;
            var body = fmeas.map(renderFaultMeas).join('');
            items.push('<div class="fltecu" id="flt-' + sid + '">'
                + '<h3 class="meas"><a href="#' + sid + '">'
                + ecuDisplay(sec) + '</a></h3>' + body + '</div>');
        }
        if (!items.length) return '';
        return '<details class="ecu" id="faults" open>'
            + '<summary>Faults '
            + '<span class="badge fault">' + nfaults + ' fault'
            + (nfaults === 1 ? '' : 's') + '</span></summary>'
            + '<div class="body">' + items.join('') + '</div></details>';
    }

    function ecuSection(sec, sid) {
        var raw = (findtext(sec, 'TITLE') || '').trim();
        var gloss = D.ECU_GLOSS[normDe(raw)];
        var title;
        if (gloss) {
            title = esc(gloss.charAt(0).toUpperCase() + gloss.slice(1))
                + ' <span class="gloss">' + esc(raw) + '</span>';
        } else {
            title = esc(raw);
        }
        var faults = faultValues(sec);
        var nvals = findall(sec, './/VALUE').length;
        var badge = '';
        if (faults.length) {
            badge = '<a class="fl" href="#flt-' + sid
                + '" title="show only the fault entries">'
                + '<span class="badge fault">' + faults.length + ' fault'
                + (faults.length === 1 ? '' : 's') + '</span></a>';
        }
        var openAttr = faults.length ? ' open' : '';
        var out = ['<details class="ecu" id="' + sid + '"' + openAttr + '>',
            '<summary>' + title + ' ' + badge
            + '<span class="badge count">' + nvals + ' values</span>'
            + '<a style="margin-left:auto;font-weight:normal;font-size:12px"'
            + ' href="#overview">back to top</a></summary>',
            '<div class="body">'];
        var meas = findall(sec, 'MEAS');
        for (var m = 0; m < meas.length; m++) {
            var obj = meas[m].attrib.OBJECT || '';
            var body;
            if (obj === 'Fehler') {
                body = renderFaultMeas(meas[m]);
            } else {
                body = renderMeasValues(meas[m]);
            }
            if (body) {
                out.push('<h3 class="meas">' + measHeading(meas[m])
                    + '</h3>' + body);
            }
        }
        out.push('</div></details>');
        return out.join('\n');
    }

    function two(n) { return n < 10 ? '0' + n : String(n); }

    PIWIS.render = function (root, sourceDesc) {
        LABEL_CACHE = {};
        VALUE_CACHE = {};
        var res = find(root, 'RESULT');
        var title = res !== null ? esc(findtext(res, 'TITLE'))
                                 : 'Vehicle analysis log';
        var vin = esc(findtext(root, 'RESULTSHEADER/VEHICLE/IDENT/VIN'));
        var created = esc(findtext(root, 'RESULT/HEADER/END_TEST'));
        var sections = res !== null
            ? findall(res, "SECTION[@OBJECT='ECU']") : [];
        var secIds = [];
        for (var i = 0; i < sections.length; i++) secIds.push('ecu-' + i);
        var nfaults = 0;
        for (var f = 0; f < sections.length; f++) {
            nfaults += faultValues(sections[f]).length;
        }

        var parts = [];
        parts.push('<!DOCTYPE html>');
        parts.push('<html lang="en"><head><meta charset="utf-8">');
        parts.push('<title>' + title + ' - ' + vin + '</title>');
        parts.push('<style>' + D.CSS + '</style>');
        parts.push('<script>' + D.JS + '<\/script>');
        parts.push('</head><body>');
        var nav = '<a href="#overview">Overview</a>'
            + (nfaults ? '<a href="#faults">Faults</a>' : '')
            + '<a href="#coding">Coding</a>'
            + '<a href="#ecus">Control units</a>'
            + '<a href="#" title="filter for Nmax over-rev ranges '
            + '(supported vehicles only)" '
            + 'onclick="return presetFilter(\'Nmax\')">Overrevs</a>';
        parts.push('<div class="topbar"><h1>' + title + '</h1>'
            + '<span class="vin">' + vin + ' &middot; ' + created
            + '</span>'
            + '<div class="navlinks">' + nav + '</div>'
            + '<input id="filterbox" type="search" '
            + 'placeholder="Filter rows (label, value, fault code)..." '
            + 'oninput="onFilterInput()">'
            + '<span id="filtercount"></span>'
            + '<button onclick="setAll(true)">Expand all</button>'
            + '<button onclick="setAll(false)">Collapse all</button></div>');
        parts.push('<div class="wrap">');
        parts.push(headerCards(root));
        parts.push('<h2 class="sect" id="overview">Overview</h2>');
        if (nfaults) {
            parts.push(faultsSection(sections, secIds, nfaults));
        }
        parts.push(overviewTable(sections, secIds));
        var coding = codingTable(sections, secIds);
        if (coding) parts.push(coding);
        parts.push('<h2 class="sect" id="ecus">Control units '
            + '<span class="badge count">' + sections.length
            + ' ECUs</span></h2>');
        for (var s = 0; s < sections.length; s++) {
            parts.push(ecuSection(sections[s], secIds[s]));
        }
        var now = new Date();
        var stamp = now.getFullYear() + '-' + two(now.getMonth() + 1)
            + '-' + two(now.getDate()) + ' ' + two(now.getHours())
            + ':' + two(now.getMinutes()) + ':' + two(now.getSeconds());
        parts.push('<div class="footer">Generated by ' + D.APP_NAME
            + ' from ' + esc(sourceDesc) + ' on ' + stamp
            + '<br>German labels and values are translated to English '
            + 'where known; hover a translated item to see the '
            + 'original text.</div>');
        parts.push('</div></body></html>');
        return parts.join('\n');
    };

    // mirror of Python's ascii + xmlcharrefreplace output encoding
    PIWIS.toAscii = function (s) {
        var out = [];
        for (var i = 0; i < s.length; i++) {
            var cp = s.codePointAt(i);
            if (cp > 0xFFFF) i++;  // surrogate pair consumed
            if (cp < 128) out.push(String.fromCodePoint(cp));
            else out.push('&#' + cp + ';');
        }
        return out.join('');
    };

    PIWIS.init = function (data) {
        D = data;
        TRANSLIT = {};
        for (var k in D.TRANSLIT) TRANSLIT[Number(k)] = D.TRANSLIT[k];
        PHRASE_RE = buildPhraseRe();
        STEMS = buildStems();
        LABEL_CACHE = {};
        VALUE_CACHE = {};
    };

    PIWIS.translateLabel = function (t) { return translateLabel(t); };
    PIWIS.translateValue = function (t) { return translateValue(t); };
    PIWIS.normDe = function (t) { return normDe(t); };

    global.PIWIS = PIWIS;
})(typeof globalThis !== 'undefined' ? globalThis : this);

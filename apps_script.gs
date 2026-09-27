// Job Watcher webhook.
// Setup: open your tracker sheet > Extensions > Apps Script, paste this file, set TOKEN to a
// long random string (e.g. `python3 -c "import secrets;print(secrets.token_urlsafe(24))"`),
// then Deploy > New deployment > Web app (Execute as: Me, Who has access: Anyone).
const TOKEN = 'CHANGE_ME';
const TAB = 'Main';        // tab the postings are appended to
const SEEN_TAB = '_seen';  // hidden tab holding already-shown posting keys
const SEEN_DAYS = 120;     // forget keys older than this

// Expected columns in TAB: Role | Company | Location | Term | Application platform | Date applied | Status | Notes

function ss_() { return SpreadsheetApp.getActiveSpreadsheet(); }
function out_(obj) { return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON); }

// Last row whose Role (col A) or Company (col B) is filled; ignores rows that only carry dropdowns/formatting.
function lastDataRow_(sh) {
  const vals = sh.getRange(1, 1, sh.getMaxRows(), 2).getValues();
  for (let i = vals.length - 1; i >= 0; i--) if (String(vals[i][0]).trim() || String(vals[i][1]).trim()) return i + 1;
  return 1;
}

function seenTab_() {
  let sh = ss_().getSheetByName(SEEN_TAB);
  if (!sh) {
    sh = ss_().insertSheet(SEEN_TAB);
    sh.getRange(1, 1, 1, 2).setValues([['key', 'first_seen']]);
    sh.hideSheet();
  }
  return sh;
}

function readSeen_() {
  const sh = seenTab_();
  const n = sh.getLastRow();
  const seen = {};
  if (n > 1) sh.getRange(2, 1, n - 1, 2).getDisplayValues().forEach(r => { if (r[0]) seen[r[0]] = r[1]; });
  return seen;
}

function writeSeen_(keys, today) {
  const seen = readSeen_();
  keys.forEach(k => { if (k && !seen[k]) seen[k] = today; });
  const horizon = new Date(Date.now() - SEEN_DAYS * 864e5).toISOString().slice(0, 10);
  const rows = Object.keys(seen).filter(k => seen[k] >= horizon).map(k => [k, seen[k]]);
  const sh = seenTab_();
  if (sh.getLastRow() > 1) sh.getRange(2, 1, sh.getLastRow() - 1, 2).clearContent();
  if (rows.length) {
    sh.getRange(2, 2, rows.length, 1).setNumberFormat('@');
    sh.getRange(2, 1, rows.length, 2).setValues(rows);
  }
  return rows.length;
}

// GET ?token=... -> existing rows + seen keys (for dedupe)
function doGet(e) {
  if (!e || !e.parameter || e.parameter.token !== TOKEN) return out_({ ok: false, error: 'bad token' });
  const sh = ss_().getSheetByName(TAB);
  const vals = sh.getRange(1, 1, lastDataRow_(sh), 8).getValues();
  const rows = vals.filter(r => String(r[0]).trim() !== 'Role').map(r => ({
    role: r[0], company: r[1], location: r[2], term: r[3], url: r[4], status: r[6],
  })).filter(r => r.role || r.company);
  return out_({ ok: true, rows, seen: readSeen_() });
}

// POST {token, rows:[{role,company,location,term,platform,date_applied,status,notes}], seen:[keys], today:'YYYY-MM-DD'}
function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const body = JSON.parse(e.postData.contents);
    if (body.token !== TOKEN) return out_({ ok: false, error: 'bad token' });
    const rows = (body.rows || []).map(r => [r.role, r.company, r.location, r.term, r.platform, r.date_applied || '', r.status || 'Apply later', r.notes || '']);
    let start = null;
    if (rows.length) {
      const sh = ss_().getSheetByName(TAB);
      start = lastDataRow_(sh) + 1;
      sh.getRange(start, 1, rows.length, 8).setValues(rows);
    }
    const seenCount = writeSeen_(body.seen || [], body.today || new Date().toISOString().slice(0, 10));
    return out_({ ok: true, appended: rows.length, firstRow: start, seenCount });
  } catch (err) {
    return out_({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

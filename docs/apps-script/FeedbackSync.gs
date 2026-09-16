/**
 * BDE & Sales Portal — Google Form feedback sync
 * =============================================
 *
 * Sends every form response to the portal, signed, so the portal can match it
 * back to the request that caused it.
 *
 * This file lives outside the portal repository once installed: it runs on
 * Google's servers, as the form owner. That is the whole reason this design
 * needs no Google credentials on the portal side — no service account, no
 * OAuth client, no API key, no Cloud project.
 *
 * INSTALL — see docs/apps-script/README.md for the full walkthrough.
 *
 *   1. Open the FORM (not the sheet) → ⋮ → Script editor
 *   2. Paste this file
 *   3. Project Settings → Script Properties, add:
 *          PORTAL_URL      https://your-portal/api/feedback/sync/webhook
 *          PORTAL_SECRET   the same value as GOOGLE_SYNC_SECRET in backend/.env
 *   4. Triggers → Add trigger → onFormSubmit → From form → On form submit
 *   5. Run `testConnection` once and check the log
 *
 * WHY A FORM TRIGGER, NOT A SHEET TRIGGER
 * A form-submit trigger gives `e.response.getId()` — a stable id Google
 * assigns to that response. A sheet trigger only knows a row number, and row
 * numbers shift the moment anybody sorts or deletes. That id is what makes
 * redelivery safe: the portal treats a repeat as a no-op rather than as a
 * second piece of feedback.
 */

var PROPS = PropertiesService.getScriptProperties();
var QUEUE_KEY = 'PORTAL_RETRY_QUEUE';
var MAX_QUEUE = 200;

/* ------------------------------------------------------------ triggers */

/**
 * Installed trigger: fires once per submitted response.
 */
function onFormSubmit(e) {
  if (!e || !e.response) {
    Logger.log('No response on the event — is this trigger bound to the FORM?');
    return;
  }
  var payload = buildPayload(e.response);
  var result = deliver(payload);
  if (!result.ok) {
    enqueue(payload);
    Logger.log('Delivery failed (' + result.status + '); queued for retry.');
  }
}

/**
 * Time-driven trigger (optional, hourly): retries anything that failed.
 * Also add this under Triggers if the portal is ever briefly unreachable.
 */
function retryQueued() {
  var queue = readQueue();
  if (!queue.length) return;

  var remaining = [];
  for (var i = 0; i < queue.length; i++) {
    var result = deliver(queue[i]);
    if (!result.ok) remaining.push(queue[i]);
  }
  writeQueue(remaining);
  Logger.log('Retried ' + queue.length + ', still pending ' + remaining.length);
}

/**
 * Web App entry point for the portal's manual [Sync now].
 *
 * Deploy: Deploy → New deployment → Web app → Execute as ME → Anyone with
 * the link. The link is not the security boundary; the signature is, and it
 * is checked below exactly as the portal checks the webhook's.
 */
function doPost(e) {
  var body = (e && e.postData && e.postData.contents) || '';
  var signature = (e && e.parameter && e.parameter.signature) || '';

  if (!verifyInbound(body, signature)) {
    return json({ error: 'unauthorized' });
  }
  return json({ responses: readQueue() });
}

/* ------------------------------------------------------------- payload */

function buildPayload(formResponse) {
  var answers = {};

  // Google's own timestamp, in the sheet's column-one format.
  answers['Timestamp'] = Utilities.formatDate(
    formResponse.getTimestamp(),
    Session.getScriptTimeZone(),
    'yyyy-MM-dd HH:mm:ss'
  );

  var items = formResponse.getItemResponses();
  for (var i = 0; i < items.length; i++) {
    var title = items[i].getItem().getTitle();
    var value = items[i].getResponse();
    // Checkbox and grid questions answer with arrays.
    answers[title] = Array.isArray(value) ? value.join(', ') : String(value);
  }

  return {
    response_id: formResponse.getId(),
    submitted_at: formResponse.getTimestamp().toISOString(),
    answers: answers
  };
}

/* ------------------------------------------------------------ delivery */

function deliver(payload) {
  var url = PROPS.getProperty('PORTAL_URL');
  var secret = PROPS.getProperty('PORTAL_SECRET');

  if (!url || !secret) {
    Logger.log('PORTAL_URL or PORTAL_SECRET is not set in Script Properties.');
    return { ok: false, status: 0 };
  }

  var body = JSON.stringify(payload);
  var response;
  try {
    response = UrlFetchApp.fetch(url, {
      method: 'post',
      contentType: 'application/json',
      payload: body,
      headers: { 'X-Portal-Signature': signature(body, secret) },
      muteHttpExceptions: true,
      followRedirects: true
    });
  } catch (err) {
    // Network-level failure. The payload goes on the retry queue rather than
    // being lost — a customer's feedback is not something to drop.
    Logger.log('Fetch threw: ' + err);
    return { ok: false, status: 0 };
  }

  var status = response.getResponseCode();

  // 2xx is done. So is 4xx other than 429: the portal rejected the payload
  // and will keep rejecting it, so retrying forever helps nobody. Those are
  // logged and left for the manual sync to surface.
  if (status >= 200 && status < 300) return { ok: true, status: status };
  if (status >= 400 && status < 500 && status !== 429) {
    Logger.log('Portal refused response ' + payload.response_id + ': ' + status);
    return { ok: true, status: status };
  }
  return { ok: false, status: status };
}

/* ------------------------------------------------------------ signature */

/**
 * `t=<unix>,v1=<hex>` over "<t>.<body>", HMAC-SHA256.
 *
 * The timestamp is inside the signed material on purpose: it is what lets
 * the portal refuse a captured request after a few minutes.
 */
function signature(body, secret) {
  var stamp = Math.floor(Date.now() / 1000);
  var bytes = Utilities.computeHmacSha256Signature(stamp + '.' + body, secret);
  return 't=' + stamp + ',v1=' + toHex(bytes);
}

function verifyInbound(body, header) {
  var secret = PROPS.getProperty('PORTAL_SECRET');
  if (!secret || !header) return false;

  var parts = {};
  header.split(',').forEach(function (piece) {
    var bits = piece.split('=');
    if (bits.length === 2) parts[bits[0]] = bits[1];
  });
  if (!parts.t || !parts.v1) return false;

  var skew = Math.abs(Math.floor(Date.now() / 1000) - parseInt(parts.t, 10));
  if (isNaN(skew) || skew > 300) return false;

  var expected = toHex(
    Utilities.computeHmacSha256Signature(parts.t + '.' + body, secret)
  );
  return constantTimeEquals(expected, parts.v1);
}

function toHex(bytes) {
  var out = '';
  for (var i = 0; i < bytes.length; i++) {
    var value = bytes[i] < 0 ? bytes[i] + 256 : bytes[i];
    var hex = value.toString(16);
    out += hex.length === 1 ? '0' + hex : hex;
  }
  return out;
}

function constantTimeEquals(a, b) {
  if (a.length !== b.length) return false;
  var diff = 0;
  for (var i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

/* ---------------------------------------------------------- retry queue */

function readQueue() {
  try {
    return JSON.parse(PROPS.getProperty(QUEUE_KEY) || '[]');
  } catch (err) {
    return [];
  }
}

function writeQueue(queue) {
  PROPS.setProperty(QUEUE_KEY, JSON.stringify(queue.slice(-MAX_QUEUE)));
}

function enqueue(payload) {
  var queue = readQueue();
  for (var i = 0; i < queue.length; i++) {
    if (queue[i].response_id === payload.response_id) return;
  }
  queue.push(payload);
  writeQueue(queue);
}

function json(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(
    ContentService.MimeType.JSON
  );
}

/* ------------------------------------------------------------ manual use */

/**
 * Run this once after installing. It sends a harmless probe and logs what
 * the portal said. A 401 means the secrets do not match; a 422 means the
 * portal is reachable and rejected the shape, which is also a pass for
 * connectivity.
 */
function testConnection() {
  var result = deliver({
    response_id: 'connection-test-' + Date.now(),
    submitted_at: new Date().toISOString(),
    answers: { Timestamp: '', 'Reference code': '' }
  });
  Logger.log('Portal replied: ' + JSON.stringify(result));
  Logger.log(
    result.status === 401
      ? 'PORTAL_SECRET does not match GOOGLE_SYNC_SECRET in backend/.env.'
      : result.status === 0
        ? 'Could not reach PORTAL_URL. Is it public HTTPS?'
        : 'Reachable. A 422 here is expected — the probe carries no ratings.'
  );
}

/**
 * Re-send every response already in the sheet. Safe: the portal skips
 * anything it has seen, by response id. Use after a period of downtime.
 */
function resendAll() {
  var form = FormApp.getActiveForm();
  var responses = form.getResponses();
  var sent = 0;
  for (var i = 0; i < responses.length; i++) {
    if (deliver(buildPayload(responses[i])).ok) sent++;
  }
  Logger.log('Sent ' + sent + ' of ' + responses.length + '.');
}

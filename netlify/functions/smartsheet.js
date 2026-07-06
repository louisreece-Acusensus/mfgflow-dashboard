// Netlify serverless function — proxies Smartsheet API calls server-side
// so CORS and corporate firewalls don't block the browser fetch.
//
// Auth: uses the browser-supplied x-smartsheet-key header if present,
// otherwise falls back to the SMARTSHEET_KEY environment variable
// (set in Netlify → Site settings → Environment variables).
// When the server key is used, only the two known dashboard sheets are
// allowed, so the public URL can't be abused as an open Smartsheet proxy.

const SERVER_KEY_ALLOWED_PATHS = [
  'sheets/6805949508439940', // Production Schedule (main)
  'sheets/2372330635349892', // Build Type Hours lookup
];

exports.handler = async (event) => {
  const CORS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'x-smartsheet-key, x-dash-pass',
    'Content-Type': 'application/json',
  };

  // Handle CORS preflight
  if (event.httpMethod === 'OPTIONS') {
    return { statusCode: 204, headers: CORS, body: '' };
  }

  const userKey = event.headers['x-smartsheet-key'];
  const apiKey  = userKey || process.env.SMARTSHEET_KEY;
  const path    = event.queryStringParameters && event.queryStringParameters.path;

  if (!apiKey) {
    return { statusCode: 400, headers: CORS, body: JSON.stringify({ message: 'No API key: send x-smartsheet-key header or set SMARTSHEET_KEY env var' }) };
  }
  if (!path) {
    return { statusCode: 400, headers: CORS, body: JSON.stringify({ message: 'Missing path query parameter' }) };
  }
  // Keyless (shared-key) access requires the site passphrase, if one is set.
  // DASH_PASS unset = gate disabled, so deploys are safe before it's configured.
  if (!userKey && process.env.DASH_PASS) {
    if (event.headers['x-dash-pass'] !== process.env.DASH_PASS) {
      return { statusCode: 401, headers: CORS, body: JSON.stringify({ message: 'Passphrase required' }) };
    }
  }
  // Server key is restricted to the dashboard's own sheets
  if (!userKey && !SERVER_KEY_ALLOWED_PATHS.includes(path)) {
    return { statusCode: 403, headers: CORS, body: JSON.stringify({ message: 'Path not allowed with server key' }) };
  }

  try {
    const upstream = await fetch(`https://api.smartsheet.com/2.0/${path}`, {
      headers: { Authorization: `Bearer ${apiKey}`, Accept: 'application/json' },
    });

    const text = await upstream.text();
    return {
      statusCode: upstream.status,
      headers: CORS,
      body: text,
    };
  } catch (err) {
    return {
      statusCode: 502,
      headers: CORS,
      body: JSON.stringify({ message: `Proxy error: ${err.message}` }),
    };
  }
};

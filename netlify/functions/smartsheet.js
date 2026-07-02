// Netlify serverless function — proxies Smartsheet API calls server-side
// so CORS and corporate firewalls don't block the browser fetch.

exports.handler = async (event) => {
  const CORS = {
      'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Headers': 'x-smartsheet-key',
              'Content-Type': 'application/json',
                };

                  if (event.httpMethod === 'OPTIONS') {
                      return { statusCode: 204, headers: CORS, body: '' };
                        }

                          const apiKey = event.headers['x-smartsheet-key'];
                            const path   = event.queryStringParameters && event.queryStringParameters.path;

                              if (!apiKey) {
                                  return { statusCode: 400, headers: CORS, body: JSON.stringify({ message: 'Missing x-smartsheet-key header' }) };
                                    }
                                      if (!path) {
                                          return { statusCode: 400, headers: CORS, body: JSON.stringify({ message: 'Missing path query parameter' }) };
                                            }

                                              try {
                                                  const upstream = await fetch(`https://api.smartsheet.com/2.0/${path}`, {
                                                        headers: { Authorization: `Bearer ${apiKey}`, Accept: 'application/json' },
                                                            });
                                                                const text = await upstream.text();
                                                                    return { statusCode: upstream.status, headers: CORS, body: text };
                                                                      } catch (err) {
                                                                          return { statusCode: 502, headers: CORS, body: JSON.stringify({ message: `Proxy error: ${err.message}` }) };
                                                                            }
                                                                            };

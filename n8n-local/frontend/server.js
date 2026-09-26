const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

const port = Number(process.env.PORT || 8080);
const webhookUrl = new URL(process.env.N8N_WEBHOOK_URL || 'http://n8n:5678/webhook/audio-upload');
const publicDir = path.join(__dirname, 'public');
const contentTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
};

function headers(type) {
  return {
    'Content-Type': type,
    'Cache-Control': 'no-store',
    'Content-Security-Policy': "default-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    'X-Content-Type-Options': 'nosniff',
  };
}

function serveFile(requestPath, response) {
  const relative = requestPath === '/' ? 'index.html' : requestPath.slice(1);
  const filePath = path.resolve(publicDir, relative);
  if (!filePath.startsWith(`${publicDir}${path.sep}`) || !fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
    response.writeHead(404, headers('text/plain; charset=utf-8'));
    response.end('Not found');
    return;
  }
  response.writeHead(200, headers(contentTypes[path.extname(filePath)] || 'application/octet-stream'));
  fs.createReadStream(filePath).pipe(response);
}

function proxyUpload(request, response) {
  const upstream = http.request({
    hostname: webhookUrl.hostname,
    port: webhookUrl.port || 80,
    path: webhookUrl.pathname,
    method: 'POST',
    headers: {
      'content-type': request.headers['content-type'] || 'application/octet-stream',
      ...(request.headers['content-length'] ? { 'content-length': request.headers['content-length'] } : {}),
    },
  }, (upstreamResponse) => {
    response.writeHead(upstreamResponse.statusCode || 502, {
      'content-type': upstreamResponse.headers['content-type'] || 'application/json; charset=utf-8',
      'cache-control': 'no-store',
      'x-content-type-options': 'nosniff',
    });
    upstreamResponse.pipe(response);
  });
  upstream.on('error', () => {
    if (!response.headersSent) {
      response.writeHead(502, headers('application/json; charset=utf-8'));
    }
    response.end(JSON.stringify({ error: 'n8n upload workflow is unavailable' }));
  });
  request.pipe(upstream);
}

http.createServer((request, response) => {
  const requestUrl = new URL(request.url, `http://${request.headers.host || 'localhost'}`);
  if (request.method === 'POST' && requestUrl.pathname === '/api/upload') {
    proxyUpload(request, response);
    return;
  }
  if (request.method === 'GET' || request.method === 'HEAD') {
    if (request.method === 'HEAD') {
      response.writeHead(200, headers('text/plain; charset=utf-8'));
      response.end();
      return;
    }
    serveFile(requestUrl.pathname, response);
    return;
  }
  response.writeHead(405, headers('text/plain; charset=utf-8'));
  response.end('Method not allowed');
}).listen(port, '0.0.0.0', () => {
  console.log(`MedPark frontend listening on ${port}`);
});

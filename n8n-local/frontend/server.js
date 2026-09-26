const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

const port = Number(process.env.PORT || 8080);
const webhookUrl = new URL(process.env.N8N_WEBHOOK_URL || 'http://n8n:5678/webhook/audio-upload');
const statusUrl = process.env.N8N_STATUS_URL || 'http://n8n:5678/webhook/job-status';
const jobIdPattern = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/;
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

// Job status and the generated minutes both come from the n8n "job-status" workflow.
async function fetchJob(jobId) {
  const upstream = await fetch(`${statusUrl}?id=${encodeURIComponent(jobId)}`);
  if (!upstream.ok) throw new Error(`n8n status workflow answered ${upstream.status}`);
  return upstream.json();
}

async function serveJob(jobId, wantMinutes, response) {
  try {
    const job = await fetchJob(jobId);
    if (wantMinutes) {
      if (!job.mom_html) {
        response.writeHead(404, headers('text/plain; charset=utf-8'));
        response.end('Minutes are not ready');
        return;
      }
      // The minutes are generated HTML with inline styles only: no scripts, no external requests.
      response.writeHead(200, {
        'Content-Type': 'text/html; charset=utf-8',
        'Cache-Control': 'no-store',
        'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'",
        'X-Content-Type-Options': 'nosniff',
      });
      response.end(job.mom_html);
      return;
    }
    const { mom_html: momHtml, ...rest } = job;
    response.writeHead(200, headers('application/json; charset=utf-8'));
    response.end(JSON.stringify({ ...rest, has_minutes: Boolean(momHtml) }));
  } catch (error) {
    response.writeHead(502, headers('application/json; charset=utf-8'));
    response.end(JSON.stringify({ error: `status unavailable: ${error.message}` }));
  }
}

http.createServer((request, response) => {
  const requestUrl = new URL(request.url, `http://${request.headers.host || 'localhost'}`);
  if (request.method === 'POST' && requestUrl.pathname === '/api/upload') {
    proxyUpload(request, response);
    return;
  }
  const jobRoute = requestUrl.pathname.match(/^\/api\/jobs\/([^/]+)(\/minutes)?$/);
  if (request.method === 'GET' && jobRoute) {
    if (!jobIdPattern.test(jobRoute[1])) {
      response.writeHead(400, headers('application/json; charset=utf-8'));
      response.end(JSON.stringify({ error: 'invalid job id' }));
      return;
    }
    serveJob(jobRoute[1], Boolean(jobRoute[2]), response);
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

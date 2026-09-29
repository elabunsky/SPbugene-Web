// SPbugene News — локальный сервер: раздаёт index.html и проксирует запросы (обход CORS).
// Запуск: node server.js  →  http://localhost:8787
const http = require("http");
const https = require("https");
const fs = require("fs");
const path = require("path");

const PORT = Number(process.env.PORT || 8787);
const staticFiles = {
  "/manifest.json": { file: "manifest.json", type: "application/manifest+json; charset=utf-8" },
  "/apple-touch-icon.png": { file: "pwa/touch-180.png", type: "image/png" },
  "/pwa/favicon-32.png": { file: "pwa/favicon-32.png", type: "image/png" },
  "/pwa/icon-192.png": { file: "pwa/icon-192.png", type: "image/png" },
  "/pwa/icon-512.png": { file: "pwa/icon-512.png", type: "image/png" },
  "/pwa/touch-180.png": { file: "pwa/touch-180.png", type: "image/png" },
};

function fetchUrl(url, cb, binary) {
  const mod = url.startsWith("https") ? https : http;
  const req = mod.get(url, { headers: { "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15" } }, res => {
    const chunks = [];
    res.on("data", c => chunks.push(c));
    res.on("end", () => cb(null, Buffer.concat(chunks), res.statusCode, res.headers));
  });
  req.on("error", e => cb(e));
  req.setTimeout(25000, () => req.destroy(new Error("timeout")));
}

const server = http.createServer((req, res) => {
  const u = new URL(req.url, `http://localhost:${PORT}`);

  const asset = staticFiles[u.pathname];
  if (asset) {
    const file = path.join(__dirname, asset.file);
    fs.readFile(file, (err, data) => {
      if (err) { res.writeHead(404); res.end("not found"); return; }
      res.writeHead(200, { "Content-Type": asset.type, "Cache-Control": "public, max-age=3600" });
      res.end(data);
    });
    return;
  }

  if (u.pathname === "/" || u.pathname === "/index.html") {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    fs.createReadStream(path.join(__dirname, "index.html")).pipe(res);
    return;
  }

  // Прокси текстовых источников: /proxy?url=<encoded>
  if (u.pathname === "/proxy") {
    const target = u.searchParams.get("url");
    if (!target || !/^https?:\/\//.test(target)) { res.writeHead(400); res.end("bad url"); return; }
    fetchUrl(target, (err, data, status) => {
      if (err) { res.writeHead(502, { "Access-Control-Allow-Origin": "*" }); res.end(String(err)); return; }
      res.writeHead(status || 200, { "Content-Type": "text/html; charset=utf-8", "Access-Control-Allow-Origin": "*" });
      res.end(data);
    });
    return;
  }

  // Прокси картинок: /img?url=<encoded>
  if (u.pathname === "/img") {
    const target = u.searchParams.get("url");
    if (!target || !/^https?:\/\//.test(target)) { res.writeHead(400); res.end("bad url"); return; }
    fetchUrl(target, (err, data, status, headers) => {
      if (err) { res.writeHead(502, { "Access-Control-Allow-Origin": "*" }); res.end(String(err)); return; }
      res.writeHead(status || 200, {
        "Content-Type": headers["content-type"] || "image/jpeg",
        "Cache-Control": "public, max-age=86400",
        "Access-Control-Allow-Origin": "*",
      });
      res.end(data);
    });
    return;
  }

  // Прокси LLM API: /llm — клиент передаёт apiBase и apiKey в заголовках,
  // чтобы ключ не светился в URL и прокси не знал его.
  if (u.pathname === "/llm" && req.method === "POST") {
    const chunks = [];
    req.on("data", c => chunks.push(c));
    req.on("end", () => {
      const body = Buffer.concat(chunks);
      const apiBase = req.headers["x-api-base"];
      const apiKey = req.headers["x-api-key"];
      if (!apiBase || !apiKey) { res.writeHead(400, { "Access-Control-Allow-Origin": "*" }); res.end("missing headers"); return; }
      const target = apiBase.replace(/\/+$/, "") + "/chat/completions";
      const mod = target.startsWith("https") ? https : http;
      const preq = mod.request(target, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": "Bearer " + apiKey,
          "Content-Length": body.length,
        },
        timeout: 600000,
      }, pres => {
        const pchunks = [];
        pres.on("data", c => pchunks.push(c));
        pres.on("end", () => {
          res.writeHead(pres.statusCode || 200, { "Content-Type": "application/json; charset=utf-8", "Access-Control-Allow-Origin": "*" });
          res.end(Buffer.concat(pchunks));
        });
      });
      preq.on("error", e => { res.writeHead(502, { "Access-Control-Allow-Origin": "*" }); res.end(String(e)); });
      preq.on("timeout", () => preq.destroy(new Error("timeout")));
      preq.end(body);
    });
    return;
  }

  res.writeHead(404);
  res.end("not found");
});

server.listen(PORT, () => console.log(`SPbugene News: http://localhost:${PORT}`));

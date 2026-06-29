"use strict";

const { JSDOM } = require("jsdom");
const https = require("https");
const http = require("http");
const zlib = require("zlib");

const envPatch = require("./env_patch");
const invariants = require("./invariants");
const eventDispatch = require("./event_dispatch");

const ORIGIN_CN = "https://turing.captcha.qcloud.com";
const ORIGIN_GLOBAL = "https://ca.turing.captcha.qcloud.com";
const LANDING_CN = "https://turing.captcha.gtimg.com/1/template/drag_ele.html";
const LANDING_GLOBAL = "https://global.turing.captcha.gtimg.com/1/template/drag_ele.html";
const REFERER_CN = "https://turing.captcha.gtimg.com/";
const REFERER_GLOBAL = "https://global.turing.captcha.gtimg.com/";

const scriptCache = new Map();

function resolveCaptchaEnv(captcha_origin) {
  const isGlobal = String(captcha_origin || "").includes("ca.turing");
  return {
    origin: isGlobal ? ORIGIN_GLOBAL : ORIGIN_CN,
    landing: isGlobal ? LANDING_GLOBAL : LANDING_CN,
    referer: isGlobal ? REFERER_GLOBAL : REFERER_CN,
  };
}

function log(msg) {
  process.stderr.write(`[tdc_run] ${msg}\n`);
}

function fetchScript(url, ua, referer, origin) {
  return new Promise((resolve, reject) => {
    const client = url.startsWith("https") ? https : http;
    const req = client.get(
      url,
      {
        headers: {
          "User-Agent": ua,
          Accept: "*/*",
          "Accept-Encoding": "gzip, deflate",
          "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
          Referer: referer,
          Origin: origin,
        },
      },
      (res) => {
        let stream = res;
        const encoding = res.headers["content-encoding"];
        if (encoding === "gzip") stream = res.pipe(zlib.createGunzip());
        else if (encoding === "deflate") stream = res.pipe(zlib.createInflate());

        let body = "";
        stream.on("data", (chunk) => (body += chunk));
        stream.on("end", () => resolve(body));
        stream.on("error", reject);
      }
    );
    req.on("error", reject);
    req.setTimeout(30000, () => {
      req.destroy(new Error("tdc.js fetch timed out"));
    });
  });
}

function resolveTdcUrl(tdc_url, origin) {
  if (!tdc_url) return null;
  if (tdc_url.startsWith("http")) return tdc_url;
  return origin + (tdc_url.startsWith("/") ? tdc_url : "/" + tdc_url);
}

async function waitForTDC(win, timeoutMs = 5000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (win.TDC) return true;
    await new Promise((r) => setTimeout(r, 50));
  }
  return false;
}

async function getScriptSource(fullUrl, ua, referer, origin, debug) {
  if (scriptCache.has(fullUrl)) {
    if (debug) log(`tdc.js cache hit: ${fullUrl}`);
    return scriptCache.get(fullUrl);
  }
  if (debug) log(`fetching tdc.js: ${fullUrl}`);
  const source = await fetchScript(fullUrl, ua, referer, origin);
  scriptCache.set(fullUrl, source);
  if (debug) log(`tdc.js fetched: ${source.length} bytes`);
  return source;
}

async function runCollect(input) {
  const { tdc_url, ua, trajectory, debug, captcha_origin } = input;

  const env = resolveCaptchaEnv(captcha_origin || tdc_url);
  const fullUrl = resolveTdcUrl(tdc_url, env.origin);
  if (!fullUrl) throw new Error("tdc_url is empty");

  const tdcSource = await getScriptSource(fullUrl, ua, env.referer, env.origin, debug);

  const dom = new JSDOM(`<!DOCTYPE html><html><head></head><body></body></html>`, {
    url: env.landing,
    referrer: env.referer,
    userAgent: ua,
    pretendToBeVisual: true,
    runScripts: "dangerously",
  });
  const win = dom.window;

  try {
    envPatch.apply(win);
    if (debug) log("env_patch applied");

    const script = win.document.createElement("script");
    script.textContent = tdcSource;
    win.document.head.appendChild(script);

    const ready = await waitForTDC(win, 5000);
    if (!ready) {
      const keys = Object.keys(win).filter((k) => /tdc|tencent|chaos/i.test(k));
      throw new Error("TDC global not found. Candidate keys: " + JSON.stringify(keys));
    }
    if (debug) log("TDC global ready");

    invariants.apply(win);
    await eventDispatch.dispatch(win, trajectory);
    if (debug) log(`dispatched trajectory kind=${trajectory.kind} points=${trajectory.points.length}`);

    await new Promise((r) => setTimeout(r, 250));

    let collect = "";
    let info = "";
    let tokenid = "";
    try {
      collect = typeof win.TDC.getData === "function" ? win.TDC.getData(true) : "";
    } catch (e) {
      log(`TDC.getData error: ${e.message || e}`);
    }
    try {
      const raw = typeof win.TDC.getInfo === "function" ? win.TDC.getInfo() : "";
      if (raw && typeof raw === "object") {
        info = raw.info || raw.eks || raw.data || JSON.stringify(raw);
        tokenid = raw.tokenid || "";
      } else {
        info = String(raw || "");
      }
    } catch (e) {
      log(`TDC.getInfo error: ${e.message || e}`);
    }

    return {
      collect: String(collect || ""),
      eks: String(info || ""),
      tokenid: String(tokenid || ""),
      tlg: String(collect || "").length,
    };
  } finally {
    dom.window.close();
  }
}

module.exports = { runCollect };

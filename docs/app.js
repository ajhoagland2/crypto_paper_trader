const form = document.querySelector("#simulation-form");
const chart = document.querySelector("#price-chart");
const ctx = chart.getContext("2d");
const backtestEquityChart = document.querySelector("#backtest-equity-chart");
const backtestPriceChart = document.querySelector("#backtest-price-chart");
const backtestEquityCtx = backtestEquityChart?.getContext("2d");
const backtestPriceCtx = backtestPriceChart?.getContext("2d");
let liveTimer = null;
let simulationTimer = null;
let liveTickInFlight = false;
let lastLiveStatus = null;
let lastLiveSignals = {};
let chartPinnedToLive = true;
const apiTimeoutMs = 15000;
const liveLoopMs = 500;
const chartWindowMs = 5 * 60 * 1000;
let authReady = false;

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

function simpleMovingAverage(values, period) {
  if (values.length < period) {
    return null;
  }
  const window = values.slice(values.length - period);
  return window.reduce((sum, value) => sum + value, 0) / period;
}

function parsePrices(raw) {
  const matches = String(raw).match(/\$?\d+(?:,\d{3})*(?:\.\d+)?|\$?\d*\.\d+/g) || [];
  return matches
    .map((value) => Number(value.replace(/[$,]/g, "")))
    .filter((value) => Number.isFinite(value) && value > 0);
}

function parsePositiveNumber(raw, fallback) {
  const match = String(raw).match(/\$?\d+(?:,\d{3})*(?:\.\d+)?|\$?\d*\.\d+/);
  const value = match ? Number(match[0].replace(/[$,]/g, "")) : Number.NaN;
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function parsePositiveInteger(raw, fallback) {
  return Math.max(1, Math.floor(parsePositiveNumber(raw, fallback)));
}

function percentText(value) {
  return `${Number(value || 0).toFixed(2)}%`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatSessionRange(startTimestamp, endTimestamp) {
  const start = new Date(startTimestamp);
  const end = new Date(endTimestamp);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    return "--";
  }
  const dateText = start.toLocaleDateString([], {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
  const startTime = start.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const endTime = end.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return `${dateText} / ${startTime} - ${endTime}`;
}

function secondsToDuration(seconds) {
  const safe = Math.max(0, Number(seconds || 0));
  const minutes = Math.floor(safe / 60);
  const remaining = Math.floor(safe % 60);
  return `${minutes}m ${remaining}s`;
}

function formatTimeLabel(timestamp) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return "--";
  }
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}

function defaultDateTimeLocal(minutesAgo = 60) {
  const date = new Date(Date.now() - minutesAgo * 60 * 1000);
  date.setSeconds(0, 0);
  return date.toISOString().slice(0, 16);
}

function showError(message) {
  const error = document.querySelector("#form-error");
  error.textContent = message;
  error.classList.add("is-visible");
}

function setAuthMessage(message = "", isError = true) {
  const element = document.querySelector("#auth-message");
  element.textContent = message;
  element.classList.toggle("is-visible", Boolean(message));
  element.style.borderColor = isError ? "rgba(255, 92, 77, 0.6)" : "rgba(47, 209, 128, 0.62)";
  element.style.color = isError ? "#ffd8d4" : "#d8ffe9";
  element.style.background = isError ? "rgba(80, 12, 8, 0.48)" : "rgba(8, 80, 42, 0.34)";
}

function showAuthMode(mode) {
  const signIn = document.querySelector("#sign-in-form");
  const forgot = document.querySelector("#forgot-password-form");
  const reset = document.querySelector("#reset-password-form");
  const back = document.querySelector("#show-sign-in");
  const forgotButton = document.querySelector("#show-forgot-password");
  const resetButton = document.querySelector("#show-reset-password");
  signIn.hidden = mode !== "sign-in";
  forgot.hidden = mode !== "forgot";
  reset.hidden = mode !== "reset";
  back.hidden = mode === "sign-in";
  forgotButton.hidden = mode === "forgot";
  resetButton.hidden = mode === "reset";
  document.querySelector("#auth-copy").textContent = {
    "sign-in": "Use your configured operator email and password to open the trading console.",
    forgot: "Enter your operator email to request a recovery token.",
    reset: "Paste the recovery token and choose a new password.",
  }[mode];
  setAuthMessage("");
}

async function authRequest(path, body = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok || payload.error) {
    throw new Error(payload.error || "Authentication request failed.");
  }
  return payload;
}

function showAuthenticatedApp(email = "") {
  authReady = true;
  document.body.classList.remove("requires-auth");
  document.body.classList.add("authenticated");
  document.querySelector("#auth-shell").hidden = true;
  document.querySelector("#sign-out").hidden = false;
  document.querySelector("#backend-status").textContent = email ? `Signed in: ${email}` : "Signed in";
}

function showAuthScreen(message = "") {
  authReady = false;
  stopLiveLoop("");
  stopSimulationLoop("");
  document.body.classList.add("requires-auth");
  document.body.classList.remove("authenticated");
  document.querySelector("#auth-shell").hidden = false;
  document.querySelector("#sign-out").hidden = true;
  document.querySelector("#backend-status").textContent = "Sign in required";
  showAuthMode("sign-in");
  if (message) {
    setAuthMessage(message);
  }
}

async function initializeAuth() {
  try {
    const response = await fetch("/api/auth/status");
    const status = await response.json();
    if (!status.enabled || status.authenticated) {
      showAuthenticatedApp(status.email || "");
      return true;
    }
    if (!status.configured) {
      showAuthScreen("Set WEB_AUTH_EMAIL and WEB_AUTH_PASSWORD in .env, then restart the web server.");
      return false;
    }
    showAuthScreen();
    return false;
  } catch (error) {
    showAuthScreen("Could not check sign-in status. Start or restart the Python web server.");
    return false;
  }
}

function showLiveStatus(message, isError = false) {
  const status = document.querySelector("#live-status-text");
  status.textContent = message;
  status.classList.toggle("is-error", isError);
  if (isError) {
    document.querySelector("#backend-status").textContent = "Local API needs attention";
  }
}

function isLiveMode(status = lastLiveStatus || {}) {
  return String(status.mode || "").toLowerCase() === "live";
}

function modeText(status = lastLiveStatus || {}) {
  return isLiveMode(status) ? "LIVE" : "PAPER";
}

function tradeNoun(status = lastLiveStatus || {}, plural = true) {
  if (isLiveMode(status)) {
    return plural ? "live orders" : "live order";
  }
  return plural ? "paper trades" : "paper trade";
}

function updateExecutionModeChrome(status = {}) {
  const live = isLiveMode(status);
  const mode = live ? "LIVE ORDERS ARMED" : "PAPER SHADOW MODE";
  const banner = document.querySelector("#execution-banner");
  const bannerStrong = banner?.querySelector("strong");
  const statusStrip = document.querySelector(".status-strip");
  const modeCard = document.querySelector(".execution-mode-card");

  document.body.classList.toggle("is-live-mode", live);
  document.body.classList.toggle("is-paper-mode", !live);
  statusStrip.classList.toggle("is-live", live);
  statusStrip.classList.toggle("is-paper", !live);
  modeCard?.classList.toggle("is-live", live);
  modeCard?.classList.toggle("is-paper", !live);
  banner?.classList.toggle("is-live", live);
  banner?.classList.toggle("is-paper", !live);
  banner?.classList.remove("is-waiting");

  document.querySelector("#backend-status").textContent = mode;
  document.querySelector("#brand-mode-label").textContent = live ? "Live Execution Console" : "Paper Trading Console";
  document.querySelector("#execution-mode").textContent = mode;
  if (bannerStrong) {
    bannerStrong.textContent = live
      ? "Robinhood market orders can be submitted"
      : "No Robinhood orders will be submitted";
  }
  document.querySelector("#execution-panel-title").textContent = live
    ? "Live Robinhood Execution"
    : "Robinhood Paper Shadow";
  document.querySelector("#control-panel-title").textContent = live
    ? "Live Trading Controls"
    : "Paper Trading Controls";
  document.querySelector("#control-panel-copy").textContent = live
    ? "Continuously run the control stack against Robinhood live quote ticks. Approved signals submit Robinhood market orders."
    : "Continuously run the control stack against Robinhood live quote ticks through paper-only risk checks.";
  document.querySelector("#reset-live-session").textContent = live ? "Reset Live Session" : "Reset Paper Session";
  document.querySelector("#stream-eyebrow").textContent = live ? "Live Execution Stream" : "Paper Output";
  document.querySelector("#event-log-copy").textContent = live
    ? "Signals, submitted live orders, mirrored trades, and rejected trades."
    : "Signals, approved paper trades, and rejected trades.";
  document.querySelector("#analytics-copy").textContent = live
    ? "Live execution performance and exposure metrics for the current Robinhood session."
    : "Paper-trading performance and exposure metrics for the current live-data session.";
}

function syncControlsFromStatus(status = {}) {
  const symbols = status.symbols || [];
  const primarySymbol = symbols[0];
  if (primarySymbol) {
    document.querySelector("#symbol").value = primarySymbol;
  }
  if (Number.isFinite(Number(status.startingCash))) {
    document.querySelector("#starting-cash").value = String(Number(status.startingCash));
  }
  if (Number.isFinite(Number(status.riskControls?.maxTradeSize))) {
    document.querySelector("#max-trade-size").value = String(Number(status.riskControls.maxTradeSize));
  }
  if (Number.isFinite(Number(status.momentumConfig?.target_profit_pct))) {
    document.querySelector("#target-profit-percent").value = String(Number(status.momentumConfig.target_profit_pct));
  }
  if (Number.isFinite(Number(status.momentumConfig?.entry_threshold))) {
    document.querySelector("#momentum-entry-threshold").value = String(Number(status.momentumConfig.entry_threshold));
  }
  if (Number.isFinite(Number(status.momentumConfig?.entry_interval_seconds))) {
    document.querySelector("#momentum-entry-interval-seconds").value = String(Number(status.momentumConfig.entry_interval_seconds));
  }
  if (Number.isFinite(Number(status.momentumConfig?.max_open_trades))) {
    document.querySelector("#max-open-trades").value = String(Number(status.momentumConfig.max_open_trades));
  }
  if (Number.isFinite(Number(status.momentumConfig?.exit_threshold))) {
    document.querySelector("#momentum-exit-threshold").value = String(Number(status.momentumConfig.exit_threshold));
  }
  if (Number.isFinite(Number(status.momentumConfig?.catastrophic_loss_pct))) {
    document.querySelector("#catastrophic-loss-percent").value = String(Number(status.momentumConfig.catastrophic_loss_pct));
  }
  if (Number.isFinite(Number(status.riskControls?.cooldownAfterLossSeconds))) {
    document.querySelector("#cooldown-after-loss-seconds").value = String(Number(status.riskControls.cooldownAfterLossSeconds));
  }
  if (typeof status.riskControls?.killSwitch === "boolean") {
    document.querySelector("#kill-switch").checked = status.riskControls.killSwitch;
  }
}

function clearError() {
  const error = document.querySelector("#form-error");
  error.textContent = "";
  error.classList.remove("is-visible");
}

function evaluateRisk(config, state, quantity, price) {
  const notional = quantity * price;
  if (config.killSwitch) {
    return { approved: false, reason: "global kill switch is enabled" };
  }
  if (quantity <= 0) {
    return { approved: false, reason: "trade quantity is too small" };
  }
  if (state.pendingSide === "BUY" && notional > config.maxTradeSize * 1.000001) {
    return { approved: false, reason: "trade size exceeds max trade size" };
  }
  if (state.pendingSide === "BUY" && state.realizedPl <= -Math.abs(config.maxDailyLoss)) {
    return { approved: false, reason: "max daily loss reached" };
  }
  if (state.pendingSide === "BUY" && state.trades.length >= config.maxTrades) {
    return { approved: false, reason: "max trades per day reached" };
  }
  return { approved: true, reason: "" };
}

function recentWindow(prices, index, lookback) {
  const start = Math.max(0, index - lookback + 1);
  return prices.slice(start, index + 1);
}

function percentMove(from, to) {
  return ((to - from) / from) * 100;
}

function getLiveStyleSignal(config, state, index) {
  const price = config.prices[index];
  const previous = config.prices[index - 1];
  const priorWindow = recentWindow(config.prices, Math.max(0, index - 1), config.lookbackWindow);
  const currentWindow = recentWindow(config.prices, index, config.lookbackWindow);
  const recentLow = Math.min(...priorWindow);
  const recentHigh = Math.max(...priorWindow);
  const currentHigh = Math.max(...currentWindow);

  if (state.position <= 0) {
    const hadTradableDip = recentHigh > 0 && percentMove(recentHigh, recentLow) <= -config.buyDipPercent;
    const reboundedFromLow = recentLow > 0 && percentMove(recentLow, price) >= config.reboundPercent;
    const momentumConfirmed = Number.isFinite(previous) && price >= previous;
    const priorTickWasLow = Number.isFinite(previous) && previous <= recentLow * 1.002;
    const flatBaseBreakout = priorTickWasLow && reboundedFromLow && momentumConfirmed;

    if ((hadTradableDip && reboundedFromLow && momentumConfirmed) || flatBaseBreakout) {
      return {
        signal: "BUY",
        reason: `live-style entry: rebound ${percentMove(recentLow, price).toFixed(2)}% from recent low`,
      };
    }
    return { signal: "HOLD", reason: "waiting for a confirmed rebound from recent lows" };
  }

  state.highestSinceEntry = Math.max(state.highestSinceEntry, price);
  const gain = percentMove(state.averagePrice, price);
  const drawdownFromPeak = percentMove(state.highestSinceEntry, price);
  const momentumRolledOver = Number.isFinite(previous) && price < previous;
  const extendedNearHigh = price >= currentHigh * 0.998;

  if (gain <= -config.stopLossPercent) {
    return { signal: "SELL", reason: `stop loss: ${gain.toFixed(2)}% from entry` };
  }
  if (gain >= config.profitTargetPercent) {
    return { signal: "SELL", reason: `profit target: ${gain.toFixed(2)}% from entry` };
  }
  if (gain > 0 && drawdownFromPeak <= -config.trailingStopPercent) {
    return { signal: "SELL", reason: `trailing stop: ${drawdownFromPeak.toFixed(2)}% from peak` };
  }
  if (gain > 0 && extendedNearHigh && momentumRolledOver) {
    return { signal: "SELL", reason: `momentum rollover near recent high: ${gain.toFixed(2)}% from entry` };
  }
  if (index === config.prices.length - 1 && gain !== 0) {
    return { signal: "SELL", reason: `end of paper session exit: ${gain.toFixed(2)}% from entry` };
  }
  return { signal: "HOLD", reason: `holding position, unrealized ${gain.toFixed(2)}%` };
}

function runPaperSimulation(config) {
  const state = {
    cash: config.startingCash,
    position: 0,
    averagePrice: 0,
    realizedPl: 0,
    gainReserve: 0,
    trades: [],
    events: [],
    highestSinceEntry: 0,
    pendingSide: "",
  };

  config.prices.forEach((price, index) => {
    const signalResult = getLiveStyleSignal(config, state, index);
    const signal = signalResult.signal;
    const event = {
      step: index + 1,
      type: "signal",
      signal,
      price,
      details: signalResult.reason,
    };

    if (signal === "BUY") {
      const availableCash = Math.max(0, state.cash - state.gainReserve);
      const tradeBudget = Math.min(config.maxTradeSize, availableCash);
      const quantity = tradeBudget / price;
      state.pendingSide = "BUY";
      const decision = evaluateRisk(config, state, quantity, price);
      if (decision.approved && availableCash >= price) {
        const oldCost = state.position * state.averagePrice;
        state.position += quantity;
        state.averagePrice = (oldCost + quantity * price) / state.position;
        state.highestSinceEntry = price;
        state.cash -= quantity * price;
        state.trades.push({ side: "BUY", quantity, price, realizedPl: 0 });
        event.type = "trade";
        event.details = `paper buy ${quantity.toFixed(4)} units after live-style rebound confirmation`;
      } else {
        event.type = "rejected";
        event.details = decision.reason || "insufficient available paper cash";
      }
    }

    if (signal === "SELL" && state.position > 0) {
      const quantity = state.position;
      state.pendingSide = "SELL";
      const decision = evaluateRisk(config, state, quantity, price);
      if (decision.approved) {
        const realized = (price - state.averagePrice) * quantity;
        const reservedGain = realized > 0 ? realized * (config.gainReservePercent / 100) : 0;
        state.position = 0;
        state.cash += quantity * price;
        state.realizedPl += realized;
        state.gainReserve += reservedGain;
        state.trades.push({ side: "SELL", quantity, price, realizedPl: realized, reservedGain });
        event.type = "trade";
        event.details = `${signalResult.reason}, realized ${money.format(realized)}, reserved ${money.format(reservedGain)}`;
      } else {
        event.type = "rejected";
        event.details = decision.reason;
      }
    }

    state.pendingSide = "";

    state.events.push(event);
  });

  const latestPrice = config.prices[config.prices.length - 1] || 0;
  state.portfolioValue = state.cash + state.position * latestPrice;
  state.unrealizedPl = state.position * (latestPrice - state.averagePrice);
  state.availableCash = state.cash - state.gainReserve;
  return state;
}

function drawChart(prices, events) {
  const width = chart.width;
  const height = chart.height;
  const pad = 34;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#070707";
  ctx.fillRect(0, 0, width, height);

  if (prices.length === 0) {
    return;
  }

  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const range = max - min || 1;

  ctx.strokeStyle = "rgba(255, 255, 255, 0.08)";
  ctx.lineWidth = 1;
  for (let i = 0; i < 5; i += 1) {
    const y = pad + (i * (height - pad * 2)) / 4;
    ctx.beginPath();
    ctx.moveTo(pad, y);
    ctx.lineTo(width - pad, y);
    ctx.stroke();
  }

  const pointFor = (price, index) => {
    const x = pad + (index * (width - pad * 2)) / Math.max(prices.length - 1, 1);
    const y = height - pad - ((price - min) / range) * (height - pad * 2);
    return { x, y };
  };

  ctx.strokeStyle = "#ff7b00";
  ctx.lineWidth = 3;
  ctx.shadowColor = "rgba(255, 123, 0, 0.45)";
  ctx.shadowBlur = 12;
  ctx.beginPath();
  prices.forEach((price, index) => {
    const point = pointFor(price, index);
    if (index === 0) {
      ctx.moveTo(point.x, point.y);
    } else {
      ctx.lineTo(point.x, point.y);
    }
  });
  ctx.stroke();
  ctx.shadowBlur = 0;

  events.forEach((event, index) => {
    if (event.type !== "trade" && event.type !== "rejected") {
      return;
    }
    const eventIndex = Number.isFinite(event.step) ? Math.max(0, event.step - 1) : index;
    const point = pointFor(event.price, eventIndex);
    ctx.beginPath();
    ctx.arc(point.x, point.y, event.type === "rejected" ? 7 : 8, 0, Math.PI * 2);
    ctx.fillStyle = event.type === "rejected" ? "#d7a7ff" : event.signal === "BUY" ? "#2fd180" : "#ff5c4d";
    ctx.fill();
    ctx.strokeStyle = "#050505";
    ctx.lineWidth = 2;
    ctx.stroke();
  });

  ctx.fillStyle = "#a0a0a0";
  ctx.font = "13px Inter, system-ui";
  ctx.fillText(`Low ${money.format(min)}`, pad, height - 10);
  ctx.fillText(`High ${money.format(max)}`, pad, 20);
}

function drawLiveCandlestickChart(candles, events, overlay = {}) {
  const width = chart.width;
  const height = chart.height;
  const pad = 38;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#070707";
  ctx.fillRect(0, 0, width, height);

  if (!candles.length) {
    return;
  }

  const openTrades = Array.isArray(overlay.openTrades)
    ? overlay.openTrades
    : overlay.openTrade
      ? [overlay.openTrade]
      : [];
  const sortedOpenTrades = openTrades
    .slice()
    .sort((a, b) => Number(a.target_exit_price) - Number(b.target_exit_price));
  const primaryTrade = sortedOpenTrades[0];
  const targetExitPrice = Number(primaryTrade?.target_exit_price);
  const entryPrice = Number(primaryTrade?.entry_price);
  const hasOpenTarget = Number.isFinite(targetExitPrice) && targetExitPrice > 0
    && Number.isFinite(entryPrice) && entryPrice > 0;
  const values = candles.flatMap((candle) => [candle.open, candle.high, candle.low, candle.close]);
  const marketMin = Math.min(...values);
  const marketMax = Math.max(...values);
  const marketRange = marketMax - marketMin || Math.max(marketMax * 0.001, 0.000001);
  const targetFitsMarketScale = hasOpenTarget
    && targetExitPrice <= marketMax + marketRange * 0.5
    && targetExitPrice >= marketMin - marketRange * 0.5;
  if (hasOpenTarget) {
    values.push(entryPrice);
  }
  if (targetFitsMarketScale) {
    values.push(targetExitPrice);
  }
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const rawRange = rawMax - rawMin || Math.max(rawMax * 0.001, 1);
  const min = rawMin - rawRange * 0.08;
  const max = rawMax + rawRange * 0.08;
  const range = max - min;
  const plotWidth = width - pad * 2;
  const plotHeight = height - pad * 2;
  const slot = plotWidth / Math.max(candles.length, 1);
  const bodyWidth = Math.max(5, Math.min(18, slot * 0.58));

  const yFor = (price) => height - pad - ((price - min) / range) * plotHeight;
  const xFor = (index) => pad + slot * index + slot / 2;

  const intervalSeconds = overlay.momentumConfig?.interval_seconds || 30;
  const firstTime = Date.parse(candles[0].timestamp);

  ctx.strokeStyle = "rgba(255, 255, 255, 0.08)";
  ctx.lineWidth = 1;
  for (let i = 0; i < 5; i += 1) {
    const y = pad + (i * plotHeight) / 4;
    ctx.beginPath();
    ctx.moveTo(pad, y);
    ctx.lineTo(width - pad, y);
    ctx.stroke();
  }

  if (hasOpenTarget) {
    const entryY = yFor(entryPrice);
    const targetY = targetFitsMarketScale ? yFor(targetExitPrice) : pad + 2;
    const zoneTop = Math.min(entryY, targetY);
    const zoneHeight = Math.abs(entryY - targetY);
    const targetPct = ((targetExitPrice - entryPrice) / entryPrice) * 100;
    const latestPrice = Number(candles[candles.length - 1]?.close);
    const remainingPct = Number.isFinite(latestPrice) && latestPrice > 0
      ? ((targetExitPrice - latestPrice) / latestPrice) * 100
      : targetPct;

    ctx.fillStyle = targetFitsMarketScale
      ? "rgba(215, 167, 255, 0.08)"
      : "rgba(215, 167, 255, 0.035)";
    ctx.fillRect(pad, zoneTop, plotWidth, zoneHeight);

    ctx.strokeStyle = "rgba(47, 209, 128, 0.9)";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 5]);
    ctx.beginPath();
    ctx.moveTo(pad, entryY);
    ctx.lineTo(width - pad, entryY);
    ctx.stroke();

    ctx.strokeStyle = "#d7a7ff";
    ctx.lineWidth = 2.5;
    ctx.setLineDash([9, 7]);
    ctx.beginPath();
    ctx.moveTo(pad, targetY);
    ctx.lineTo(width - pad, targetY);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.font = "12px Inter, system-ui";
    ctx.fillStyle = "#2fd180";
    ctx.fillText(`BUY ${money.format(entryPrice)}`, pad + 8, entryY + 16);
    ctx.fillStyle = "#d7a7ff";
    ctx.fillText(
      targetFitsMarketScale
        ? `NEXT TARGET ${money.format(targetExitPrice)} (+${targetPct.toFixed(2)}%)`
        : `NEXT TARGET ABOVE VIEW ${money.format(targetExitPrice)} / ${Math.max(0, remainingPct).toFixed(2)}% REMAINING`,
      pad + 8,
      targetFitsMarketScale ? Math.max(16, targetY - 8) : targetY + 16,
    );

    sortedOpenTrades.slice(1).forEach((trade, index) => {
      const additionalTarget = Number(trade.target_exit_price);
      if (!Number.isFinite(additionalTarget) || additionalTarget < min || additionalTarget > max) {
        return;
      }
      const additionalY = yFor(additionalTarget);
      ctx.strokeStyle = "rgba(215, 167, 255, 0.55)";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([5, 7]);
      ctx.beginPath();
      ctx.moveTo(pad, additionalY);
      ctx.lineTo(width - pad, additionalY);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "rgba(215, 167, 255, 0.9)";
      ctx.fillText(`T${index + 2} ${money.format(additionalTarget)}`, width - pad - 110, additionalY - 5);
    });
  }

  candles.forEach((candle, index) => {
    const candleTime = Date.parse(candle.timestamp);
    if (Number.isFinite(firstTime) && Number.isFinite(candleTime)) {
      const elapsedSeconds = Math.round((candleTime - firstTime) / 1000);
      if (elapsedSeconds > 0 && elapsedSeconds % intervalSeconds === 0) {
        const x = xFor(index);
        ctx.strokeStyle = "rgba(255, 123, 0, 0.18)";
        ctx.setLineDash([4, 8]);
        ctx.beginPath();
        ctx.moveTo(x, pad);
        ctx.lineTo(x, height - pad);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }
  });

  candles.forEach((candle, index) => {
    const x = xFor(index);
    const openY = yFor(candle.open);
    const closeY = yFor(candle.close);
    const highY = yFor(candle.high);
    const lowY = yFor(candle.low);
    const rising = candle.close >= candle.open;
    ctx.strokeStyle = rising ? "#2fd180" : "#ff5c4d";
    ctx.fillStyle = rising ? "rgba(47, 209, 128, 0.7)" : "rgba(255, 92, 77, 0.75)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, highY);
    ctx.lineTo(x, lowY);
    ctx.stroke();
    const bodyTop = Math.min(openY, closeY);
    const bodyHeight = Math.max(3, Math.abs(closeY - openY));
    ctx.fillRect(x - bodyWidth / 2, bodyTop, bodyWidth, bodyHeight);
  });

  ctx.strokeStyle = "#ff7b00";
  ctx.lineWidth = 2;
  ctx.shadowColor = "rgba(255, 123, 0, 0.35)";
  ctx.shadowBlur = 10;
  ctx.beginPath();
  candles.forEach((candle, index) => {
    const x = xFor(index);
    const y = yFor(candle.close);
    if (index === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.stroke();
  ctx.shadowBlur = 0;

  events.forEach((event) => {
    if (event.type !== "trade" && event.type !== "rejected") {
      return;
    }
    const index = Math.max(0, Math.min(candles.length - 1, (event.step || 1) - 1));
    const x = xFor(index);
    const y = yFor(event.price);
    ctx.beginPath();
    ctx.arc(x, y, event.type === "rejected" ? 7 : 8, 0, Math.PI * 2);
    ctx.fillStyle = event.type === "rejected" ? "#d7a7ff" : event.signal === "BUY" ? "#2fd180" : "#ff5c4d";
    ctx.fill();
    ctx.strokeStyle = "#050505";
    ctx.lineWidth = 2;
    ctx.stroke();
  });

  ctx.fillStyle = "#a0a0a0";
  ctx.font = "13px Inter, system-ui";
  ctx.fillText(`Low ${money.format(min)}`, pad, height - 10);
  ctx.fillText(`High ${money.format(max)}`, pad, 20);

  const symbolMomentum = overlay.momentum || {};
  const score = symbolMomentum.score;
  if (Number.isFinite(score)) {
    const entry = overlay.momentumConfig?.entry_threshold ?? 80;
    const targetProfit = overlay.momentumConfig?.target_profit_pct ?? 0;
    ctx.fillStyle = "#f5f5f5";
    ctx.font = "14px Orbitron, system-ui";
    ctx.fillText(`Momentum ${score.toFixed(1)}`, width - pad - 180, 22);
    ctx.fillStyle = "#2fd180";
    ctx.fillText(`Entry ${Number(entry).toFixed(0)}`, width - pad - 180, 42);
    ctx.fillStyle = "#ff5c4d";
    ctx.fillText(`Target ${Number(targetProfit).toFixed(2)}%`, width - pad - 90, 42);
  }
}

function getVisibleChartWindow(candles, events) {
  const scroll = document.querySelector("#chart-window-scroll");
  const windowLabel = document.querySelector("#chart-window-label");
  const rangeLabel = document.querySelector("#chart-range-label");
  const sampleCount = document.querySelector("#chart-sample-count");
  windowLabel.textContent = "Last 5 minutes";
  if (!candles.length) {
    scroll.min = "0";
    scroll.max = "0";
    scroll.value = "0";
    rangeLabel.textContent = "Waiting";
    sampleCount.textContent = "0 ticks";
    chartPinnedToLive = true;
    return { candles: [], events: [] };
  }

  scroll.min = "0";
  scroll.max = String(candles.length - 1);
  if (chartPinnedToLive || Number(scroll.value) >= candles.length - 2) {
    scroll.value = String(candles.length - 1);
    chartPinnedToLive = true;
  }

  const endIndex = Math.min(candles.length - 1, Math.max(0, Number(scroll.value) || 0));
  const endTime = Date.parse(candles[endIndex].timestamp);
  let startIndex = endIndex;
  if (Number.isFinite(endTime)) {
    while (startIndex > 0) {
      const candleTime = Date.parse(candles[startIndex - 1].timestamp);
      if (!Number.isFinite(candleTime) || endTime - candleTime > chartWindowMs) {
        break;
      }
      startIndex -= 1;
    }
  } else {
    startIndex = Math.max(0, endIndex - 600);
  }

  const visibleCandles = candles.slice(startIndex, endIndex + 1);
  const firstVisible = visibleCandles[0];
  const lastVisible = visibleCandles[visibleCandles.length - 1];
  const startTime = Date.parse(firstVisible?.timestamp || "");
  const stopTime = Date.parse(lastVisible?.timestamp || "");
  const visibleEvents = events
    .filter((event) => {
      const eventTime = Date.parse(event.timestamp || "");
      if (Number.isFinite(eventTime) && Number.isFinite(startTime) && Number.isFinite(stopTime)) {
        return eventTime >= startTime && eventTime <= stopTime;
      }
      return event.step >= Number(firstVisible?.step || startIndex + 1)
        && event.step <= Number(lastVisible?.step || endIndex + 1);
    })
    .map((event) => {
      const eventTime = Date.parse(event.timestamp || "");
      let nearestIndex = 0;
      let nearestDistance = Number.POSITIVE_INFINITY;
      visibleCandles.forEach((candle, index) => {
        const candleTime = Date.parse(candle.timestamp || "");
        const distance = Number.isFinite(eventTime) && Number.isFinite(candleTime)
          ? Math.abs(candleTime - eventTime)
          : Math.abs(Number(candle.step || index + 1) - Number(event.step || 1));
        if (distance < nearestDistance) {
          nearestDistance = distance;
          nearestIndex = index;
        }
      });
      return { ...event, step: nearestIndex + 1 };
    });
  if (Number.isFinite(startTime) && Number.isFinite(stopTime)) {
    const minutes = Math.max(0, (stopTime - startTime) / 60000);
    rangeLabel.textContent = `${formatTimeLabel(firstVisible.timestamp)} - ${formatTimeLabel(lastVisible.timestamp)}`;
    windowLabel.textContent = `${minutes.toFixed(1)} min visible`;
  } else {
    rangeLabel.textContent = `${startIndex + 1} - ${endIndex + 1}`;
  }
  sampleCount.textContent = `${visibleCandles.length} of ${candles.length} ticks`;
  return { candles: visibleCandles, events: visibleEvents };
}

function render(state, config) {
  document.querySelector("#cash").textContent = money.format(state.cash);
  document.querySelector("#available-cash").textContent = money.format(state.availableCash);
  document.querySelector("#portfolio-value").textContent = money.format(state.portfolioValue);
  document.querySelector("#realized-pl").textContent = money.format(state.realizedPl);
  document.querySelector("#gain-reserve").textContent = money.format(state.gainReserve);
  document.querySelector("#trade-count").textContent = String(state.trades.length);

  const summary = `${config.symbol}: ${state.trades.length} paper trades, ${state.events.filter((event) => event.type === "rejected").length} rejected, ${money.format(state.gainReserve)} reserved from gains.`;
  document.querySelector("#signal-summary").textContent = summary;

  const table = document.querySelector("#event-table");
  table.innerHTML = "";
  state.events.forEach((event) => {
    const row = document.createElement("tr");
    const tagClass = event.type === "rejected" ? "tag-rejected" : `tag-${event.signal.toLowerCase()}`;
    row.innerHTML = `
      <td>${event.step}</td>
      <td>${event.type}</td>
      <td><span class="tag ${tagClass}">${event.signal}</span></td>
      <td>${money.format(event.price)}</td>
      <td>${event.details}</td>
    `;
    table.appendChild(row);
  });

  drawChart(config.prices, state.events);
}

function switchView(viewId) {
  document.querySelectorAll(".view-tab").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.view === viewId);
  });
  document.querySelectorAll(".view-panel").forEach((panel) => {
    panel.classList.toggle("is-active", panel.id === viewId);
  });
  if (viewId === "saved-sessions-view") {
    loadSavedSessions();
  }
}

async function loadSavedSessions() {
  const state = document.querySelector("#saved-sessions-state");
  const cards = document.querySelector("#saved-session-cards");
  const refresh = document.querySelector("#refresh-saved-sessions");
  state.hidden = false;
  state.textContent = "Loading saved SQLite sessions...";
  cards.innerHTML = "";
  refresh.disabled = true;
  try {
    const payload = await apiRequest("/api/saved-sessions");
    renderSavedSessions(payload);
  } catch (error) {
    state.hidden = false;
    state.textContent = error.message || "Unable to load saved SQLite sessions.";
  } finally {
    refresh.disabled = false;
  }
}

function renderSavedSessions(payload) {
  const sessions = payload.sessions || [];
  const counts = payload.eventCounts || {};
  const state = document.querySelector("#saved-sessions-state");
  const cards = document.querySelector("#saved-session-cards");
  document.querySelector("#saved-session-count").textContent = String(sessions.length);
  document.querySelector("#saved-market-count").textContent = String(counts.market_data || 0);
  document.querySelector("#saved-trade-count").textContent = String(counts.simulated_trade || 0);
  document.querySelector("#saved-rejected-count").textContent = String(counts.rejected_trade || 0);
  document.querySelector("#saved-sessions-summary").textContent =
    `${sessions.length} saved 30-minute trade windows from ${payload.databasePath || "SQLite"}.`;

  if (!sessions.length) {
    state.hidden = false;
    state.textContent = "No saved trade sessions yet. Market data may exist, but a card appears after a BUY, SELL, or rejection is saved.";
    cards.innerHTML = "";
    return;
  }

  state.hidden = true;
  cards.innerHTML = sessions.map((session) => {
    const winning = Number(session.realizedPl) > 0;
    const reasons = Object.entries(session.rejectedTradesByReason || {})
      .map(([reason, count]) => `${escapeHtml(reason)}: ${count}`)
      .join(", ") || "None";
    const symbols = (session.symbols || []).join(", ") || "No symbol";
    const status = session.isComplete ? "Complete" : "In progress";
    return `
      <article class="session-card ${winning ? "is-winning" : "is-non-winning"}">
        <header class="session-card-header">
          <div>
            <p>${escapeHtml(symbols)} / ${escapeHtml(status)}</p>
            <h3>${escapeHtml(formatSessionRange(session.start, session.end))}</h3>
          </div>
          <strong class="session-outcome">${winning ? "Winning Session" : "Non-Winning Session"}</strong>
        </header>
        <div class="session-metrics">
          <div><span>Total Return</span><strong>${percentText(session.totalReturn)}</strong></div>
          <div><span>Realized P/L</span><strong>${money.format(session.realizedPl || 0)}</strong></div>
          <div><span>Max Drawdown</span><strong>${percentText(session.maxDrawdown)}</strong></div>
          <div><span>Win Rate</span><strong>${percentText(session.winRate)}</strong></div>
          <div><span>Average Win</span><strong>${money.format(session.averageWin || 0)}</strong></div>
          <div><span>Average Loss</span><strong>${money.format(session.averageLoss || 0)}</strong></div>
          <div><span>Trades</span><strong>${Number(session.numberOfTrades || 0)}</strong></div>
          <div><span>Best / Worst</span><strong>${money.format(session.bestTrade || 0)} / ${money.format(session.worstTrade || 0)}</strong></div>
        </div>
        <footer class="session-card-footer">
          ${Number(session.buyCount || 0)} buys / ${Number(session.openLots || 0)} open lots / Rejected: ${reasons}
        </footer>
      </article>
    `;
  }).join("");
}

function renderLiveStatus(status, signals = {}) {
  lastLiveStatus = status;
  lastLiveSignals = signals;
  updateExecutionModeChrome(status);
  syncControlsFromStatus(status);
  document.querySelector("#cash").textContent = money.format(status.cash || 0);
  document.querySelector("#available-cash").textContent = money.format(status.availableCash || 0);
  document.querySelector("#portfolio-value").textContent = money.format(status.portfolioValue || 0);
  document.querySelector("#realized-pl").textContent = money.format(status.realizedPl || 0);
  document.querySelector("#gain-reserve").textContent = money.format(status.gainReserve || 0);
  document.querySelector("#trade-count").textContent = String(status.trades || 0);
  renderPerformanceAnalytics(status.performanceAnalytics || {});

  document.querySelector("#live-symbols").textContent = (status.symbols || []).join(", ") || "None";
  const displayPrices = Object.keys(status.latestPrices || {}).length
    ? status.latestPrices
    : (status.marketDataCollector && status.marketDataCollector.latestPrices) || {};
  const prices = Object.entries(displayPrices)
    .map(([symbol, price]) => `${symbol} ${money.format(price)}`)
    .join(", ");
  document.querySelector("#live-prices").textContent = prices || "No market ticks yet";
  const signalText = Object.entries(signals)
    .map(([symbol, signal]) => `${symbol}:${signal}`)
    .join(", ");
  document.querySelector("#live-signals").textContent = signalText || "No signal yet";
  const positions = (status.positions || [])
    .map((position) => `${position.symbol} ${Number(position.quantity).toFixed(6)} @ ${money.format(position.average_price)}`)
    .join(", ");
  document.querySelector("#live-positions").textContent = positions || "None";
  renderCollectorStatus(status.marketDataCollector);
  renderManualControlState(status.manualControls || {}, status.discordControl || {});

  if (!status.configured) {
    showLiveStatus("Robinhood credentials are not configured in .env.", true);
    return;
  }
  if (status.lastError) {
    showLiveStatus(status.lastError, true);
    renderLiveApiError(status.lastError, status);
    return;
  }
  renderLivePaperOutput(status, signals);
  showLiveStatus(
    isLiveMode(status)
      ? "Connected in LIVE mode. Approved strategy signals can submit Robinhood market orders."
      : "Connected in PAPER mode. Strategy signals are simulated only.",
  );
}

function renderPerformanceAnalytics(analytics = {}) {
  setText("#analytics-win-rate", percentText(analytics.win_rate_pct));
  setText("#analytics-profit-factor", Number(analytics.profit_factor || 0).toFixed(2));
  setText("#analytics-total-return", percentText(analytics.total_return_pct));
  setText("#analytics-open-exposure", percentText(analytics.open_exposure_pct));
  setText("#analytics-average-win", money.format(analytics.average_win || 0));
  setText("#analytics-average-loss", money.format(analytics.average_loss || 0));
  setText(
    "#analytics-best-worst",
    `${money.format(analytics.best_trade || 0)} / ${money.format(analytics.worst_trade || 0)}`,
  );
}

function setText(selector, text) {
  const target = document.querySelector(selector);
  if (target) {
    target.textContent = text;
  }
}

function renderManualControlState(controls = {}, discord = {}) {
  const manualState = document.querySelector("#manual-state");
  const discordState = document.querySelector("#discord-control-status");
  const status = document.querySelector("#manual-control-status");
  const pausedText = controls.paused ? `Paused: ${controls.pauseReason || "manual pause"}` : "Running";
  const killText = controls.killSwitch ? "Kill switch on" : "Kill switch off";
  manualState.textContent = `${pausedText} / ${killText}`;
  discordState.textContent = discord.running
    ? `Running / ${discord.prefix || "!trader"}`
    : discord.enabled
      ? "Enabled, not connected"
      : "Off";
  if (status) {
    status.textContent = `${pausedText}. ${killText}. Discord control ${discordState.textContent.toLowerCase()}. Manual exits apply to ${tradeNoun(lastLiveStatus, true)}.`;
  }
}

function renderCollectorStatus(collector = {}) {
  const target = document.querySelector("#collector-status");
  if (!target) {
    return;
  }
  const symbolCount = (collector.symbols || []).length;
  const tickCount = Number(collector.collectedTicks || 0);
  if (!collector.running) {
    target.textContent = "Off";
    return;
  }
  const lastText = collector.lastCollectionAt
    ? new Date(collector.lastCollectionAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : "starting";
  target.textContent = `${tickCount} ticks / ${symbolCount} symbols / ${lastText}`;
}

function setButtonText(selector, text) {
  document.querySelector(selector).textContent = text;
}

function renderLivePaperOutput(status, signals = {}) {
  const configuredSymbols = status.symbols || [];
  const requestedSymbol = document.querySelector("#symbol").value.trim().toUpperCase();
  const symbol = configuredSymbols.includes(requestedSymbol)
    ? requestedSymbol
    : configuredSymbols[0];
  const prices = (status.priceHistory && status.priceHistory[symbol]) || [];
  const candles = (status.candleHistory && status.candleHistory[symbol]) || [];
  const allEvents = status.eventHistory || [];
  const events = allEvents.filter((event) => event.symbol === symbol);
  const latestSignal = events.slice().reverse().find((event) => event.type === "signal");
  const openTrades = status.openTradeMetadata?.[symbol] || [];
  renderTargetLevel(openTrades, prices[prices.length - 1]);

  if (!symbol || prices.length === 0) {
    document.querySelector("#signal-summary").textContent = isLiveMode(status)
      ? "Waiting for the first Robinhood live execution tick."
      : "Waiting for the first Robinhood paper tick.";
    drawLiveCandlestickChart([], []);
    return;
  }

  const tradeEvents = events.filter((event) => event.type === "trade" || event.type === "live_order");
  document.querySelector("#signal-summary").textContent =
    `${symbol}: ${prices.length} Robinhood quote ticks, ${candles.length} live candles, ${tradeEvents.length} ${tradeNoun(status, true)}, ${events.filter((event) => event.type === "rejected").length} rejected. ${latestSignal ? latestSignal.details : ""}`;

  const table = document.querySelector("#event-table");
  table.innerHTML = "";
  events.slice().reverse().slice(0, 5).forEach((event) => {
    const row = document.createElement("tr");
    const signal = event.signal || signals[event.symbol] || "HOLD";
    const tagClass = event.type === "rejected" ? "tag-rejected" : `tag-${signal.toLowerCase()}`;
    row.innerHTML = `
      <td>${event.step}</td>
      <td>${event.type}</td>
      <td><span class="tag ${tagClass}">${signal}</span></td>
      <td>${money.format(event.price)}</td>
      <td>${event.symbol}: ${event.details}</td>
    `;
    table.appendChild(row);
  });

  const visible = getVisibleChartWindow(candles, events);
  drawLiveCandlestickChart(visible.candles, visible.events, {
    momentum: status.momentum?.[symbol],
    momentumConfig: status.momentumConfig,
    openTrades,
  });
}

function renderTargetLevel(openTrades, currentPrice) {
  const target = document.querySelector("#chart-target-level");
  if (!target) {
    return;
  }
  const trades = Array.isArray(openTrades) ? openTrades : openTrades ? [openTrades] : [];
  const openTrade = trades
    .slice()
    .sort((a, b) => Number(a.target_exit_price) - Number(b.target_exit_price))[0];
  const entryPrice = Number(openTrade?.entry_price);
  const targetPrice = Number(openTrade?.target_exit_price);
  const latestPrice = Number(currentPrice);
  if (!Number.isFinite(entryPrice) || !Number.isFinite(targetPrice)) {
    target.textContent = "No open trade";
    return;
  }
  const targetPct = ((targetPrice - entryPrice) / entryPrice) * 100;
  const remainingPct = Number.isFinite(latestPrice) && latestPrice > 0
    ? ((targetPrice - latestPrice) / latestPrice) * 100
    : targetPct;
  const remainingText = remainingPct <= 0
    ? "target reached"
    : `${remainingPct.toFixed(2)}% remaining`;
  target.textContent = `${trades.length} open / next ${money.format(targetPrice)} / +${targetPct.toFixed(2)}% / ${remainingText}`;
}

function readConfig() {
  const priceSeries = document.querySelector("#price-series");
  const prices = priceSeries ? parsePrices(priceSeries.value) : [];
  if (prices.length < 6) {
    throw new Error("Enter at least 6 positive prices so the live-style optimizer has enough data.");
  }

  const startingCash = parsePositiveNumber(document.querySelector("#starting-cash").value, 10000);
  const maxTradeSize = parsePositiveNumber(document.querySelector("#max-trade-size").value, 1000);
  const maxDailyLossPercent = parsePositiveNumber(document.querySelector("#max-daily-loss-percent").value, 5);
  const maxDailyLoss = startingCash * (maxDailyLossPercent / 100);
  const maxTrades = 999;
  const gainReservePercent = parsePositiveNumber(document.querySelector("#gain-reserve-percent").value, 15);
  const lookbackWindow = 5;
  const buyDipPercent = 0;
  const reboundPercent = 0;
  const profitTargetPercent = parsePositiveNumber(document.querySelector("#target-profit-percent").value, 0.25);
  const stopLossPercent = Math.abs(Number(document.querySelector("#catastrophic-loss-percent").value) || -3);
  const trailingStopPercent = profitTargetPercent;

  return {
    symbol: document.querySelector("#symbol").value.trim().toUpperCase() || "BTC-USD",
    startingCash,
    maxTradeSize,
    maxDailyLoss,
    maxTrades,
    gainReservePercent,
    lookbackWindow,
    buyDipPercent,
    reboundPercent,
    profitTargetPercent,
    stopLossPercent,
    trailingStopPercent,
    killSwitch: document.querySelector("#kill-switch").checked,
    prices,
  };
}

function readLiveOverrides() {
  const startingCash = parsePositiveNumber(document.querySelector("#starting-cash").value, 10000);
  const maxDailyLossPercent = parsePositiveNumber(
    document.querySelector("#max-daily-loss-percent").value,
    5,
  );
  const targetProfitPct = parsePositiveNumber(document.querySelector("#target-profit-percent").value, 0.25);
  const symbol = document.querySelector("#symbol").value.trim().toUpperCase() || "BTC-USD";
  return {
    symbol,
    startingCash,
    maxTradeSize: parsePositiveNumber(document.querySelector("#max-trade-size").value, 1000),
    maxDailyLoss: startingCash * (maxDailyLossPercent / 100),
    maxTrades: 999,
    cooldownAfterLossSeconds: parsePositiveInteger(document.querySelector("#cooldown-after-loss-seconds").value, 60),
    gainReservePercent: parsePositiveNumber(document.querySelector("#gain-reserve-percent").value, 15),
    lookbackWindow: 5,
    buyDipPercent: 0,
    reboundPercent: 0,
    sellAboveDipPercent: targetProfitPct,
    targetProfitPct,
    paperAllocationPerTrade: parsePositiveNumber(document.querySelector("#max-trade-size").value, 100),
    momentumEntryThreshold: parsePositiveNumber(document.querySelector("#momentum-entry-threshold").value, 80),
    momentumExitThreshold: parsePositiveNumber(document.querySelector("#momentum-exit-threshold").value, 40),
    momentumEntryIntervalSeconds: parsePositiveInteger(
      document.querySelector("#momentum-entry-interval-seconds").value,
      300,
    ),
    maxOpenTrades: parsePositiveInteger(document.querySelector("#max-open-trades").value, 3),
    catastrophicLossPct: Number(document.querySelector("#catastrophic-loss-percent").value) || -3,
    stopLossPercent: Math.abs(Number(document.querySelector("#catastrophic-loss-percent").value) || -3),
    trailingStopPercent: targetProfitPct,
    killSwitch: document.querySelector("#kill-switch").checked,
  };
}

function readBacktestConfig() {
  const symbol = document.querySelector("#backtest-symbol").value.trim().toUpperCase() || "BTC-USD";
  const start = document.querySelector("#backtest-start").value;
  const end = document.querySelector("#backtest-end").value;
  const startingCash = parsePositiveNumber(document.querySelector("#backtest-starting-cash").value, 0);
  const maxTradeSize = parsePositiveNumber(document.querySelector("#backtest-max-trade-size").value, 0);
  const entryScore = Number(document.querySelector("#backtest-entry-score").value);
  const exitScore = Number(document.querySelector("#backtest-exit-score").value);
  const targetProfitPct = parsePositiveNumber(document.querySelector("#backtest-target-profit").value, 0);
  const cooldownSeconds = Number(document.querySelector("#backtest-cooldown").value);
  const entryIntervalSeconds = Number(document.querySelector("#backtest-entry-interval").value);
  const maxOpenTrades = Number(document.querySelector("#backtest-max-open-trades").value);
  const catastrophicLossPct = Number(document.querySelector("#backtest-catastrophic-loss").value);

  if (!start || !end || new Date(end) <= new Date(start)) {
    throw new Error("Choose a valid date/time range.");
  }
  if (startingCash <= 0 || maxTradeSize <= 0) {
    throw new Error("Starting cash and max trade size must be positive.");
  }
  if (maxTradeSize > startingCash) {
    throw new Error("Max trade size cannot exceed starting cash.");
  }
  if (!Number.isFinite(entryScore) || entryScore < 0 || entryScore > 100) {
    throw new Error("Entry score must be between 0 and 100.");
  }
  if (!Number.isFinite(exitScore) || exitScore < 0 || exitScore > 100) {
    throw new Error("Exit score must be between 0 and 100.");
  }
  if (!Number.isFinite(cooldownSeconds) || cooldownSeconds < 0) {
    throw new Error("Cooldown seconds cannot be negative.");
  }
  if (!Number.isInteger(entryIntervalSeconds) || entryIntervalSeconds < 1) {
    throw new Error("Entry interval seconds must be at least 1.");
  }
  if (!Number.isInteger(maxOpenTrades) || maxOpenTrades < 1 || maxOpenTrades > 100) {
    throw new Error("Max open trades must be between 1 and 100.");
  }
  if (!Number.isFinite(catastrophicLossPct) || catastrophicLossPct >= 0) {
    throw new Error("Catastrophic loss % must be negative.");
  }

  return {
    symbol,
    start: new Date(start).toISOString(),
    end: new Date(end).toISOString(),
    startingCash,
    maxTradeSize,
    entryScore,
    exitScore,
    targetProfitPct,
    cooldownSeconds,
    entryIntervalSeconds,
    maxOpenTrades,
    catastrophicLossPct,
    walkForwardEnabled: document.querySelector("#walk-forward-enabled").checked,
  };
}

function setBacktestError(message = "") {
  const error = document.querySelector("#backtest-error");
  error.textContent = message;
  error.classList.toggle("is-visible", Boolean(message));
}

async function runBacktestFromForm(event) {
  event.preventDefault();
  setBacktestError("");
  const button = document.querySelector("#run-backtest");
  try {
    button.textContent = "Running...";
    button.disabled = true;
    document.querySelector("#backtest-summary").textContent = "Loading local historical market data...";
    const payload = await apiRequest("/api/backtest/run", {
      method: "POST",
      body: JSON.stringify(readBacktestConfig()),
    });
    renderBacktest(payload);
  } catch (error) {
    setBacktestError(error.message || "Backtest failed.");
    document.querySelector("#backtest-summary").textContent = "Backtest could not run.";
  } finally {
    button.textContent = "Run Backtest";
    button.disabled = false;
  }
}

function renderBacktest(payload) {
  const result = payload.result;
  const metrics = result.metrics || {};
  document.querySelector("#bt-total-return").textContent = percentText(metrics.totalReturn);
  document.querySelector("#bt-realized-pl").textContent = money.format(metrics.realizedPl || 0);
  document.querySelector("#bt-max-drawdown").textContent = percentText(metrics.maxDrawdown);
  document.querySelector("#bt-win-rate").textContent = percentText(metrics.winRate);
  document.querySelector("#bt-average-win").textContent = money.format(metrics.averageWin || 0);
  document.querySelector("#bt-average-loss").textContent = money.format(metrics.averageLoss || 0);
  document.querySelector("#bt-trades").textContent = String(metrics.numberOfTrades || 0);
  document.querySelector("#bt-best-worst").textContent = `${money.format(metrics.bestTrade || 0)} / ${money.format(metrics.worstTrade || 0)}`;

  const warnings = result.warnings || [];
  document.querySelector("#backtest-summary").textContent =
    `${result.config.symbol}: ${payload.dataPointCount} historical ticks from ${payload.source}. ${warnings.join(" ")}`;
  document.querySelector("#backtest-rejections").textContent =
    `Rejected trades by reason: ${formatRejectedReasons(result.rejectedReasons || {})}`;
  renderBacktestCharts(result);
  renderTradeLog(result.trades || []);
  renderWalkForward(payload.walkForward);
}

function formatRejectedReasons(reasons) {
  const entries = Object.entries(reasons);
  if (!entries.length) {
    return "none";
  }
  return entries.map(([reason, count]) => `${reason}: ${count}`).join(" | ");
}

function renderTradeLog(trades) {
  const table = document.querySelector("#backtest-trade-log");
  table.innerHTML = "";
  if (!trades.length) {
    const row = document.createElement("tr");
    row.innerHTML = `<td colspan="14">No simulated trades in this backtest.</td>`;
    table.appendChild(row);
    return;
  }
  trades.forEach((trade) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${String(trade.trade_id || "").slice(0, 8)}</td>
      <td>${trade.symbol}</td>
      <td>${formatTimeLabel(trade.entry_timestamp)}</td>
      <td>${money.format(trade.entry_price || 0)}</td>
      <td>${Number(trade.entry_score || 0).toFixed(1)}</td>
      <td>${trade.exit_timestamp ? formatTimeLabel(trade.exit_timestamp) : "--"}</td>
      <td>${trade.exit_price ? money.format(trade.exit_price) : "--"}</td>
      <td>${trade.exit_score ? Number(trade.exit_score).toFixed(1) : "--"}</td>
      <td>${money.format(trade.trade_size || 0)}</td>
      <td>${money.format(trade.realized_pl || 0)}</td>
      <td>${percentText(trade.return_pct)}</td>
      <td>${trade.exit_reason || "--"}</td>
      <td>${secondsToDuration(trade.duration_seconds)}</td>
      <td>${trade.status}</td>
    `;
    table.appendChild(row);
  });
}

function renderWalkForward(walkForward) {
  const warning = document.querySelector("#walk-forward-warning");
  if (!walkForward || !walkForward.enabled) {
    warning.textContent = "Enable walk-forward testing to compare optimized training settings against an unseen period.";
    document.querySelector("#wf-best-params").textContent = "--";
    document.querySelector("#wf-training-return").textContent = "--";
    document.querySelector("#wf-training-drawdown").textContent = "--";
    document.querySelector("#wf-training-win-rate").textContent = "--";
    document.querySelector("#wf-test-return").textContent = "--";
    document.querySelector("#wf-test-drawdown").textContent = "--";
    document.querySelector("#wf-test-win-rate").textContent = "--";
    document.querySelector("#wf-degradation").textContent = "--";
    return;
  }
  const params = walkForward.bestParameters || {};
  const training = walkForward.trainingMetrics || {};
  const test = walkForward.testMetrics || {};
  const degradation = walkForward.degradation || {};
  document.querySelector("#wf-best-params").textContent =
    `Entry ${params.entryScore}, Exit ${params.exitScore}, Target ${params.targetProfitPct}%, Cooldown ${params.cooldownSeconds}s`;
  document.querySelector("#wf-training-return").textContent = percentText(training.totalReturn);
  document.querySelector("#wf-training-drawdown").textContent = percentText(training.maxDrawdown);
  document.querySelector("#wf-training-win-rate").textContent = percentText(training.winRate);
  document.querySelector("#wf-test-return").textContent = percentText(test.totalReturn);
  document.querySelector("#wf-test-drawdown").textContent = percentText(test.maxDrawdown);
  document.querySelector("#wf-test-win-rate").textContent = percentText(test.winRate);
  document.querySelector("#wf-degradation").textContent =
    `Return ${percentText(degradation.totalReturnDelta)}, Win ${percentText(degradation.winRateDelta)}`;
  warning.textContent = walkForward.overfitWarning || "Walk-forward test passed basic overfit checks.";
}

function renderBacktestCharts(result) {
  drawSeriesChart(
    backtestEquityCtx,
    backtestEquityChart,
    (result.equityCurve || []).map((point) => ({ timestamp: point.timestamp, value: point.value })),
    [],
    { lineColor: "#2fd180", valueLabel: "Equity" },
  );
  drawSeriesChart(
    backtestPriceCtx,
    backtestPriceChart,
    (result.priceSeries || []).map((point) => ({ timestamp: point.timestamp, value: point.price, score: point.score })),
    result.markers || [],
    { lineColor: "#ff7b00", valueLabel: "Price" },
  );
}

function drawSeriesChart(context, canvasEl, series, markers, options) {
  if (!context || !canvasEl) {
    return;
  }
  const width = canvasEl.width;
  const height = canvasEl.height;
  const pad = 34;
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#070707";
  context.fillRect(0, 0, width, height);
  if (!series.length) {
    context.fillStyle = "#a0a0a0";
    context.font = "13px Inter, system-ui";
    context.fillText("No historical data for this range.", pad, height / 2);
    return;
  }
  const values = series.map((point) => point.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const xForIndex = (index) => pad + (index * (width - pad * 2)) / Math.max(series.length - 1, 1);
  const yForValue = (value) => height - pad - ((value - min) / range) * (height - pad * 2);

  context.strokeStyle = "rgba(255, 255, 255, 0.08)";
  context.lineWidth = 1;
  for (let i = 0; i < 4; i += 1) {
    const y = pad + (i * (height - pad * 2)) / 3;
    context.beginPath();
    context.moveTo(pad, y);
    context.lineTo(width - pad, y);
    context.stroke();
  }

  context.strokeStyle = options.lineColor;
  context.lineWidth = 2;
  context.beginPath();
  series.forEach((point, index) => {
    const x = xForIndex(index);
    const y = yForValue(point.value);
    if (index === 0) {
      context.moveTo(x, y);
    } else {
      context.lineTo(x, y);
    }
  });
  context.stroke();

  markers.forEach((marker) => {
    const markerTime = Date.parse(marker.timestamp);
    let index = series.findIndex((point) => Date.parse(point.timestamp) >= markerTime);
    if (index < 0) {
      index = series.length - 1;
    }
    const x = xForIndex(index);
    const y = yForValue(marker.price);
    context.beginPath();
    context.arc(x, y, marker.type === "BUY" ? 6 : 7, 0, Math.PI * 2);
    context.fillStyle = marker.type === "BUY" ? "#2fd180" : "#ff5c4d";
    context.fill();
    context.strokeStyle = "#050505";
    context.stroke();
  });

  context.fillStyle = "#a0a0a0";
  context.font = "13px Inter, system-ui";
  context.fillText(`${options.valueLabel} low ${money.format(min)}`, pad, height - 10);
  context.fillText(`High ${money.format(max)}`, pad, 20);
}

function applyFastTestControls() {
  document.querySelector("#target-profit-percent").value = "0.01";
  document.querySelector("#momentum-entry-threshold").value = "45";
  document.querySelector("#momentum-exit-threshold").value = "20";
  document.querySelector("#momentum-entry-interval-seconds").value = "300";
  document.querySelector("#max-open-trades").value = "3";
  document.querySelector("#catastrophic-loss-percent").value = "-3";
  document.querySelector("#max-daily-loss-percent").value = "5";
  document.querySelector("#cooldown-after-loss-seconds").value = "15";
  document.querySelector("#max-trade-size").value = "100";
  showLiveStatus("Fast test controls applied. Run the trading loop to continue.");
}

async function apiRequest(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), apiTimeoutMs);
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    signal: controller.signal,
    ...options,
  }).catch((error) => {
    if (error.name === "AbortError") {
      throw new Error("Local API timed out while waiting for Robinhood market data.");
    }
    throw error;
  }).finally(() => clearTimeout(timeout));
  const payload = await response.json();
  if (response.status === 401) {
    showAuthScreen("Your session expired. Sign in again to continue.");
    throw new Error("Authentication required.");
  }
  if (!response.ok || payload.error) {
    throw new Error(payload.error || "Local API request failed.");
  }
  return payload;
}

async function checkLiveStatus() {
  try {
    const status = await apiRequest("/api/status");
    renderLiveStatus(status);
  } catch (error) {
    showLiveStatus("Start the Python web app with: python -m src.web_server", true);
  }
}

async function runLiveTick() {
  if (liveTickInFlight) {
    return null;
  }
  liveTickInFlight = true;
  try {
    showLiveStatus(
      isLiveMode()
        ? "Pulling Robinhood quote and evaluating live order controls..."
        : "Pulling Robinhood quote and running paper controls...",
    );
    const payload = await apiRequest("/api/live-paper/step", {
      method: "POST",
      body: JSON.stringify(readLiveOverrides()),
    });
    renderLiveStatus(payload.status, payload.result.signals || {});
    if (payload.result.error) {
      showLiveStatus(payload.result.error, true);
      renderLiveApiError(payload.result.error, payload.status);
      return;
    }
    const rejectionCount = (payload.result.rejections || []).length;
    const tradeCount = (payload.result.trades || []).length;
    document.querySelector("#signal-summary").textContent =
      `${modeText(payload.status)} tick complete: ${tradeCount} ${tradeNoun(payload.status, true)}, ${rejectionCount} rejections.`;
    return payload;
  } catch (error) {
    showLiveStatus(error.message || "Trading tick failed.", true);
    return null;
  } finally {
    liveTickInFlight = false;
  }
}

function renderLiveApiError(message, status = {}) {
  document.querySelector("#signal-summary").textContent =
    `Robinhood market data call failed: ${message}`;
  const table = document.querySelector("#event-table");
  table.innerHTML = "";
  const row = document.createElement("tr");
  row.innerHTML = `
    <td>${(status.eventHistory || []).length}</td>
    <td>api_error</td>
    <td><span class="tag tag-rejected">ERROR</span></td>
    <td>$0.00</td>
    <td>${message}</td>
  `;
  table.appendChild(row);
}

async function resetLiveSession() {
  try {
    stopLiveLoop();
    stopSimulationLoop("Trading loop stopped and session reset.");
    const payload = await apiRequest("/api/live-paper/reset", { method: "POST", body: "{}" });
    renderLiveStatus(payload.status);
    showLiveStatus("Local trading session reset.");
  } catch (error) {
    showLiveStatus(error.message || "Reset failed.", true);
  }
}

async function runManualControl(action, extra = {}) {
  try {
    const payload = await apiRequest("/api/manual-control", {
      method: "POST",
      body: JSON.stringify({
        action,
        reason: extra.reason || "Browser manual control",
        symbol: extra.symbol,
        overrides: readLiveOverrides(),
      }),
    });
    renderLiveStatus(payload.status);
    if (payload.error) {
      showLiveStatus(payload.error, true);
      return;
    }
    const closed = Array.isArray(payload.trades) ? payload.trades.length : 0;
    showLiveStatus(
      action === "force_exit"
        ? `Manual force exit processed: ${closed} ${tradeNoun(payload.status, true)}.`
        : `Manual control applied: ${action}.`,
    );
  } catch (error) {
    showLiveStatus(error.message || "Manual control failed.", true);
  }
}

function stopLiveLoop(message = "Live tick stream stopped.") {
  const button = document.querySelector("#run-live-tick");
  if (liveTimer) {
    clearInterval(liveTimer);
    liveTimer = null;
    button.textContent = "Start Live Tick Stream";
    showLiveStatus(message);
  }
}

function toggleAutoLive() {
  const button = document.querySelector("#run-live-tick");
  if (liveTimer) {
    stopLiveLoop();
    return;
  }
  stopSimulationLoop("Trading loop paused while live tick stream runs.");
  runLiveTick();
  liveTimer = setInterval(runLiveTick, liveLoopMs);
  button.textContent = "Stop Live Tick Stream";
  showLiveStatus(
    isLiveMode()
      ? "Live tick stream running every 0.5 seconds. Approved signals can submit orders."
      : "Paper tick stream running every 0.5 seconds.",
  );
}

function stopSimulationLoop(message = "Trading loop stopped.") {
  if (simulationTimer) {
    clearInterval(simulationTimer);
    simulationTimer = null;
    setButtonText("#simulation-form button[type='submit']", "Run Trading Loop");
    showLiveStatus(message);
  }
}

async function simulationStep() {
  await runLiveTick();
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  clearError();
  if (simulationTimer) {
    stopSimulationLoop();
    return;
  }
  stopLiveLoop("Live tick stream paused while the trading loop runs.");
  setButtonText("#simulation-form button[type='submit']", "Stop Trading Loop");
  showLiveStatus(
    isLiveMode()
      ? "Running continuous live execution loop. Press Stop Trading Loop to pause."
      : "Running continuous live-data paper loop. Press Stop Trading Loop to pause.",
  );
  simulationStep();
  simulationTimer = setInterval(simulationStep, liveLoopMs);
});

document.querySelector("#check-live-status").addEventListener("click", checkLiveStatus);
document.querySelector("#run-live-tick").addEventListener("click", toggleAutoLive);
document.querySelector("#reset-live-session").addEventListener("click", resetLiveSession);
document.querySelector("#test-controls").addEventListener("click", applyFastTestControls);
document.querySelector("#manual-pause").addEventListener("click", () => runManualControl("pause"));
document.querySelector("#manual-resume").addEventListener("click", () => runManualControl("resume"));
document.querySelector("#manual-kill-on").addEventListener("click", () => {
  document.querySelector("#kill-switch").checked = true;
  runManualControl("kill_on");
});
document.querySelector("#manual-kill-off").addEventListener("click", () => {
  document.querySelector("#kill-switch").checked = false;
  runManualControl("kill_off");
});
document.querySelector("#manual-force-exit").addEventListener("click", () => {
  const symbol = document.querySelector("#symbol").value.trim().toUpperCase() || "ALL";
  runManualControl("force_exit", { symbol, reason: "Browser force exit" });
});
document.querySelector("#backtest-form").addEventListener("submit", runBacktestFromForm);
document.querySelector("#refresh-saved-sessions").addEventListener("click", loadSavedSessions);
document.querySelectorAll(".view-tab").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});
document.querySelector("#jump-live-chart").addEventListener("click", () => {
  const scroll = document.querySelector("#chart-window-scroll");
  scroll.value = scroll.max;
  chartPinnedToLive = true;
  if (lastLiveStatus) {
    renderLivePaperOutput(lastLiveStatus, lastLiveSignals);
  }
});
document.querySelector("#chart-window-scroll").addEventListener("input", (event) => {
  const scroll = event.currentTarget;
  chartPinnedToLive = Number(scroll.value) >= Number(scroll.max);
  if (lastLiveStatus) {
    renderLivePaperOutput(lastLiveStatus, lastLiveSignals);
  }
});
document.querySelector("#sign-in-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const email = document.querySelector("#auth-email").value.trim();
    const password = document.querySelector("#auth-password").value;
    await authRequest("/api/auth/login", { email, password });
    showAuthenticatedApp(email);
    await startApplication();
  } catch (error) {
    setAuthMessage(error.message || "Sign in failed.");
  }
});
document.querySelector("#forgot-password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const payload = await authRequest("/api/auth/forgot-password", {
      email: document.querySelector("#recovery-email").value.trim(),
    });
    if (payload.resetToken) {
      document.querySelector("#reset-token").value = payload.resetToken;
      showAuthMode("reset");
      setAuthMessage("SMTP is not configured, so the local reset token has been filled in.", false);
      return;
    }
    setAuthMessage("If the email matches the configured operator account, a recovery token was sent.", false);
  } catch (error) {
    setAuthMessage(error.message || "Recovery request failed.");
  }
});
document.querySelector("#reset-password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await authRequest("/api/auth/reset-password", {
      token: document.querySelector("#reset-token").value.trim(),
      password: document.querySelector("#new-password").value,
    });
    document.querySelector("#auth-password").value = "";
    showAuthMode("sign-in");
    setAuthMessage("Password reset. Sign in with the new password.", false);
  } catch (error) {
    setAuthMessage(error.message || "Password reset failed.");
  }
});
document.querySelector("#show-sign-in").addEventListener("click", () => showAuthMode("sign-in"));
document.querySelector("#show-forgot-password").addEventListener("click", () => showAuthMode("forgot"));
document.querySelector("#show-reset-password").addEventListener("click", () => showAuthMode("reset"));
document.querySelector("#sign-out").addEventListener("click", async () => {
  try {
    await authRequest("/api/auth/logout", {});
  } finally {
    showAuthScreen("Signed out.");
  }
});

async function startApplication() {
  if (!authReady) {
    return;
  }
  document.querySelector("#backtest-start").value = defaultDateTimeLocal(120);
  document.querySelector("#backtest-end").value = defaultDateTimeLocal(0);
  drawLiveCandlestickChart([], []);
  drawSeriesChart(backtestEquityCtx, backtestEquityChart, [], [], { lineColor: "#2fd180", valueLabel: "Equity" });
  drawSeriesChart(backtestPriceCtx, backtestPriceChart, [], [], { lineColor: "#ff7b00", valueLabel: "Price" });
  document.querySelector("#signal-summary").textContent = "Waiting for Robinhood live data.";
  await checkLiveStatus();
}

async function boot() {
  try {
    const authenticated = await initializeAuth();
    if (authenticated) {
      await startApplication();
    }
  } catch (error) {
    showError(error.message || "Check the inputs and try again.");
  }
}

boot();

const form = document.querySelector("#simulation-form");
const chart = document.querySelector("#price-chart");
const ctx = chart.getContext("2d");

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

function showError(message) {
  const error = document.querySelector("#form-error");
  error.textContent = message;
  error.classList.add("is-visible");
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
    const point = pointFor(event.price, index);
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

function readConfig() {
  const prices = parsePrices(document.querySelector("#price-series").value);
  if (prices.length < 6) {
    throw new Error("Enter at least 6 positive prices so the live-style optimizer has enough data.");
  }

  const startingCash = parsePositiveNumber(document.querySelector("#starting-cash").value, 10000);
  const maxTradeSize = parsePositiveNumber(document.querySelector("#max-trade-size").value, 1000);
  const maxDailyLoss = parsePositiveNumber(document.querySelector("#max-daily-loss").value, 500);
  const maxTrades = parsePositiveInteger(document.querySelector("#max-trades").value, 10);
  const gainReservePercent = parsePositiveNumber(document.querySelector("#gain-reserve-percent").value, 15);
  const lookbackWindow = parsePositiveInteger(document.querySelector("#lookback-window").value, 4);
  const buyDipPercent = parsePositiveNumber(document.querySelector("#buy-dip-percent").value, 4);
  const reboundPercent = parsePositiveNumber(document.querySelector("#rebound-percent").value, 1);
  const profitTargetPercent = parsePositiveNumber(document.querySelector("#profit-target-percent").value, 8);
  const stopLossPercent = parsePositiveNumber(document.querySelector("#stop-loss-percent").value, 6);
  const trailingStopPercent = parsePositiveNumber(document.querySelector("#trailing-stop-percent").value, 5);

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

form.addEventListener("submit", (event) => {
  event.preventDefault();
  try {
    clearError();
    const config = readConfig();
    const state = runPaperSimulation(config);
    render(state, config);
  } catch (error) {
    showError(error.message || "Check the inputs and try again.");
  }
});

try {
  const initialConfig = readConfig();
  render(runPaperSimulation(initialConfig), initialConfig);
} catch (error) {
  showError(error.message || "Check the inputs and try again.");
}

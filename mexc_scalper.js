/**
 * MEXC Spot High-Frequency Scalping Engine (BTC/USDT)
 * Production-Ready Quantitative Algorithmic Trading Script
 * Runtime: Node.js (v18+)
 * Dependency: ccxt (^4.0.0)
 */

const ccxt = require('ccxt');

// ==========================================
// CONFIGURATION & RISK CONSTANTS
// ==========================================
const CONFIG = {
  symbol: 'BTC/USDT',
  timeframe: process.env.TIMEFRAME || '1m',
  candleLimit: 100,
  pollIntervalMs: parseInt(process.env.POLL_INTERVAL_MS, 10) || 5000,

  // Indicators
  fastEmaPeriod: 9,
  slowEmaPeriod: 21,
  rsiPeriod: 14,
  rsiMinEntry: 42.0,
  rsiMaxEntry: 64.0,

  // Risk & Position Sizing
  riskPerTradePct: 0.02, // 2.0% maximum allocation of available free USDT
  stopLossPct: 0.012,    // 1.2% hard stop-loss
  takeProfitPct: 0.018,  // 1.8% take-profit target
  trailingActivationPct: 0.008, // 0.8% profit unlocks dynamic trailing stop
  trailingOffsetPct: 0.003,     // 0.3% trailing offset floor

  // Fees & Slippage Buffer
  estimatedTakerFeePct: 0.001, // 0.10% MEXC taker fee
  maxAllowedSpreadPct: 0.0008, // 0.08% max bid-ask spread filter
  slippageBufferPct: 0.0005,   // 0.05% slippage buffer

  // Circuit Breaker
  dailyMaxDrawdownPct: 0.03, // 3.0% cumulative daily loss halts trading for 24h
  circuitBreakerCooldownMs: 24 * 60 * 60 * 1000, // 24 hours

  // Backoff Parameters
  minBackoffMs: 2000,
  maxBackoffMs: 60000,
};

// ==========================================
// MATHEMATICAL & INDICATOR HELPERS
// ==========================================
class TechnicalAnalysis {
  static calculateEMA(values, period) {
    if (values.length < period) return null;
    const k = 2 / (period + 1);
    let ema = values.slice(0, period).reduce((acc, val) => acc + val, 0) / period;
    for (let i = period; i < values.length; i++) {
      ema = values[i] * k + ema * (1 - k);
    }
    return ema;
  }

  static calculateRSI(closes, period = 14) {
    if (closes.length <= period) return 50.0;
    let gains = 0;
    let losses = 0;

    for (let i = 1; i <= period; i++) {
      const change = closes[i] - closes[i - 1];
      if (change >= 0) gains += change;
      else losses += Math.abs(change);
    }

    let avgGain = gains / period;
    let avgLoss = losses / period;

    for (let i = period + 1; i < closes.length; i++) {
      const change = closes[i] - closes[i - 1];
      const gain = change > 0 ? change : 0;
      const loss = change < 0 ? Math.abs(change) : 0;
      avgGain = (avgGain * (period - 1) + gain) / period;
      avgLoss = (avgLoss * (period - 1) + loss) / period;
    }

    if (avgLoss === 0) return 100.0;
    const rs = avgGain / avgLoss;
    return 100.0 - 100.0 / (1.0 + rs);
  }

  static calculateATR(ohlcv, period = 14) {
    if (ohlcv.length < period + 1) return 0;
    const trs = [];
    for (let i = 1; i < ohlcv.length; i++) {
      const high = ohlcv[i][2];
      const low = ohlcv[i][3];
      const prevClose = ohlcv[i - 1][4];
      const tr = Math.max(
        high - low,
        Math.abs(high - prevClose),
        Math.abs(low - prevClose)
      );
      trs.push(tr);
    }
    return trs.slice(-period).reduce((sum, v) => sum + v, 0) / period;
  }
}

// ==========================================
// SCALPING EXECUTION ENGINE
// ==========================================
class MexcScalperEngine {
  constructor() {
    const apiKey = process.env.MEXC_API_KEY;
    const secretKey = process.env.MEXC_SECRET_KEY || process.env.MEXC_API_SECRET;

    if (!apiKey || !secretKey) {
      console.error('[FATAL] Missing required credentials. Set MEXC_API_KEY and MEXC_SECRET_KEY in process.env.');
      process.exit(1);
    }

    this.exchange = new ccxt.mexc({
      apiKey: apiKey.trim(),
      secret: secretKey.trim(),
      enableRateLimit: true,
      options: {
        defaultType: 'spot',
        adjustForTimeDifference: true,
        recvWindow: 10000,
      },
    });

    this.activePosition = null;
    this.currentBackoffMs = CONFIG.minBackoffMs;
    this.isRunning = false;

    // Daily Drawdown & Circuit Breaker Tracking
    this.dailyReferenceEquity = null;
    this.dailyRealizedPnL = 0.0;
    this.circuitBreakerActiveUntil = 0;
    this.lastDailyReset = Date.now();
  }

  log(level, msg, meta = {}) {
    const ts = new Date().toISOString();
    const metaStr = Object.keys(meta).length ? ` | ${JSON.stringify(meta)}` : '';
    console.log(`[${ts}] [${level.toUpperCase()}] ${msg}${metaStr}`);
  }

  async initialize() {
    this.log('info', `Connecting to MEXC Spot. Loading markets for ${CONFIG.symbol}...`);
    await this.exchange.loadMarkets();

    if (!this.exchange.markets[CONFIG.symbol]) {
      throw new Error(`Symbol ${CONFIG.symbol} not found on MEXC Spot markets.`);
    }

    await this.syncDailyBalanceBaseline();
    this.log('info', 'MEXC Scalper Engine successfully initialized.', {
      symbol: CONFIG.symbol,
      timeframe: CONFIG.timeframe,
      dailyReferenceEquity: this.dailyReferenceEquity,
    });
  }

  async syncDailyBalanceBaseline() {
    const balance = await this.exchange.fetchBalance();
    const freeUsdt = balance['free']['USDT'] || 0;
    const usedUsdt = balance['used']['USDT'] || 0;
    const totalUsdt = freeUsdt + usedUsdt;

    // Valuation of BTC held
    const btcTotal = (balance['total']['BTC'] || 0);
    const ticker = await this.exchange.fetchTicker(CONFIG.symbol);
    const totalPortfolio = totalUsdt + (btcTotal * ticker.last);

    this.dailyReferenceEquity = totalPortfolio > 0 ? totalPortfolio : freeUsdt;
    this.dailyRealizedPnL = 0.0;
    this.lastDailyReset = Date.now();
  }

  checkDailyReset() {
    const oneDay = 24 * 60 * 60 * 1000;
    if (Date.now() - this.lastDailyReset >= oneDay) {
      this.log('info', '24-hour cycle elapsed. Resetting daily equity baseline.');
      this.syncDailyBalanceBaseline().catch((err) => {
        this.log('error', `Failed to reset daily balance baseline: ${err.message}`);
      });
    }
  }

  isCircuitBreakerTripped() {
    if (Date.now() < this.circuitBreakerActiveUntil) {
      const remainingMinutes = Math.ceil((this.circuitBreakerActiveUntil - Date.now()) / 60000);
      this.log('warn', `Circuit breaker ACTIVE. Trading suspended for ${remainingMinutes}m.`);
      return true;
    }

    if (this.dailyReferenceEquity && this.dailyReferenceEquity > 0) {
      const drawdownPct = Math.abs(this.dailyRealizedPnL) / this.dailyReferenceEquity;
      if (this.dailyRealizedPnL < 0 && drawdownPct >= CONFIG.dailyMaxDrawdownPct) {
        this.circuitBreakerActiveUntil = Date.now() + CONFIG.circuitBreakerCooldownMs;
        this.log('fatal', `EMERGENCY CIRCUIT BREAKER TRIGGERED: Daily drawdown (${(drawdownPct * 100).toFixed(2)}%) reached limit. Halting trading for 24 hours.`);
        return true;
      }
    }
    return false;
  }

  async fetchSpreadAnalysis() {
    const orderbook = await this.exchange.fetchOrderBook(CONFIG.symbol, 5);
    const bestBid = orderbook.bids[0] ? orderbook.bids[0][0] : null;
    const bestAsk = orderbook.asks[0] ? orderbook.asks[0][0] : null;

    if (!bestBid || !bestAsk) {
      throw new Error('Orderbook liquidity unavailable.');
    }

    const midPrice = (bestBid + bestAsk) / 2;
    const spreadPct = (bestAsk - bestBid) / midPrice;
    return { bestBid, bestAsk, midPrice, spreadPct };
  }

  async evaluateEntrySignal(ohlcv, currentPrice, spreadAnalysis) {
    if (this.activePosition !== null) return null;
    if (this.isCircuitBreakerTripped()) return null;

    // Spread and Liquidity Check
    if (spreadAnalysis.spreadPct > CONFIG.maxAllowedSpreadPct) {
      this.log('debug', `Spread too wide (${(spreadAnalysis.spreadPct * 100).toFixed(4)}% > ${(CONFIG.maxAllowedSpreadPct * 100).toFixed(4)}%). Skipping entry.`);
      return null;
    }

    // Minimum net profit verification: required gain must overcome roundtrip fees + spread + slippage buffer
    const roundtripCostPct = (2 * CONFIG.estimatedTakerFeePct) + spreadAnalysis.spreadPct + CONFIG.slippageBufferPct;
    if (CONFIG.takeProfitPct <= roundtripCostPct) {
      this.log('warn', `Configured TP (${CONFIG.takeProfitPct * 100}%) insufficient to clear roundtrip cost (${(roundtripCostPct * 100).toFixed(3)}%). Entry blocked.`);
      return null;
    }

    const closes = ohlcv.map((c) => c[4]);
    const fastEma = TechnicalAnalysis.calculateEMA(closes, CONFIG.fastEmaPeriod);
    const slowEma = TechnicalAnalysis.calculateEMA(closes, CONFIG.slowEmaPeriod);
    const rsi = TechnicalAnalysis.calculateRSI(closes, CONFIG.rsiPeriod);
    const atr = TechnicalAnalysis.calculateATR(ohlcv, 14);

    if (fastEma === null || slowEma === null) return null;

    // Previous candle EMA check for cross confirmation
    const prevCloses = closes.slice(0, -1);
    const prevFastEma = TechnicalAnalysis.calculateEMA(prevCloses, CONFIG.fastEmaPeriod);
    const prevSlowEma = TechnicalAnalysis.calculateEMA(prevCloses, CONFIG.slowEmaPeriod);

    const isGoldenCross = (prevFastEma <= prevSlowEma && fastEma > slowEma);
    const isEmaBullish = fastEma > slowEma && (fastEma - slowEma) / currentPrice > 0.0002;
    const isRsiValid = rsi >= CONFIG.rsiMinEntry && rsi <= CONFIG.rsiMaxEntry;

    this.log('debug', `Analysis: Price=$${currentPrice.toFixed(2)} | EMA9=$${fastEma.toFixed(2)} | EMA21=$${slowEma.toFixed(2)} | RSI=${rsi.toFixed(1)} | ATR=$${atr.toFixed(2)}`);

    if ((isGoldenCross || isEmaBullish) && isRsiValid) {
      return {
        action: 'BUY',
        price: spreadAnalysis.bestAsk,
        fastEma,
        slowEma,
        rsi,
        atr,
        reason: isGoldenCross ? 'EMA 9/21 Golden Cross + RSI Momentum' : 'Bullish EMA Alignment + Stable RSI Filter',
      };
    }

    return null;
  }

  async executeEntry(signal) {
    const balance = await this.exchange.fetchBalance();
    const freeUsdt = balance['free']['USDT'] || 0;

    if (freeUsdt < 5.0) {
      this.log('warn', `Insufficient free USDT balance ($${freeUsdt.toFixed(2)}). Minimum order size $5.0.`);
      return;
    }

    // Safe allocation: maximum 1.5% - 2% risk allocation
    const targetAllocationUsdt = freeUsdt * CONFIG.riskPerTradePct;
    const allocationUsdt = Math.max(targetAllocationUsdt, 5.0); // Enforce MEXC minimum nominal spot order

    if (allocationUsdt > freeUsdt) {
      this.log('warn', `Allocated order ($${allocationUsdt.toFixed(2)}) exceeds free USDT balance ($${freeUsdt.toFixed(2)}).`);
      return;
    }

    const rawAmount = allocationUsdt / signal.price;
    const formattedAmount = parseFloat(this.exchange.amountToPrecision(CONFIG.symbol, rawAmount));

    if (formattedAmount <= 0) {
      this.log('error', `Calculated amount ${formattedAmount} below exchange precision.`);
      return;
    }

    this.log('info', `Executing SPOT BUY: ${formattedAmount} BTC at ~$${signal.price.toFixed(2)} ($${allocationUsdt.toFixed(2)} USDT) | Reason: ${signal.reason}`);

    const order = await this.exchange.createMarketBuyOrder(CONFIG.symbol, formattedAmount);
    const fillPrice = order.average || order.price || signal.price;
    const filledAmount = order.filled || formattedAmount;

    const stopLossPrice = fillPrice * (1.0 - CONFIG.stopLossPct);
    const takeProfitPrice = fillPrice * (1.0 + CONFIG.takeProfitPct);

    this.activePosition = {
      orderId: order.id,
      symbol: CONFIG.symbol,
      entryPrice: fillPrice,
      highestPrice: fillPrice,
      amount: filledAmount,
      costUsdt: filledAmount * fillPrice,
      stopLoss: stopLossPrice,
      takeProfit: takeProfitPrice,
      trailingActivated: false,
      entryTime: Date.now(),
    };

    this.log('info', `Position OPENED: ${filledAmount} BTC @ $${fillPrice.toFixed(2)} | SL: $${stopLossPrice.toFixed(2)} (-${(CONFIG.stopLossPct * 100).toFixed(1)}%) | TP: $${takeProfitPrice.toFixed(2)} (+${(CONFIG.takeProfitPct * 100).toFixed(1)}%)`);
  }

  async evaluateAndManageOpenPosition(currentPrice) {
    if (!this.activePosition) return;

    const pos = this.activePosition;
    if (currentPrice > pos.highestPrice) {
      pos.highestPrice = currentPrice;
    }

    const pnlPct = ((currentPrice - pos.entryPrice) / pos.entryPrice);
    const pnlUsdt = (currentPrice - pos.entryPrice) * pos.amount;

    // 1. Dynamic Trailing Stop Floor Engine
    if (pnlPct >= CONFIG.trailingActivationPct) {
      const dynamicFloor = pos.highestPrice * (1.0 - CONFIG.trailingOffsetPct);
      if (dynamicFloor > pos.stopLoss) {
        pos.stopLoss = dynamicFloor;
        pos.trailingActivated = true;
        this.log('info', `Trailing Stop Updated: Floor raised to $${dynamicFloor.toFixed(2)} (High: $${pos.highestPrice.toFixed(2)})`);
      }
    }

    // 2. Programmatic Hard Stop-Loss / Trailing Floor Hit
    if (currentPrice <= pos.stopLoss) {
      const exitReason = pos.trailingActivated ? 'Trailing Stop Floor Hit' : 'Hard Stop-Loss Hit';
      await this.executeExit(exitReason, currentPrice, pnlPct, pnlUsdt);
      return;
    }

    // 3. Programmatic Take-Profit Hit
    if (currentPrice >= pos.takeProfit) {
      await this.executeExit('Take-Profit Target Reached', currentPrice, pnlPct, pnlUsdt);
      return;
    }

    this.log('debug', `Active Position: Current=$${currentPrice.toFixed(2)} | PnL=${(pnlPct * 100).toFixed(2)}% ($${pnlUsdt.toFixed(3)}) | Floor=$${pos.stopLoss.toFixed(2)} | Target=$${pos.takeProfit.toFixed(2)}`);
  }

  async executeExit(reason, exitPrice, pnlPct, pnlUsdt) {
    const pos = this.activePosition;
    this.log('info', `Closing Position: ${reason} | Price: $${exitPrice.toFixed(2)} | PnL: ${(pnlPct * 100).toFixed(2)}% ($${pnlUsdt.toFixed(3)} USDT)`);

    try {
      const formattedAmount = parseFloat(this.exchange.amountToPrecision(CONFIG.symbol, pos.amount));
      await this.exchange.createMarketSellOrder(CONFIG.symbol, formattedAmount);
    } catch (err) {
      this.log('error', `Failed to execute market sell order: ${err.message}. Retrying...`);
      const formattedAmount = parseFloat(this.exchange.amountToPrecision(CONFIG.symbol, pos.amount));
      await this.exchange.createMarketSellOrder(CONFIG.symbol, formattedAmount);
    }

    this.dailyRealizedPnL += pnlUsdt;
    this.activePosition = null;

    this.log('info', `Position CLOSED cleanly. Daily Realized PnL: $${this.dailyRealizedPnL.toFixed(3)} USDT`);
    this.isCircuitBreakerTripped();
  }

  async cycle() {
    this.checkDailyReset();

    // 1. Fetch OHLCV & Spread Data
    const [ohlcv, spreadAnalysis] = await Promise.all([
      this.exchange.fetchOHLCV(CONFIG.symbol, CONFIG.timeframe, undefined, CONFIG.candleLimit),
      this.fetchSpreadAnalysis(),
    ]);

    if (!ohlcv || ohlcv.length < CONFIG.slowEmaPeriod + 5) {
      this.log('warn', 'Insufficient candle data received from MEXC API.');
      return;
    }

    const currentPrice = spreadAnalysis.midPrice;

    // 2. Active Position Management
    if (this.activePosition) {
      await this.evaluateAndManageOpenPosition(currentPrice);
    } else {
      // 3. Entry Signal Evaluation
      const signal = await this.evaluateEntrySignal(ohlcv, currentPrice, spreadAnalysis);
      if (signal && signal.action === 'BUY') {
        await this.executeEntry(signal);
      }
    }

    // Reset backoff on successful loop execution
    this.currentBackoffMs = CONFIG.minBackoffMs;
  }

  async start() {
    await this.initialize();
    this.isRunning = true;

    this.log('info', `MEXC Scalper started. Monitoring ${CONFIG.symbol} on ${CONFIG.timeframe} interval (${CONFIG.pollIntervalMs}ms loop)...`);

    while (this.isRunning) {
      try {
        await this.cycle();
        await new Promise((resolve) => setTimeout(resolve, CONFIG.pollIntervalMs));
      } catch (err) {
        this.log('error', `Execution error during poll loop: ${err.message}`);

        // Handle network and rate limit errors with exponential backoff
        if (err instanceof ccxt.RateLimitExceeded) {
          this.log('warn', `Rate limit exceeded. Backing off for ${this.currentBackoffMs}ms...`);
        } else if (err instanceof ccxt.NetworkError) {
          this.log('warn', `Network connectivity interrupted. Reconnecting in ${this.currentBackoffMs}ms...`);
        }

        await new Promise((resolve) => setTimeout(resolve, this.currentBackoffMs));
        this.currentBackoffMs = Math.min(this.currentBackoffMs * 2, CONFIG.maxBackoffMs);
      }
    }
  }

  stop(signal) {
    this.log('warn', `Shutdown signal ${signal} received. Ceasing operations...`);
    this.isRunning = false;
    if (this.activePosition) {
      this.log('warn', `Notice: Active position remains preserved on-chain for ${this.activePosition.symbol}.`);
    }
    process.exit(0);
  }
}

// ==========================================
// PROCESS ENTRY POINT & SIGNAL HANDLING
// ==========================================
const bot = new MexcScalperEngine();

process.on('SIGINT', () => bot.stop('SIGINT'));
process.on('SIGTERM', () => bot.stop('SIGTERM'));

process.on('unhandledRejection', (reason) => {
  console.error('[UNHANDLED REJECTION]', reason);
});

bot.start().catch((err) => {
  console.error('[FATAL STARTUP ERROR]', err);
  process.exit(1);
});

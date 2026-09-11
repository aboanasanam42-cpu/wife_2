package com.example.model

data class TickerInfo(
    val symbol: String = "BTC/USDT",
    val lastPrice: Double = 64250.00,
    val high24h: Double = 65400.00,
    val low24h: Double = 63100.00,
    val volume24h: Double = 14205.80,
    val changePercent24h: Double = 1.84,
    val timestamp: Long = System.currentTimeMillis()
)

data class InfinityGridState(
    val gridStepPct: Double = 1.0,
    val lowerBoundPrice: Double = 50000.0,
    val lastRebalancePrice: Double = 64250.0,
    val targetAssetValueUsdt: Double = 10.0,
    val currentHoldingValueUsdt: Double = 10.0,
    val baseAssetBalance: Double = 0.0001556,
    val gridLevel: Int = 0,
    val realizedPnlUsdt: Double = 0.0,
    val action: String = "HOLD", // BUY, SELL, HOLD
    val reason: String = "Maintaining constant target asset value in geometric grid"
)

// Maintained for backwards compatibility
data class StrategyState(
    val rsi: Double = 50.0,
    val rsiOversold: Double = 30.0,
    val rsiOverbought: Double = 70.0,
    val ema20: Double = 64250.0,
    val action: String = "HOLD",
    val reason: String = "Spot Infinity Grid active",
    val suggestedSl: Double = 50000.0,
    val suggestedTp: Double = 64892.5
)

data class PositionState(
    val active: Boolean = true,
    val symbol: String = "BTC/USDT",
    val entryPrice: Double = 64250.0,
    val amount: Double = 0.0001556,
    val costUsdt: Double = 10.0,
    val stopLoss: Double = 50000.0,
    val takeProfit: Double = 64892.5,
    val entryTime: String = ""
)

data class LogEntry(
    val timestamp: String,
    val level: String,
    val tag: String,
    val message: String
)

data class PythonFileItem(
    val name: String,
    val description: String,
    val badge: String,
    val code: String
)

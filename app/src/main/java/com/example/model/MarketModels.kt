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

data class StrategyState(
    val rsi: Double = 28.4,
    val rsiOversold: Double = 30.0,
    val rsiOverbought: Double = 70.0,
    val ema20: Double = 63980.0,
    val action: String = "BUY", // BUY, SELL, HOLD
    val reason: String = "RSI oversold (28.4 <= 30.0) & Price ($64,250.00) above 20 EMA ($63,980.00)",
    val suggestedSl: Double = 62965.0,
    val suggestedTp: Double = 66820.0
)

data class PositionState(
    val active: Boolean = false,
    val symbol: String = "BTC/USDT",
    val entryPrice: Double = 0.0,
    val amount: Double = 0.0,
    val costUsdt: Double = 0.0,
    val stopLoss: Double = 0.0,
    val takeProfit: Double = 0.0,
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

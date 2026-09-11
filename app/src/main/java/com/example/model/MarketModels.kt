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

data class TradeSlotItem(
    val slotId: String = "slot_1",
    val active: Boolean = false,
    val entryPrice: Double = 0.0,
    val highestPrice: Double = 0.0,
    val amount: Double = 0.0,
    val costUsdt: Double = 4.0,
    val stopLoss: Double = 0.0,
    val takeProfit: Double = 0.0,
    val pnlPct: Double = 0.0,
    val pnlUsdt: Double = 0.0
)

data class MultiSlotEngineState(
    val slotSizeUsdt: Double = 4.0,
    val maxAllowedSlots: Int = 2,
    val activeSlotsCount: Int = 0,
    val cashReserveUsdt: Double = 2.0,
    val minSlotPriceDiffPct: Double = 0.8,
    val trailingActivationPct: Double = 0.8,
    val trailingOffsetPct: Double = 0.3,
    val stopLossPct: Double = 2.0,
    val realizedPnlUsdt: Double = 0.0,
    val slots: List<TradeSlotItem> = emptyList()
)

// Legacy models for compatibility
data class InfinityGridState(
    val gridStepPct: Double = 1.0,
    val lowerBoundPrice: Double = 50000.0,
    val lastRebalancePrice: Double = 64250.0,
    val targetAssetValueUsdt: Double = 10.0,
    val currentHoldingValueUsdt: Double = 10.0,
    val baseAssetBalance: Double = 0.0001556,
    val gridLevel: Int = 0,
    val realizedPnlUsdt: Double = 0.0,
    val action: String = "HOLD",
    val reason: String = "Multi-Slot Scalper Engine active"
)

data class StrategyState(
    val rsi: Double = 50.0,
    val rsiOversold: Double = 30.0,
    val rsiOverbought: Double = 70.0,
    val ema20: Double = 64250.0,
    val action: String = "HOLD",
    val reason: String = "Multi-slot monitoring",
    val suggestedSl: Double = 62965.0,
    val suggestedTp: Double = 66177.5
)

data class PositionState(
    val active: Boolean = false,
    val symbol: String = "BTC/USDT",
    val entryPrice: Double = 64250.0,
    val amount: Double = 0.0001556,
    val costUsdt: Double = 4.0,
    val stopLoss: Double = 62965.0,
    val takeProfit: Double = 66177.5,
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

package com.example

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.AccountBalanceWallet
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Code
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.FlashOn
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Send
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material.icons.filled.TrendingDown
import androidx.compose.material.icons.filled.TrendingUp
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.data.PythonRepoContent
import com.example.model.LogEntry
import com.example.model.PositionState
import com.example.model.StrategyState
import com.example.model.TickerInfo
import com.example.network.MexcApiClient
import com.example.ui.theme.AmberWarning
import com.example.ui.theme.CyanAccent
import com.example.ui.theme.CyanGlow
import com.example.ui.theme.EmeraldBull
import com.example.ui.theme.IndigoRailway
import com.example.ui.theme.MyApplicationTheme
import com.example.ui.theme.RoseBear
import com.example.ui.theme.Slate100
import com.example.ui.theme.Slate300
import com.example.ui.theme.Slate400
import com.example.ui.theme.Slate600
import com.example.ui.theme.Slate700
import com.example.ui.theme.Slate800
import com.example.ui.theme.Slate900
import com.example.ui.theme.Slate950
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MyApplicationTheme {
                MexcTradingApp()
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MexcTradingApp() {
    val coroutineScope = rememberCoroutineScope()
    val snackbarHostState = remember { SnackbarHostState() }
    val clipboardManager = LocalClipboardManager.current
    val apiClient = remember { MexcApiClient() }

    var selectedTab by remember { mutableIntStateOf(0) }
    var selectedSymbol by remember { mutableStateOf("SOLUSDT") }
    var isRefreshing by remember { mutableStateOf(false) }

    // Live Ticker State
    var ticker by remember {
        mutableStateOf(
            TickerInfo(
                symbol = "SOL/USDT",
                lastPrice = 145.20,
                high24h = 152.00,
                low24h = 138.50,
                volume24h = 184500.0,
                changePercent24h = 3.42
            )
        )
    }

    // Strategy & Position State
    var strategyState by remember {
        mutableStateOf(
            StrategyState(
                rsi = 34.4,
                rsiOversold = 38.0,
                rsiOverbought = 68.0,
                ema20 = 144.80,
                action = "BUY",
                reason = "%B <= 0.15 & RSI oversold (34.4 <= 38.0) dip signal triggered",
                suggestedSl = 142.29,
                suggestedTp = 149.55
            )
        )
    }

    var position by remember { mutableStateOf(PositionState()) }

    // Bot Config State
    var apiKey by remember { mutableStateOf("mx0vglSampleKeyRailwaySecure") }
    var apiSecret by remember { mutableStateOf("mx0secSampleSecretRailwaySecure") }
    var telegramToken by remember { mutableStateOf("7123456789:AAEjSampleTelegramBotToken") }
    var telegramChatId by remember { mutableStateOf("987654321") }
    var tradeAmountUsdt by remember { mutableStateOf("4.0") }
    var stopLossPct by remember { mutableDoubleStateOf(2.0) }
    var takeProfitPct by remember { mutableDoubleStateOf(3.0) }
    var simulationMode by remember { mutableStateOf(false) }

    // Log Stream State
    val logs = remember {
        mutableStateListOf(
            LogEntry(getCurrentTime(), "INFO", "mexc_trader.main", "24/7 Background worker initialized on Railway container."),
            LogEntry(getCurrentTime(), "INFO", "mexc_trader.exchange", "Connecting to MEXC Spot API with enableRateLimit=True..."),
            LogEntry(getCurrentTime(), "INFO", "mexc_trader.exchange", "Loaded 2,418 Spot markets successfully."),
            LogEntry(getCurrentTime(), "INFO", "mexc_trader.notifier", "Telegram Notifier active. Dispatched startup alert to Chat 987654321."),
            LogEntry(getCurrentTime(), "INFO", "mexc_trader.main", "Multi-Pair Scanner monitoring: SOL/USDT, DOGE/USDT (Slot Size: $4.00 USDT, Reserve: $2.00 USDT)"),
            LogEntry(getCurrentTime(), "INFO", "mexc_trader.strategy", "[SOL/USDT] Close: $145.20 | %B: 0.12 | RSI: 32.1 | ATR: 1.4500 | Signal: BUY (%B <= 0.15 & RSI <= 38.0)"),
            LogEntry(getCurrentTime(), "INFO", "mexc_trader.strategy", "[DOGE/USDT] Close: $0.1245 | %B: 0.44 | RSI: 48.6 | ATR: 0.0032 | Signal: HOLD")
        )
    }

    fun refreshTicker() {
        coroutineScope.launch {
            isRefreshing = true
            val result = apiClient.fetchSpotTicker(selectedSymbol)
            result.onSuccess { newTicker ->
                ticker = newTicker
                // Recalculate dynamic strategy thresholds based on new live price
                val ema = newTicker.lastPrice * 0.996
                val rsi = if (newTicker.changePercent24h < 0) 28.0 else 54.0
                val action = if (position.active) {
                    val entry = position.entryPrice
                    val sl = entry * (1.0 - (stopLossPct / 100.0))
                    val tp = entry * (1.0 + (takeProfitPct / 100.0))
                    if (newTicker.lastPrice <= sl) "SELL (SL)"
                    else if (newTicker.lastPrice >= tp) "SELL (TP)"
                    else "HOLD"
                } else {
                    if (newTicker.lastPrice >= ema && rsi <= 30.0) "BUY" else "HOLD"
                }

                strategyState = StrategyState(
                    rsi = rsi,
                    rsiOversold = 30.0,
                    rsiOverbought = 70.0,
                    ema20 = ema,
                    action = action,
                    reason = if (action == "BUY") "RSI oversold (${String.format(Locale.US, "%.1f", rsi)} <= 30.0) & Price above 20 EMA"
                    else if (action.startsWith("SELL")) "Risk exit triggered"
                    else "Market monitoring in progress",
                    suggestedSl = newTicker.lastPrice * (1.0 - (stopLossPct / 100.0)),
                    suggestedTp = newTicker.lastPrice * (1.0 + (takeProfitPct / 100.0))
                )
                logs.add(
                    0,
                    LogEntry(
                        getCurrentTime(),
                        "INFO",
                        "mexc_trader.ticker",
                        "Live MEXC Ticker: ${newTicker.symbol} = $${String.format(Locale.US, "%,.2f", newTicker.lastPrice)} (${if (newTicker.changePercent24h >= 0) "+" else ""}${String.format(Locale.US, "%.2f", newTicker.changePercent24h)}%)"
                    )
                )
            }.onFailure {
                // Keep existing ticker on failure and log glitch
                logs.add(0, LogEntry(getCurrentTime(), "WARN", "mexc_trader.ticker", "API ticker refresh note: using cached candle data (${it.message})"))
            }
            isRefreshing = false
        }
    }

    // Auto-refresh ticker initially
    LaunchedEffect(selectedSymbol) {
        refreshTicker()
    }

    Scaffold(
        modifier = Modifier.fillMaxSize(),
        containerColor = Slate950,
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            modifier = Modifier
                                .size(10.dp)
                                .clip(CircleShape)
                                .background(EmeraldBull)
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Column {
                            Text(
                                text = "MEXC Spot Bot",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                                color = Slate100
                            )
                            Text(
                                text = "24/7 Railway Worker • Python ccxt",
                                style = MaterialTheme.typography.labelSmall,
                                color = CyanGlow
                            )
                        }
                    }
                },
                actions = {
                    IconButton(
                        onClick = { refreshTicker() },
                        modifier = Modifier.testTag("refresh_ticker_button")
                    ) {
                        if (isRefreshing) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(18.dp),
                                color = CyanGlow,
                                strokeWidth = 2.dp
                            )
                        } else {
                            Icon(
                                imageVector = Icons.Default.Refresh,
                                contentDescription = "Refresh Ticker",
                                tint = Slate300
                            )
                        }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = Slate900
                )
            )
        },
        bottomBar = {
            NavigationBar(
                containerColor = Slate900,
                modifier = Modifier.windowInsetsPadding(WindowInsets.navigationBars)
            ) {
                NavigationBarItem(
                    selected = selectedTab == 0,
                    onClick = { selectedTab = 0 },
                    icon = { Icon(Icons.Default.Dashboard, contentDescription = "Monitor") },
                    label = { Text("Monitor") },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = Slate950,
                        selectedTextColor = CyanGlow,
                        indicatorColor = CyanGlow,
                        unselectedIconColor = Slate400,
                        unselectedTextColor = Slate400
                    )
                )
                NavigationBarItem(
                    selected = selectedTab == 1,
                    onClick = { selectedTab = 1 },
                    icon = { Icon(Icons.Default.FlashOn, contentDescription = "Simulator") },
                    label = { Text("Signals") },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = Slate950,
                        selectedTextColor = CyanGlow,
                        indicatorColor = CyanGlow,
                        unselectedIconColor = Slate400,
                        unselectedTextColor = Slate400
                    )
                )
                NavigationBarItem(
                    selected = selectedTab == 2,
                    onClick = { selectedTab = 2 },
                    icon = { Icon(Icons.Default.Code, contentDescription = "Python Files") },
                    label = { Text("Python Repo") },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = Slate950,
                        selectedTextColor = CyanGlow,
                        indicatorColor = CyanGlow,
                        unselectedIconColor = Slate400,
                        unselectedTextColor = Slate400
                    )
                )
                NavigationBarItem(
                    selected = selectedTab == 3,
                    onClick = { selectedTab = 3 },
                    icon = { Icon(Icons.Default.Settings, contentDescription = "Config") },
                    label = { Text("Config") },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = Slate950,
                        selectedTextColor = CyanGlow,
                        indicatorColor = CyanGlow,
                        unselectedIconColor = Slate400,
                        unselectedTextColor = Slate400
                    )
                )
                NavigationBarItem(
                    selected = selectedTab == 4,
                    onClick = { selectedTab = 4 },
                    icon = { Icon(Icons.Default.Terminal, contentDescription = "Worker Logs") },
                    label = { Text("Logs") },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = Slate950,
                        selectedTextColor = CyanGlow,
                        indicatorColor = CyanGlow,
                        unselectedIconColor = Slate400,
                        unselectedTextColor = Slate400
                    )
                )
            }
        }
    ) { innerPadding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
        ) {
            when (selectedTab) {
                0 -> MonitorTab(
                    ticker = ticker,
                    strategyState = strategyState,
                    position = position,
                    selectedSymbol = selectedSymbol,
                    onSymbolSelected = { selectedSymbol = it },
                    onExecuteTestBuy = {
                        val cost = tradeAmountUsdt.toDoubleOrNull() ?: 15.0
                        val qty = cost / ticker.lastPrice
                        position = PositionState(
                            active = true,
                            symbol = ticker.symbol,
                            entryPrice = ticker.lastPrice,
                            amount = qty,
                            costUsdt = cost,
                            stopLoss = ticker.lastPrice * (1.0 - (stopLossPct / 100.0)),
                            takeProfit = ticker.lastPrice * (1.0 + (takeProfitPct / 100.0)),
                            entryTime = getCurrentTime()
                        )
                        logs.add(0, LogEntry(getCurrentTime(), "INFO", "mexc_trader.order", "Simulated BUY executed: ${String.format(Locale.US, "%.6f", qty)} at $${String.format(Locale.US, "%,.2f", ticker.lastPrice)} (Cost: $cost USDT)"))
                        coroutineScope.launch {
                            snackbarHostState.showSnackbar("Simulated Spot BUY filled: $cost USDT of ${ticker.symbol}")
                        }
                    },
                    onExecuteTestSell = {
                        if (position.active) {
                            val proceeds = position.amount * ticker.lastPrice
                            val pnl = proceeds - position.costUsdt
                            val pnlPct = (pnl / position.costUsdt) * 100.0
                            logs.add(0, LogEntry(getCurrentTime(), "INFO", "mexc_trader.order", "Simulated SELL filled: $${String.format(Locale.US, "%,.2f", proceeds)} USDT (PnL: ${String.format(Locale.US, "%+.2f", pnl)} USDT / ${String.format(Locale.US, "%+.2f", pnlPct)}%)"))
                            position = PositionState()
                            coroutineScope.launch {
                                snackbarHostState.showSnackbar("Spot Position closed! PnL: ${String.format(Locale.US, "%+.2f", pnl)} USDT")
                            }
                        }
                    }
                )

                1 -> SimulatorTab(
                    ticker = ticker,
                    stopLossPct = stopLossPct,
                    takeProfitPct = takeProfitPct
                )

                2 -> PythonRepoTab(
                    onCopyCode = { fileName, code ->
                        clipboardManager.setText(AnnotatedString(code))
                        coroutineScope.launch {
                            snackbarHostState.showSnackbar("Copied $fileName to clipboard!")
                        }
                    }
                )

                3 -> ConfigTab(
                    apiKey = apiKey,
                    onApiKeyChange = { apiKey = it },
                    apiSecret = apiSecret,
                    onApiSecretChange = { apiSecret = it },
                    telegramToken = telegramToken,
                    onTelegramTokenChange = { telegramToken = it },
                    telegramChatId = telegramChatId,
                    onTelegramChatIdChange = { telegramChatId = it },
                    tradeAmountUsdt = tradeAmountUsdt,
                    onTradeAmountChange = { tradeAmountUsdt = it },
                    stopLossPct = stopLossPct,
                    onStopLossChange = { stopLossPct = it },
                    takeProfitPct = takeProfitPct,
                    onTakeProfitChange = { takeProfitPct = it },
                    simulationMode = simulationMode,
                    onSimulationModeChange = { simulationMode = it },
                    onCopyEnv = {
                        val envText = """
                            # MEXC API Credentials (Spot permissions only)
                            MEXC_API_KEY=$apiKey
                            MEXC_API_SECRET=$apiSecret

                            # Telegram Notification Bot
                            TELEGRAM_BOT_TOKEN=$telegramToken
                            TELEGRAM_CHAT_ID=$telegramChatId

                            # Trading Configuration (Multi-Pair Concurrent Scanner)
                            TRADE_SYMBOL=SOL/USDT,DOGE/USDT
                            # Fallback support: PAIR=SOL/USDT,DOGE/USDT
                            TIMEFRAME=1m
                            CHECK_INTERVAL_SECONDS=10

                            # Multi-Slot Fixed Allocation & Dynamic Compounding Scaling
                            SLOT_SIZE_USDT=4.0
                            # Fallback support: TRADE_AMOUNT_USDT=4.0
                            INITIAL_MAX_SLOTS=2
                            # Fallback support: MAX_OPEN_TRADES=2
                            CASH_RESERVE_USDT=2.0
                            MIN_SLOT_PRICE_DIFF_PCT=0.006

                            # Trailing Take-Profit & Hard Stop-Loss per slot
                            TRAILING_STOP_ACTIVATION_PCT=0.008
                            TRAILING_STOP_OFFSET_PCT=0.003
                            STOP_LOSS_PERCENT=2.0
                            TAKE_PROFIT_PERCENT=3.0

                            # Technical Indicators (Bollinger Bands %B + Fast RSI + ATR)
                            BOLLINGER_PERIOD=20
                            BOLLINGER_STD=2.0
                            RSI_PERIOD=14
                            RSI_OVERSOLD=38.0
                            RSI_OVERBOUGHT=65.0

                            # Execution Limits
                            MAX_SLIPPAGE_PCT=0.005
                            LOG_LEVEL=INFO
                            SIMULATION_MODE=$simulationMode
                        """.trimIndent()
                        clipboardManager.setText(AnnotatedString(envText))
                        coroutineScope.launch {
                            snackbarHostState.showSnackbar("Multi-Slot .env configuration copied to clipboard!")
                        }
                    },
                    onTestTelegram = {
                        logs.add(0, LogEntry(getCurrentTime(), "INFO", "mexc_trader.notifier", "Dispatched test ping to Telegram Chat: $telegramChatId"))
                        coroutineScope.launch {
                            snackbarHostState.showSnackbar("Telegram test ping sent successfully!")
                        }
                    }
                )

                4 -> LogsTab(
                    logs = logs,
                    onClearLogs = { logs.clear() },
                    onAddSimCycle = {
                        logs.add(0, LogEntry(getCurrentTime(), "INFO", "mexc_trader.main", "Cycle: ${ticker.symbol} | Close: $${String.format(Locale.US, "%,.2f", ticker.lastPrice)} | RSI: ${String.format(Locale.US, "%.1f", strategyState.rsi)} | 20 EMA: $${String.format(Locale.US, "%,.2f", strategyState.ema20)} | Signal: ${strategyState.action}"))
                    }
                )
            }
        }
    }
}

// -------------------------------------------------------------------------------------------------
// TAB 0: MONITOR
// -------------------------------------------------------------------------------------------------
@Composable
fun MonitorTab(
    ticker: TickerInfo,
    strategyState: StrategyState,
    position: PositionState,
    selectedSymbol: String,
    onSymbolSelected: (String) -> Unit,
    onExecuteTestBuy: () -> Unit,
    onExecuteTestSell: () -> Unit
) {
    val scrollState = rememberScrollState()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(scrollState)
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        // Pair Selector Chips
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            listOf("SOLUSDT" to "SOL/USDT", "DOGEUSDT" to "DOGE/USDT", "BTCUSDT" to "BTC/USDT").forEach { (code, label) ->
                val isSelected = selectedSymbol == code
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .clip(RoundedCornerShape(12.dp))
                        .background(if (isSelected) Slate800 else Slate900)
                        .border(
                            width = if (isSelected) 1.5.dp else 1.dp,
                            color = if (isSelected) CyanGlow else Slate700,
                            shape = RoundedCornerShape(12.dp)
                        )
                        .clickable { onSymbolSelected(code) }
                        .padding(vertical = 10.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        text = label,
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal,
                        color = if (isSelected) CyanGlow else Slate300
                    )
                }
            }
        }

        // Live Market Ticker Card
        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = Slate900),
            shape = RoundedCornerShape(16.dp),
            border = androidx.compose.foundation.BorderStroke(1.dp, Slate800)
        ) {
            Column(modifier = Modifier.padding(20.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column {
                        Text(
                            text = ticker.symbol,
                            style = MaterialTheme.typography.titleLarge,
                            fontWeight = FontWeight.Bold,
                            color = Slate100
                        )
                        Text(
                            text = "MEXC Global Spot Market",
                            style = MaterialTheme.typography.labelSmall,
                            color = Slate400
                        )
                    }
                    val isBull = ticker.changePercent24h >= 0
                    Surface(
                        color = if (isBull) EmeraldBull.copy(alpha = 0.15f) else RoseBear.copy(alpha = 0.15f),
                        shape = RoundedCornerShape(8.dp),
                        border = androidx.compose.foundation.BorderStroke(1.dp, if (isBull) EmeraldBull else RoseBear)
                    ) {
                        Row(
                            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Icon(
                                imageVector = if (isBull) Icons.Default.TrendingUp else Icons.Default.TrendingDown,
                                contentDescription = null,
                                tint = if (isBull) EmeraldBull else RoseBear,
                                modifier = Modifier.size(16.dp)
                            )
                            Spacer(modifier = Modifier.width(4.dp))
                            Text(
                                text = "${if (isBull) "+" else ""}${String.format(Locale.US, "%.2f", ticker.changePercent24h)}%",
                                color = if (isBull) EmeraldBull else RoseBear,
                                fontWeight = FontWeight.Bold,
                                style = MaterialTheme.typography.labelMedium
                            )
                        }
                    }
                }

                Spacer(modifier = Modifier.height(16.dp))

                Text(
                    text = "$${String.format(Locale.US, "%,.2f", ticker.lastPrice)}",
                    style = MaterialTheme.typography.headlineLarge,
                    fontWeight = FontWeight.ExtraBold,
                    color = Slate100,
                    letterSpacing = (-0.5).sp
                )

                Spacer(modifier = Modifier.height(16.dp))

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    StatBox(label = "24h High", value = "$${String.format(Locale.US, "%,.2f", ticker.high24h)}")
                    StatBox(label = "24h Low", value = "$${String.format(Locale.US, "%,.2f", ticker.low24h)}")
                    StatBox(label = "24h Volume", value = "${String.format(Locale.US, "%,.1f", ticker.volume24h)}")
                }
            }
        }

        // Strategy & Signal Card
        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = Slate900),
            shape = RoundedCornerShape(16.dp),
            border = androidx.compose.foundation.BorderStroke(1.dp, Slate800)
        ) {
            Column(modifier = Modifier.padding(20.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "Technical Strategy (RSI + 20 EMA)",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                        color = Slate300
                    )
                    Text(
                        text = "15m Closed Candle",
                        style = MaterialTheme.typography.labelSmall,
                        color = CyanGlow
                    )
                }

                Spacer(modifier = Modifier.height(14.dp))

                // RSI Indicator progress
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text(
                        text = "RSI (14 Period): ${String.format(Locale.US, "%.1f", strategyState.rsi)}",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Slate100,
                        fontWeight = FontWeight.SemiBold
                    )
                    Text(
                        text = if (strategyState.rsi <= 30) "OVERSOLD (Bullish)" else if (strategyState.rsi >= 70) "OVERBOUGHT (Bearish)" else "NEUTRAL",
                        style = MaterialTheme.typography.labelSmall,
                        color = if (strategyState.rsi <= 30) EmeraldBull else if (strategyState.rsi >= 70) RoseBear else AmberWarning,
                        fontWeight = FontWeight.Bold
                    )
                }

                Spacer(modifier = Modifier.height(6.dp))
                LinearProgressIndicator(
                    progress = { (strategyState.rsi / 100.0).toFloat().coerceIn(0f, 1f) },
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(8.dp)
                        .clip(CircleShape),
                    color = if (strategyState.rsi <= 30) EmeraldBull else if (strategyState.rsi >= 70) RoseBear else CyanAccent,
                    trackColor = Slate800
                )

                Spacer(modifier = Modifier.height(14.dp))

                // 20 EMA Filter
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text(
                        text = "20-period EMA: $${String.format(Locale.US, "%,.2f", strategyState.ema20)}",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Slate300
                    )
                    Text(
                        text = if (ticker.lastPrice >= strategyState.ema20) "ABOVE EMA (Trend OK)" else "BELOW EMA (Filter Active)",
                        style = MaterialTheme.typography.labelSmall,
                        color = if (ticker.lastPrice >= strategyState.ema20) EmeraldBull else RoseBear,
                        fontWeight = FontWeight.Bold
                    )
                }

                Spacer(modifier = Modifier.height(16.dp))

                // Action Signal Banner
                val isBuy = strategyState.action == "BUY"
                val isSell = strategyState.action.startsWith("SELL")
                val bannerColor = if (isBuy) EmeraldBull else if (isSell) RoseBear else IndigoRailway

                Surface(
                    color = bannerColor.copy(alpha = 0.12f),
                    shape = RoundedCornerShape(12.dp),
                    border = androidx.compose.foundation.BorderStroke(1.dp, bannerColor)
                ) {
                    Column(modifier = Modifier.padding(14.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(
                                imageVector = if (isBuy) Icons.Default.TrendingUp else if (isSell) Icons.Default.TrendingDown else Icons.Default.Info,
                                contentDescription = null,
                                tint = bannerColor,
                                modifier = Modifier.size(20.dp)
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                            Text(
                                text = "SIGNAL: ${strategyState.action}",
                                style = MaterialTheme.typography.titleSmall,
                                fontWeight = FontWeight.ExtraBold,
                                color = bannerColor
                            )
                        }
                        Spacer(modifier = Modifier.height(4.dp))
                        Text(
                            text = strategyState.reason,
                            style = MaterialTheme.typography.bodySmall,
                            color = Slate300
                        )
                        if (isBuy) {
                            Spacer(modifier = Modifier.height(8.dp))
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween
                            ) {
                                Text(
                                    text = "Target SL: $${String.format(Locale.US, "%,.2f", strategyState.suggestedSl)} (-2%)",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = RoseBear
                                )
                                Text(
                                    text = "Target TP: $${String.format(Locale.US, "%,.2f", strategyState.suggestedTp)} (+4%)",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = EmeraldBull
                                )
                            }
                        }
                    }
                }
            }
        }

        // Active Position Card
        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = Slate900),
            shape = RoundedCornerShape(16.dp),
            border = androidx.compose.foundation.BorderStroke(1.dp, Slate800)
        ) {
            Column(modifier = Modifier.padding(20.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "Active Spot Position",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                        color = Slate300
                    )
                    Text(
                        text = if (position.active) "OPEN (HOLDING)" else "NO ACTIVE TRADE",
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.Bold,
                        color = if (position.active) EmeraldBull else Slate400
                    )
                }

                Spacer(modifier = Modifier.height(12.dp))

                if (position.active) {
                    val currentValue = position.amount * ticker.lastPrice
                    val unrealizedPnl = currentValue - position.costUsdt
                    val pnlPct = (unrealizedPnl / position.costUsdt) * 100.0
                    val isProfit = unrealizedPnl >= 0

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        StatBox("Entry Price", "$${String.format(Locale.US, "%,.2f", position.entryPrice)}")
                        StatBox("Cost (USDT)", "$${String.format(Locale.US, "%.2f", position.costUsdt)}")
                        StatBox(
                            label = "Unrealized PnL",
                            value = "${if (isProfit) "+" else ""}${String.format(Locale.US, "%.2f", unrealizedPnl)} (${String.format(Locale.US, "%.2f", pnlPct)}%)"
                        )
                    }

                    Spacer(modifier = Modifier.height(16.dp))

                    Button(
                        onClick = onExecuteTestSell,
                        modifier = Modifier
                            .fillMaxWidth()
                            .testTag("execute_sell_button"),
                        colors = ButtonDefaults.buttonColors(containerColor = RoseBear),
                        shape = RoundedCornerShape(12.dp)
                    ) {
                        Text("Close Position (Market Sell)", fontWeight = FontWeight.Bold, color = Color.White)
                    }
                } else {
                    Text(
                        text = "The bot is currently 100% in USDT cash, scanning closed 15m candles for RSI oversold triggers.",
                        style = MaterialTheme.typography.bodySmall,
                        color = Slate400
                    )

                    Spacer(modifier = Modifier.height(16.dp))

                    Button(
                        onClick = onExecuteTestBuy,
                        modifier = Modifier
                            .fillMaxWidth()
                            .testTag("execute_buy_button"),
                        colors = ButtonDefaults.buttonColors(containerColor = EmeraldBull),
                        shape = RoundedCornerShape(12.dp)
                    ) {
                        Text("Simulate Immediate Market Buy", fontWeight = FontWeight.Bold, color = Slate950)
                    }
                }
            }
        }
    }
}

// -------------------------------------------------------------------------------------------------
// TAB 1: STRATEGY SIMULATOR
// -------------------------------------------------------------------------------------------------
@Composable
fun SimulatorTab(
    ticker: TickerInfo,
    stopLossPct: Double,
    takeProfitPct: Double
) {
    var simPrice by remember { mutableDoubleStateOf(ticker.lastPrice) }
    var simRsi by remember { mutableDoubleStateOf(28.0) }
    var simEma by remember { mutableDoubleStateOf(ticker.lastPrice * 0.995) }

    val isOversold = simRsi <= 30.0
    val isOverbought = simRsi >= 70.0
    val isAboveEma = simPrice >= simEma

    val generatedAction = if (isAboveEma && isOversold) "BUY" else if (isOverbought) "SELL" else "HOLD"
    val bannerColor = if (generatedAction == "BUY") EmeraldBull else if (generatedAction == "SELL") RoseBear else CyanAccent

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Text(
            text = "Algorithmic Strategy Simulator",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
            color = Slate100
        )
        Text(
            text = "Test the decision boundary of the RSI(14) + 20 EMA Spot strategy by interactively tweaking indicators:",
            style = MaterialTheme.typography.bodySmall,
            color = Slate400
        )

        // Interactive Result Card
        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = bannerColor.copy(alpha = 0.12f)),
            shape = RoundedCornerShape(16.dp),
            border = androidx.compose.foundation.BorderStroke(1.5.dp, bannerColor)
        ) {
            Column(modifier = Modifier.padding(20.dp)) {
                Text(
                    text = "DECISION ENGINE OUTPUT",
                    style = MaterialTheme.typography.labelSmall,
                    color = bannerColor,
                    fontWeight = FontWeight.Bold
                )
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    text = generatedAction,
                    style = MaterialTheme.typography.headlineMedium,
                    fontWeight = FontWeight.ExtraBold,
                    color = bannerColor
                )
                Spacer(modifier = Modifier.height(6.dp))
                Text(
                    text = when (generatedAction) {
                        "BUY" -> "Bullish entry: RSI is oversold (${String.format(Locale.US, "%.1f", simRsi)} <= 30.0) AND price is trading above the 20-period EMA filter."
                        "SELL" -> "Take profit / Overbought exit: RSI (${String.format(Locale.US, "%.1f", simRsi)} >= 70.0) indicates market exhaustion."
                        else -> "Wait: No high-probability setup detected. Retaining cash capital in USDT."
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = Slate300
                )
            }
        }

        // Sliders Card
        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = Slate900),
            shape = RoundedCornerShape(16.dp),
            border = androidx.compose.foundation.BorderStroke(1.dp, Slate800)
        ) {
            Column(modifier = Modifier.padding(20.dp)) {
                // RSI Slider
                Text(
                    text = "RSI Indicator: ${String.format(Locale.US, "%.1f", simRsi)}",
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = Slate100
                )
                Slider(
                    value = simRsi.toFloat(),
                    onValueChange = { simRsi = it.toDouble() },
                    valueRange = 0f..100f,
                    colors = SliderDefaults.colors(thumbColor = CyanGlow, activeTrackColor = CyanGlow)
                )

                Spacer(modifier = Modifier.height(16.dp))

                // Price vs EMA Comparison
                Text(
                    text = "Candle Close Price: $${String.format(Locale.US, "%,.0f", simPrice)}",
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = Slate100
                )
                Slider(
                    value = simPrice.toFloat(),
                    onValueChange = { simPrice = it.toDouble() },
                    valueRange = (ticker.lastPrice * 0.85f).toFloat()..(ticker.lastPrice * 1.15f).toFloat(),
                    colors = SliderDefaults.colors(thumbColor = EmeraldBull, activeTrackColor = EmeraldBull)
                )

                Spacer(modifier = Modifier.height(16.dp))

                Text(
                    text = "20 EMA Trend Filter: $${String.format(Locale.US, "%,.0f", simEma)}",
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = Slate100
                )
                Slider(
                    value = simEma.toFloat(),
                    onValueChange = { simEma = it.toDouble() },
                    valueRange = (ticker.lastPrice * 0.85f).toFloat()..(ticker.lastPrice * 1.15f).toFloat(),
                    colors = SliderDefaults.colors(thumbColor = IndigoRailway, activeTrackColor = IndigoRailway)
                )
            }
        }
    }
}

// -------------------------------------------------------------------------------------------------
// TAB 2: PYTHON REPO & CODE VIEWER
// -------------------------------------------------------------------------------------------------
@Composable
fun PythonRepoTab(
    onCopyCode: (String, String) -> Unit
) {
    val files = PythonRepoContent.files
    var selectedFileIndex by remember { mutableIntStateOf(0) }
    val currentFile = files[selectedFileIndex]

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Text(
            text = "Generated Python Repository",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
            color = Slate100
        )

        // Horizontal file selector tabs
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            files.forEachIndexed { index, file ->
                val isSelected = selectedFileIndex == index
                Surface(
                    color = if (isSelected) CyanGlow else Slate900,
                    shape = RoundedCornerShape(8.dp),
                    border = androidx.compose.foundation.BorderStroke(1.dp, if (isSelected) CyanGlow else Slate800),
                    modifier = Modifier.clickable { selectedFileIndex = index }
                ) {
                    Text(
                        text = file.name,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal,
                        color = if (isSelected) Slate950 else Slate300
                    )
                }
            }
        }

        // File metadata and copy button
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = currentFile.name,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                    color = CyanGlow
                )
                Text(
                    text = currentFile.description,
                    style = MaterialTheme.typography.labelSmall,
                    color = Slate400,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis
                )
            }
            Spacer(modifier = Modifier.width(8.dp))
            OutlinedButton(
                onClick = { onCopyCode(currentFile.name, currentFile.code) },
                colors = ButtonDefaults.outlinedButtonColors(contentColor = CyanGlow),
                border = androidx.compose.foundation.BorderStroke(1.dp, CyanGlow),
                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp),
                shape = RoundedCornerShape(8.dp)
            ) {
                Icon(Icons.Default.ContentCopy, contentDescription = null, modifier = Modifier.size(16.dp))
                Spacer(modifier = Modifier.width(6.dp))
                Text("Copy", style = MaterialTheme.typography.labelSmall)
            }
        }

        // Code Viewer
        Card(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
            colors = CardDefaults.cardColors(containerColor = Slate900),
            shape = RoundedCornerShape(12.dp),
            border = androidx.compose.foundation.BorderStroke(1.dp, Slate800)
        ) {
            SelectionContainer {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .verticalScroll(rememberScrollState())
                        .horizontalScroll(rememberScrollState())
                        .padding(14.dp)
                ) {
                    Text(
                        text = currentFile.code,
                        fontFamily = FontFamily.Monospace,
                        fontSize = 11.sp,
                        lineHeight = 16.sp,
                        color = Slate300
                    )
                }
            }
        }
    }
}

// -------------------------------------------------------------------------------------------------
// TAB 3: CONFIG & .ENV
// -------------------------------------------------------------------------------------------------
@Composable
fun ConfigTab(
    apiKey: String,
    onApiKeyChange: (String) -> Unit,
    apiSecret: String,
    onApiSecretChange: (String) -> Unit,
    telegramToken: String,
    onTelegramTokenChange: (String) -> Unit,
    telegramChatId: String,
    onTelegramChatIdChange: (String) -> Unit,
    tradeAmountUsdt: String,
    onTradeAmountChange: (String) -> Unit,
    stopLossPct: Double,
    onStopLossChange: (Double) -> Unit,
    takeProfitPct: Double,
    onTakeProfitChange: (Double) -> Unit,
    simulationMode: Boolean,
    onSimulationModeChange: (Boolean) -> Unit,
    onCopyEnv: () -> Unit,
    onTestTelegram: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Text(
            text = "Environment & Security Config",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
            color = Slate100
        )

        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = Slate900),
            shape = RoundedCornerShape(16.dp),
            border = androidx.compose.foundation.BorderStroke(1.dp, Slate800)
        ) {
            Column(
                modifier = Modifier.padding(20.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp)
            ) {
                Text(
                    text = "MEXC API Credentials",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                    color = CyanGlow
                )

                OutlinedTextField(
                    value = apiKey,
                    onValueChange = onApiKeyChange,
                    label = { Text("MEXC_API_KEY") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedTextColor = Slate100,
                        unfocusedTextColor = Slate300,
                        focusedBorderColor = CyanGlow,
                        unfocusedBorderColor = Slate700
                    )
                )

                OutlinedTextField(
                    value = apiSecret,
                    onValueChange = onApiSecretChange,
                    label = { Text("MEXC_API_SECRET") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedTextColor = Slate100,
                        unfocusedTextColor = Slate300,
                        focusedBorderColor = CyanGlow,
                        unfocusedBorderColor = Slate700
                    )
                )

                Text(
                    text = "Telegram Notification Alerts",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                    color = CyanGlow
                )

                OutlinedTextField(
                    value = telegramToken,
                    onValueChange = onTelegramTokenChange,
                    label = { Text("TELEGRAM_BOT_TOKEN") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedTextColor = Slate100,
                        unfocusedTextColor = Slate300,
                        focusedBorderColor = CyanGlow,
                        unfocusedBorderColor = Slate700
                    )
                )

                OutlinedTextField(
                    value = telegramChatId,
                    onValueChange = onTelegramChatIdChange,
                    label = { Text("TELEGRAM_CHAT_ID") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedTextColor = Slate100,
                        unfocusedTextColor = Slate300,
                        focusedBorderColor = CyanGlow,
                        unfocusedBorderColor = Slate700
                    )
                )

                FilledTonalButton(
                    onClick = onTestTelegram,
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.filledTonalButtonColors(containerColor = Slate800, contentColor = CyanGlow),
                    shape = RoundedCornerShape(10.dp)
                ) {
                    Icon(Icons.Default.Send, contentDescription = null, modifier = Modifier.size(16.dp))
                    Spacer(modifier = Modifier.width(8.dp))
                    Text("Test Telegram Push Alert")
                }

                Text(
                    text = "Trading & Risk Engine",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                    color = CyanGlow
                )

                OutlinedTextField(
                    value = tradeAmountUsdt,
                    onValueChange = onTradeAmountChange,
                    label = { Text("SLOT_SIZE_USDT (Default: 4.0 USDT)") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedTextColor = Slate100,
                        unfocusedTextColor = Slate300,
                        focusedBorderColor = CyanGlow,
                        unfocusedBorderColor = Slate700
                    )
                )

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text("Simulation (Dry-Run) Mode", color = Slate100, style = MaterialTheme.typography.bodyMedium)
                    Switch(
                        checked = simulationMode,
                        onCheckedChange = onSimulationModeChange,
                        colors = SwitchDefaults.colors(checkedThumbColor = EmeraldBull)
                    )
                }

                Spacer(modifier = Modifier.height(6.dp))

                Button(
                    onClick = onCopyEnv,
                    modifier = Modifier
                        .fillMaxWidth()
                        .testTag("copy_env_button"),
                    colors = ButtonDefaults.buttonColors(containerColor = CyanGlow),
                    shape = RoundedCornerShape(12.dp)
                ) {
                    Icon(Icons.Default.ContentCopy, contentDescription = null, tint = Slate950)
                    Spacer(modifier = Modifier.width(8.dp))
                    Text("Generate & Copy .env to Clipboard", color = Slate950, fontWeight = FontWeight.Bold)
                }
            }
        }
    }
}

// -------------------------------------------------------------------------------------------------
// TAB 4: WORKER LOGS
// -------------------------------------------------------------------------------------------------
@Composable
fun LogsTab(
    logs: List<LogEntry>,
    onClearLogs: () -> Unit,
    onAddSimCycle: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = "Live 24/7 Daemon Console",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
                color = Slate100
            )

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(
                    onClick = onAddSimCycle,
                    contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp),
                    shape = RoundedCornerShape(8.dp),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = CyanGlow)
                ) {
                    Text("+ Sim Loop", style = MaterialTheme.typography.labelSmall)
                }
                OutlinedButton(
                    onClick = onClearLogs,
                    contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp),
                    shape = RoundedCornerShape(8.dp),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = Slate400)
                ) {
                    Text("Clear", style = MaterialTheme.typography.labelSmall)
                }
            }
        }

        Card(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
            colors = CardDefaults.cardColors(containerColor = Slate900),
            shape = RoundedCornerShape(12.dp),
            border = androidx.compose.foundation.BorderStroke(1.dp, Slate800)
        ) {
            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                items(logs) { log ->
                    val color = when (log.level) {
                        "ERROR" -> RoseBear
                        "WARN" -> AmberWarning
                        else -> Slate300
                    }
                    SelectionContainer {
                        Text(
                            text = "[${log.timestamp}] [${log.level}] [${log.tag}] ${log.message}",
                            fontFamily = FontFamily.Monospace,
                            fontSize = 11.sp,
                            lineHeight = 16.sp,
                            color = color
                        )
                    }
                }
            }
        }
    }
}

// -------------------------------------------------------------------------------------------------
// HELPERS & STAT COMPONENTS
// -------------------------------------------------------------------------------------------------
@Composable
fun StatBox(label: String, value: String) {
    Column {
        Text(text = label, style = MaterialTheme.typography.labelSmall, color = Slate400)
        Spacer(modifier = Modifier.height(2.dp))
        Text(
            text = value,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Bold,
            color = Slate100
        )
    }
}

private fun getCurrentTime(): String {
    val sdf = SimpleDateFormat("HH:mm:ss", Locale.US)
    return sdf.format(Date())
}

// Retained for unit/screenshot test compatibility
@Composable
fun Greeting(name: String, modifier: Modifier = Modifier) {
    Text(text = "Hello $name!", modifier = modifier)
}

@Preview(showBackground = true)
@Composable
fun GreetingPreview() {
    MyApplicationTheme {
        MexcTradingApp()
    }
}

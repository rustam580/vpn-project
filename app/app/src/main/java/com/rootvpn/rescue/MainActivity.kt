package com.rootvpn.rescue

import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.VpnService
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.res.stringResource
import com.rootvpn.rescue.R
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.rootvpn.rescue.profile.ImportResult
import com.rootvpn.rescue.profile.ProfileParser
import com.rootvpn.rescue.profile.ProfileStore
import com.rootvpn.rescue.ui.theme.MyApplicationTheme
import com.rootvpn.rescue.vpn.RootVpnService
import com.rootvpn.rescue.vpn.RootVpnService.ConnectionState
import com.rootvpn.rescue.vpn.RootVpnService.TransportMode
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    private val importedConfig = mutableStateOf<ImportResult?>(null)

    override fun onResume() {
        super.onResume()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        importedConfig.value = ProfileStore.loadLast(this)
        handleIntent(intent)

        setContent {
            MyApplicationTheme(darkTheme = true, dynamicColor = false) {
                MainScreen(
                    importedConfigState = importedConfig,
                    onRequestVpnStart = { mode ->
                        try {
                            val intent = VpnService.prepare(this)
                            if (intent != null) {
                                // Request VPN permissions from user
                                vpnPermissionLauncher.launch(intent)
                            } else {
                                // Permission already granded, trigger directly
                                triggerVpnService(mode)
                            }
                        } catch (e: SecurityException) {
                            RootVpnService.log("SecurityException on prepare: ${e.message}. Triggering VPN directly.")
                            triggerVpnService(mode)
                        } catch (e: Exception) {
                            RootVpnService.log("Preparation failed: ${e.message}")
                            Toast.makeText(this, "Preparation warning: ${e.message}", Toast.LENGTH_SHORT).show()
                            triggerVpnService(mode)
                        }
                    },
                    onRequestVpnStop = {
                        val stopIntent = Intent(this, RootVpnService::class.java).apply {
                            action = RootVpnService.ACTION_DISCONNECT
                        }
                        startService(stopIntent)
                    },
                    onRequestToggleMode = {
                        val toggleIntent = Intent(this, RootVpnService::class.java).apply {
                            action = RootVpnService.ACTION_TOGGLE_MODE
                        }
                        startService(toggleIntent)
                    }
                )
            }
        }
    }

    private val vpnPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == RESULT_OK) {
            // Permission granted, trigger starting default mode (PERFORMANCE)
            triggerVpnService(TransportMode.PERFORMANCE)
        } else {
            Toast.makeText(this, "RootVPN requires VpnService approval to route system data.", Toast.LENGTH_LONG).show()
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        val uri = intent?.data ?: return
        try {
            val profile = ProfileParser.parse(uri.toString())
            RootVpnService.log("Captured external profile: ${profile.kind} ${profile.title}")
            RootVpnService.log(
                "Inbound profile params -> scheme=${profile.scheme}, host=${profile.host}, " +
                    "transport=${profile.transport}, room=${profile.roomId}, server=${profile.server}"
            )

            importedConfig.value = profile
            ProfileStore.saveLast(this, profile)
            RootVpnService.log("System Status: Imported client channel rules successfully.")
            Toast.makeText(this, "RootVPN Rescue: Ingested system URI configuration!", Toast.LENGTH_SHORT).show()
        } catch (e: Exception) {
            RootVpnService.log("DeepLink routing parsing error: ${e.message}")
        }
    }

    private fun triggerVpnService(mode: TransportMode) {
        val startIntent = Intent(this, RootVpnService::class.java).apply {
            action = RootVpnService.ACTION_CONNECT
            putExtra(RootVpnService.EXTRA_MODE, mode.name)
            importedConfig.value?.raw?.let { putExtra(RootVpnService.EXTRA_PROFILE_RAW, it) }
        }
        startService(startIntent)
    }
}

// Global Sleek Styling Color Constants extracted from high-fidelity spec
val CanvasSlate = Color(0xFF111318)
val SleekCardBg = Color(0xFF1D1B20)
val SleekCardBgActive = Color(0xFF211F26)
val PurpleAccent = Color(0xFFD0BCFF)
val PurpleAccentDim = Color(0xFF4A4458)
val DarkPurpleText = Color(0xFF381E72)
val TextGray = Color(0xFF938F99)
val TextLight = Color(0xFFE2E2E6)
val EmeraldGreen = Color(0xFF10B981)
val CoralRed = Color(0xFFEF4444)

@OptIn(ExperimentalAnimationApi::class)
@Composable
fun MainScreen(
    importedConfigState: MutableState<ImportResult?>,
    onRequestVpnStart: (TransportMode) -> Unit,
    onRequestVpnStop: () -> Unit,
    onRequestToggleMode: () -> Unit
) {
    val context = LocalContext.current
    var selectedTab by remember { mutableStateOf(0) }

    // Connect flow states mapping to service
    val vpnState by RootVpnService.currentState.collectAsStateWithLifecycle()
    val transportMode by RootVpnService.currentMode.collectAsStateWithLifecycle()
    val downloadSpeed by RootVpnService.downloadSpeed.collectAsStateWithLifecycle()
    val uploadSpeed by RootVpnService.uploadSpeed.collectAsStateWithLifecycle()
    val logsList by RootVpnService.logs.collectAsStateWithLifecycle()

    var secondsConnected by remember { mutableStateOf(0) }

    // Timer logic to track duration
    LaunchedEffect(vpnState) {
        if (vpnState == ConnectionState.CONNECTED) {
            while (true) {
                delay(1000)
                secondsConnected++
            }
        } else {
            secondsConnected = 0
        }
    }

    Scaffold(
        modifier = Modifier.fillMaxSize(),
        bottomBar = {
            SleekBottomNav(
                currentTab = selectedTab,
                onTabSelected = { selectedTab = it }
            )
        },
        containerColor = CanvasSlate
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
        ) {
            // High Fidelity Header Row
            SleekTopHeader(vpnState = vpnState)

            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f)
            ) {
                when (selectedTab) {
                    0 -> DashboardTab(
                        vpnState = vpnState,
                        transportMode = transportMode,
                        downloadSpeed = downloadSpeed,
                        uploadSpeed = uploadSpeed,
                        secondsConnected = secondsConnected,
                        importedConfig = importedConfigState.value,
                        onToggleState = {
                            if (vpnState == ConnectionState.CONNECTED) {
                                onRequestVpnStop()
                            } else {
                                onRequestVpnStart(transportMode)
                            }
                        },
                        onSelectMode = { mode ->
                            if (vpnState == ConnectionState.CONNECTED) {
                                if (transportMode != mode) {
                                    onRequestToggleMode()
                                }
                            } else {
                                // If disconnected, simply toggle the selection intent
                                if (transportMode != mode) {
                                    onRequestToggleMode()
                                }
                            }
                        },
                        onClearImport = {
                            importedConfigState.value = null
                            ProfileStore.clearLast(context)
                        }
                    )
                    1 -> ServersTab(
                        vpnState = vpnState,
                        activeMode = transportMode,
                        onServerSelect = { serverName ->
                            RootVpnService.log("Switched target entrypoint node to: $serverName")
                        }
                    )
                    2 -> SystemLogsTab(
                        logs = logsList,
                        onClearLogs = {
                            // We can reset logs inside VpnService by logging a simple reset marker
                            RootVpnService.log("Terminal flushed by user.")
                        }
                    )
                    3 -> ArchitectSpecsTab()
                }
            }
        }
    }
}

@Composable
fun SleekTopHeader(vpnState: ConnectionState) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 24.dp, vertical = 16.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            // Icon emblem (Custom vector)
            Box(
                modifier = Modifier
                    .size(40.dp)
                    .background(PurpleAccent, RoundedCornerShape(12.dp))
                    .testTag("app_logo_container"),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    imageVector = Icons.Default.Lock,
                    contentDescription = "RootVPN Shield",
                    tint = DarkPurpleText,
                    modifier = Modifier.size(24.dp)
                )
            }
            Column {
                Text(
                    text = stringResource(id = R.string.app_name),
                    color = TextLight,
                    fontSize = 18.sp,
                    fontWeight = FontWeight.SemiBold,
                    letterSpacing = (-0.5).sp
                )
                Text(
                    text = if (vpnState == ConnectionState.CONNECTED) stringResource(id = R.string.state_rescue_active) else stringResource(id = R.string.state_bypass_ready),
                    color = if (vpnState == ConnectionState.CONNECTED) EmeraldGreen else PurpleAccent,
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 1.5.sp
                )
            }
        }

        IconButton(
            onClick = {
                RootVpnService.log("RootVPN diagnostics requested.")
            },
            modifier = Modifier
                .size(48.dp)
                .background(SleekCardBg, CircleShape)
                .border(1.dp, Color.White.copy(alpha = 0.05f), CircleShape)
        ) {
            Icon(
                imageVector = Icons.Default.Settings,
                contentDescription = "Diagnostics Settings",
                tint = TextLight
            )
        }
    }
}

@Composable
fun DashboardTab(
    vpnState: ConnectionState,
    transportMode: TransportMode,
    downloadSpeed: Float,
    uploadSpeed: Float,
    secondsConnected: Int,
    importedConfig: ImportResult?,
    onToggleState: () -> Unit,
    onSelectMode: (TransportMode) -> Unit,
    onClearImport: () -> Unit
) {
    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 24.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp),
        contentPadding = PaddingValues(bottom = 24.dp)
    ) {
        // Master Toggle Ring Area
        item {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 12.dp),
                contentAlignment = Alignment.Center
            ) {
                // Outer Pulse Glow Circle animation
                val infiniteTransition = rememberInfiniteTransition(label = "pulse")
                val pulseScale by infiniteTransition.animateFloat(
                    initialValue = 1f,
                    targetValue = if (vpnState == ConnectionState.CONNECTED) 1.25f else 1.05f,
                    animationSpec = infiniteRepeatable(
                        animation = tween(2000, easing = LinearEasing),
                        repeatMode = RepeatMode.Reverse
                    ),
                    label = "scale"
                )
                val pulseOpacity by infiniteTransition.animateFloat(
                    initialValue = 0.15f,
                    targetValue = if (vpnState == ConnectionState.CONNECTED) 0.03f else 0.08f,
                    animationSpec = infiniteRepeatable(
                        animation = tween(2000, easing = LinearEasing),
                        repeatMode = RepeatMode.Reverse
                    ),
                    label = "opacity"
                )

                // Render pulsating background halo
                Canvas(modifier = Modifier.size(240.dp)) {
                    drawCircle(
                        color = if (vpnState == ConnectionState.CONNECTED) EmeraldGreen else PurpleAccent,
                        radius = (size.minDimension / 2.3f) * pulseScale,
                        alpha = pulseOpacity
                    )
                    drawCircle(
                        color = (if (vpnState == ConnectionState.CONNECTED) EmeraldGreen else PurpleAccent).copy(alpha = 0.2f),
                        radius = size.minDimension / 2.5f,
                        style = Stroke(width = 1.dp.toPx())
                    )
                }

                // Interactive Toggle Core
                Button(
                    onClick = onToggleState,
                    modifier = Modifier
                        .size(150.dp)
                        .testTag("vpn_toggle_button"),
                    shape = CircleShape,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = if (vpnState == ConnectionState.CONNECTED) EmeraldGreen else PurpleAccent
                    ),
                    elevation = ButtonDefaults.buttonElevation(
                        defaultElevation = 10.dp,
                        pressedElevation = 2.dp
                    ),
                    contentPadding = PaddingValues(0.dp)
                ) {
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.Center
                    ) {
                        Icon(
                            imageVector = if (vpnState == ConnectionState.CONNECTED) Icons.Default.PlayArrow else Icons.Default.Lock,
                            contentDescription = "Connection Master Switch",
                            tint = if (vpnState == ConnectionState.CONNECTED) Color.White else DarkPurpleText,
                            modifier = Modifier.size(54.dp)
                        )
                        Spacer(modifier = Modifier.height(6.dp))
                        Text(
                            text = when (vpnState) {
                                ConnectionState.CONNECTED -> stringResource(id = R.string.state_secure)
                                ConnectionState.CONNECTING -> stringResource(id = R.string.state_connecting)
                                ConnectionState.FALLING_BACK -> stringResource(id = R.string.state_falling_back)
                                ConnectionState.DISCONNECTED -> stringResource(id = R.string.state_disconnected)
                            },
                            color = if (vpnState == ConnectionState.CONNECTED) Color.White else DarkPurpleText,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Bold,
                            letterSpacing = 1.2.sp
                        )
                    }
                }
            }
        }

        // Connection States & Timer Metadata Display
        item {
            Column(
                modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                val animatedDuration = formatTime(secondsConnected)
                Text(
                    text = if (vpnState == ConnectionState.CONNECTED) animatedDuration else "00:00:00",
                    style = TextStyle(
                        fontFamily = FontFamily.Monospace,
                        fontSize = 32.sp,
                        fontWeight = FontWeight.Medium,
                        color = TextLight,
                        letterSpacing = 0.5.sp
                    )
                )
                Spacer(modifier = Modifier.height(4.dp))
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Box(
                        modifier = Modifier
                            .size(7.dp)
                            .background(
                                color = when (vpnState) {
                                    ConnectionState.CONNECTED -> EmeraldGreen
                                    ConnectionState.CONNECTING -> Color.Yellow
                                    ConnectionState.FALLING_BACK -> CoralRed
                                    ConnectionState.DISCONNECTED -> TextGray
                                },
                                shape = CircleShape
                            )
                    )
                    Text(
                        text = when (vpnState) {
                            ConnectionState.CONNECTED -> stringResource(id = R.string.perm_bypass_text)
                            ConnectionState.CONNECTING -> stringResource(id = R.string.handshake_text)
                            ConnectionState.FALLING_BACK -> stringResource(id = R.string.warning_fallback_text)
                            ConnectionState.DISCONNECTED -> stringResource(id = R.string.disconnected_socks_text)
                        },
                        fontSize = 12.sp,
                        color = TextGray
                    )
                }
            }
        }

        // Mode selectors
        item {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    text = stringResource(id = R.string.select_routing_backend),
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Bold,
                    color = TextGray,
                    letterSpacing = 1.2.sp
                )
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(16.dp)
                ) {
                    // Performance Mode Card
                    ModeSelectorCard(
                        title = stringResource(id = R.string.performance_mode),
                        modeIndex = "SPEED 01",
                        description = stringResource(id = R.string.performance_mode_desc),
                        isActive = transportMode == TransportMode.PERFORMANCE,
                        modifier = Modifier.weight(1f),
                        onClick = { onSelectMode(TransportMode.PERFORMANCE) }
                    )

                    // Rescue Mode Card
                    ModeSelectorCard(
                        title = stringResource(id = R.string.rescue_mode),
                        modeIndex = "RESCUE 02",
                        description = stringResource(id = R.string.rescue_mode_desc),
                        isActive = transportMode == TransportMode.RESCUE,
                        modifier = Modifier.weight(1f),
                        onClick = { onSelectMode(TransportMode.RESCUE) }
                    )
                }
            }
        }

        // Live stats bandwidth
        item {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(SleekCardBg, RoundedCornerShape(24.dp))
                    .border(1.dp, Color.White.copy(alpha = 0.05f), RoundedCornerShape(24.dp))
                    .padding(20.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                // Downloader
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Box(
                        modifier = Modifier
                            .size(36.dp)
                            .background(Color.White.copy(alpha = 0.05f), CircleShape),
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(
                            imageVector = Icons.Default.KeyboardArrowDown,
                            contentDescription = "Download link icon",
                            tint = PurpleAccent,
                            modifier = Modifier.size(18.dp)
                        )
                    }
                    Column {
                        Text("DOWNLOAD", color = TextGray, fontSize = 9.sp, fontWeight = FontWeight.Bold)
                        Text(
                            text = String.format("%.1f MB/s", downloadSpeed),
                            color = TextLight,
                            fontSize = 15.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                    }
                }

                // Vertical Divider line
                Box(
                    modifier = Modifier
                        .width(1.dp)
                        .height(32.dp)
                        .background(Color.White.copy(alpha = 0.1f))
                )

                // Uploader
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Box(
                        modifier = Modifier
                            .size(36.dp)
                            .background(Color.White.copy(alpha = 0.05f), CircleShape),
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(
                            imageVector = Icons.Default.KeyboardArrowUp,
                            contentDescription = "Upload link icon",
                            tint = PurpleAccent,
                            modifier = Modifier.size(18.dp)
                        )
                    }
                    Column {
                        Text("UPLOAD", color = TextGray, fontSize = 9.sp, fontWeight = FontWeight.Bold)
                        Text(
                            text = String.format("%.1f MB/s", uploadSpeed),
                            color = TextLight,
                            fontSize = 15.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                    }
                }
            }
        }

        // Conditionally render imported link configs card
        if (importedConfig != null) {
            item {
                Card(
                    colors = CardDefaults.cardColors(containerColor = SleekCardBgActive),
                    shape = RoundedCornerShape(24.dp),
                    modifier = Modifier
                        .fillMaxWidth()
                        .border(1.dp, PurpleAccent.copy(alpha = 0.3f), RoundedCornerShape(24.dp))
                ) {
                    Column(modifier = Modifier.padding(20.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                text = "IMPORTED ${importedConfig.kind} CONFIG",
                                color = PurpleAccent,
                                fontSize = 10.sp,
                                fontWeight = FontWeight.Bold,
                                letterSpacing = 1.2.sp
                            )
                            Icon(
                                imageVector = Icons.Default.Delete,
                                contentDescription = "Clear incoming config",
                                tint = CoralRed.copy(alpha = 0.8f),
                                modifier = Modifier
                                    .size(20.dp)
                                    .clickable { onClearImport() }
                            )
                        }
                        Spacer(modifier = Modifier.height(8.dp))
                        Text(
                            text = importedConfig.title,
                            color = TextLight,
                            fontSize = 14.sp,
                            fontWeight = FontWeight.SemiBold,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                        Spacer(modifier = Modifier.height(4.dp))
                        Text(
                            text = "Source: ${importedConfig.raw}",
                            color = TextLight,
                            fontSize = 12.sp,
                            fontWeight = FontWeight.Medium,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                        Spacer(modifier = Modifier.height(8.dp))
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween
                        ) {
                            Text(
                                text = "Target: ${importedConfig.roomId ?: importedConfig.server ?: importedConfig.channel ?: "not parsed"}",
                                color = TextGray,
                                fontSize = 11.sp,
                                modifier = Modifier.weight(1f),
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis
                            )
                            Text(
                                text = "Core: ${importedConfig.transport ?: importedConfig.scheme}",
                                color = TextGray,
                                fontSize = 11.sp,
                                modifier = Modifier.weight(1f),
                                textAlign = TextAlign.Center
                            )
                            Text(
                                text = "FPS: ${importedConfig.fps ?: "-"}",
                                color = TextGray,
                                fontSize = 11.sp,
                                modifier = Modifier.weight(1f),
                                textAlign = TextAlign.End
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun ModeSelectorCard(
    title: String,
    modeIndex: String,
    description: String,
    isActive: Boolean,
    modifier: Modifier = Modifier,
    onClick: () -> Unit
) {
    Box(
        modifier = modifier
            .clip(RoundedCornerShape(24.dp))
            .background(if (isActive) SleekCardBgActive else SleekCardBg)
            .border(
                1.dp,
                if (isActive) PurpleAccent else Color.White.copy(alpha = 0.05f),
                RoundedCornerShape(24.dp)
            )
            .clickable { onClick() }
            .padding(16.dp)
    ) {
        Column {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = modeIndex,
                    color = if (isActive) PurpleAccent else TextGray,
                    fontSize = 9.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 1.sp
                )
                if (isActive) {
                    Box(
                        modifier = Modifier
                            .size(6.dp)
                            .background(PurpleAccent, CircleShape)
                    )
                }
            }
            Spacer(modifier = Modifier.height(6.dp))
            Text(
                text = title,
                color = TextLight,
                fontSize = 14.sp,
                fontWeight = FontWeight.SemiBold
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = description,
                color = TextGray,
                fontSize = 10.sp,
                lineHeight = 14.sp
            )
        }
    }
}

@Composable
fun ServersTab(
    vpnState: ConnectionState,
    activeMode: TransportMode,
    onServerSelect: (String) -> Unit
) {
    var selectedServer by remember { mutableStateOf("HK-04 (VLESS Reality)") }

    val performanceNodes = listOf(
        "HK-04 Reality (Performance Node) - Ping 42ms",
        "JP-02 Reality (Performance Node) - Ping 68ms",
        "US-01 Reality (Performance Node) - Ping 120ms",
        "SG-03 Reality (Performance Node) - Ping 55ms"
    )

    val rescueNodes = listOf(
        "RootVPN Emergency Bridge-A (WebRTC Rescue) - Multi-Link",
        "RootVPN Emergency Bridge-B (WebRTC Rescue) - Standby Mode"
    )

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        contentPadding = PaddingValues(top = 12.dp, bottom = 24.dp)
    ) {
        item {
            Text(
                text = "ACTIVE TOPOLOGY NODES",
                fontSize = 10.sp,
                fontWeight = FontWeight.Bold,
                color = TextGray,
                letterSpacing = 1.2.sp
            )
        }

        item {
            Text(
                text = "VLESS Standard Outbounds (sing-box Core)",
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
                color = PurpleAccent
            )
        }

        items(performanceNodes) { node ->
            val isCurrent = selectedServer == node
            ServerNodeRow(
                name = node,
                isCurrent = isCurrent,
                isEnabled = activeMode == TransportMode.PERFORMANCE,
                isRecommended = node.contains("HK-04"),
                onClick = {
                    if (activeMode == TransportMode.PERFORMANCE) {
                        selectedServer = node
                        onServerSelect(node)
                    }
                }
            )
        }

        item {
            Spacer(modifier = Modifier.height(12.dp))
            Text(
                text = "WebRTC Rescue Outbounds (olcrtc Core)",
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
                color = PurpleAccent
            )
        }

        items(rescueNodes) { node ->
            val isCurrent = selectedServer == node
            ServerNodeRow(
                name = node,
                isCurrent = isCurrent,
                isEnabled = activeMode == TransportMode.RESCUE,
                isRecommended = node.contains("Bridge-A"),
                onClick = {
                    if (activeMode == TransportMode.RESCUE) {
                        selectedServer = node
                        onServerSelect(node)
                    }
                }
            )
        }
    }
}

@Composable
fun ServerNodeRow(
    name: String,
    isCurrent: Boolean,
    isEnabled: Boolean,
    isRecommended: Boolean,
    onClick: () -> Unit
) {
    Card(
        colors = CardDefaults.cardColors(
            containerColor = if (isCurrent) SleekCardBgActive else SleekCardBg
        ),
        shape = RoundedCornerShape(20.dp),
        modifier = Modifier
            .fillMaxWidth()
            .border(
                1.dp,
                if (isCurrent && isEnabled) PurpleAccent else Color.White.copy(alpha = 0.05f),
                RoundedCornerShape(20.dp)
            )
            .clickable(enabled = isEnabled) { onClick() }
    ) {
        Row(
            modifier = Modifier
                .padding(18.dp)
                .fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.weight(1f)
            ) {
                Box(
                    modifier = Modifier
                        .size(32.dp)
                        .background(Color.White.copy(alpha = 0.05f), CircleShape),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        imageVector = if (name.contains("Rescue")) Icons.Default.Warning else Icons.Default.List,
                        contentDescription = "Node Category",
                        tint = if (isEnabled) PurpleAccent else TextGray,
                        modifier = Modifier.size(16.dp)
                    )
                }

                Column(modifier = Modifier.weight(1f)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            text = name,
                            color = if (isEnabled) TextLight else TextGray,
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Medium,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                        if (isRecommended) {
                            Spacer(modifier = Modifier.width(6.dp))
                            Box(
                                modifier = Modifier
                                    .background(PurpleAccent.copy(alpha = 0.2f), RoundedCornerShape(4.dp))
                                    .padding(horizontal = 4.dp, vertical = 2.dp)
                            ) {
                                Text("FAST", color = PurpleAccent, fontSize = 8.sp, fontWeight = FontWeight.Bold)
                            }
                        }
                    }
                    Text(
                        text = if (isEnabled) "Ready to stream bypass packets" else "Inactive in this mode",
                        color = TextGray,
                        fontSize = 11.sp
                    )
                }
            }

            if (isCurrent && isEnabled) {
                Icon(
                    imageVector = Icons.Default.CheckCircle,
                    contentDescription = "Active selection indicator",
                    tint = EmeraldGreen,
                    modifier = Modifier.size(20.dp)
                )
            }
        }
    }
}

@Composable
fun SystemLogsTab(
    logs: List<String>,
    onClearLogs: () -> Unit
) {
    val clipboardManager = LocalContext.current.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    val context = LocalContext.current

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 24.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = "REAL-TIME DIAGNOSTIC LOOPS",
                fontSize = 10.sp,
                fontWeight = FontWeight.Bold,
                color = TextGray,
                letterSpacing = 1.2.sp
            )
            IconButton(onClick = onClearLogs) {
                Icon(imageVector = Icons.Default.Delete, contentDescription = "Clear Terminal logs", tint = TextGray)
            }
        }

        Spacer(modifier = Modifier.height(10.dp))

        // Service lifecycle log output.
        Card(
            colors = CardDefaults.cardColors(containerColor = Color(0xFF0C0E12)),
            shape = RoundedCornerShape(16.dp),
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f)
                .border(1.dp, Color.White.copy(alpha = 0.05f), RoundedCornerShape(16.dp))
        ) {
            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
                reverseLayout = true // New logs at top
            ) {
                if (logs.isEmpty()) {
                    item {
                        Text(
                            text = "No active packets recorded. Start VPN bypass to listen to live routing telemetry logs.",
                            color = TextGray,
                            style = TextStyle(fontFamily = FontFamily.Monospace, fontSize = 11.sp),
                            textAlign = TextAlign.Center,
                            modifier = Modifier.fillMaxWidth().padding(top = 24.dp)
                        )
                    }
                } else {
                    items(logs) { log ->
                        val isDanger = log.contains("WARNING") || log.contains("CRITICAL") || log.contains("block")
                        val isSuccess = log.contains("SUCCESS") || log.contains("allocated") || log.contains("connected") || log.contains("Redirected")
                        val color = when {
                            isDanger -> CoralRed
                            isSuccess -> EmeraldGreen
                            else -> TextGray
                        }
                        Text(
                            text = log,
                            color = color,
                            style = TextStyle(
                                fontFamily = FontFamily.Monospace,
                                fontSize = 10.sp,
                                lineHeight = 14.sp
                            )
                        )
                    }
                }
            }
        }

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            Button(
                onClick = {
                    if (logs.isNotEmpty()) {
                        val fullLog = logs.joinToString("\n")
                        val clip = android.content.ClipData.newPlainText("RootVPN Rescue Logs", fullLog)
                        clipboardManager.setPrimaryClip(clip)
                        Toast.makeText(context, "System Logs saved to clipboard!", Toast.LENGTH_SHORT).show()
                    }
                },
                modifier = Modifier.weight(1f),
                colors = ButtonDefaults.buttonColors(containerColor = SleekCardBg),
                shape = RoundedCornerShape(16.dp)
            ) {
                Icon(imageVector = Icons.Default.Share, contentDescription = "Copy logs", modifier = Modifier.size(16.dp))
                Spacer(modifier = Modifier.width(8.dp))
                Text("Copy Full Terminal Dump", color = TextLight, fontSize = 12.sp)
            }
        }
    }
}

@Composable
fun ArchitectSpecsTab() {
    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        contentPadding = PaddingValues(top = 12.dp, bottom = 24.dp)
    ) {
        item {
            Text(
                text = "ROOTVPN ANDROID BUILD PLAN",
                fontSize = 10.sp,
                fontWeight = FontWeight.Bold,
                color = TextGray,
                letterSpacing = 1.2.sp
            )
        }

        item {
            ArchitectSectionCard(
                title = "1. Profile import",
                content = "The app already accepts vless:// and olcrtc:// links through Android deep links, parses the important fields, and stores the last imported profile locally. This is the safe first layer: subscription delivery works before VPN routing is enabled."
            )
        }

        item {
            ArchitectSectionCard(
                title = "2. Real VPN core",
                content = "Next we wire a real packet path: Android VpnService creates TUN, a local gateway reads packets, and sing-box/libbox handles VLESS outbound. Until that is implemented, TUN startup is intentionally disabled to avoid breaking device internet."
            )
        }

        item {
            ArchitectSectionCard(
                title = "3. Rescue transport",
                content = "After VLESS works, olcRTC should run as a local backend for Rescue profiles. The same Android VPN gateway can then switch between the normal VLESS outbound and the olcRTC backend without asking the user to configure anything manually."
            )
        }

        item {
            ArchitectSectionCard(
                title = "4. Production hardening",
                content = "Before release we need stable foreground-service behavior, battery policy handling, connection diagnostics, profile refresh from the bot, and a clean failure path when a Rescue room is rotated server-side."
            )
        }
    }
}
@Composable
fun ArchitectSectionCard(title: String, content: String) {
    Card(
        colors = CardDefaults.cardColors(containerColor = SleekCardBg),
        shape = RoundedCornerShape(20.dp),
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, Color.White.copy(alpha = 0.05f), RoundedCornerShape(20.dp))
    ) {
        Column(modifier = Modifier.padding(18.dp)) {
            Text(
                text = title,
                color = TextLight,
                fontSize = 14.sp,
                fontWeight = FontWeight.SemiBold
            )
            Spacer(modifier = Modifier.height(10.dp))
            Text(
                text = content,
                color = TextGray,
                fontSize = 12.sp,
                lineHeight = 18.sp
            )
        }
    }
}

@Composable
fun SleekBottomNav(
    currentTab: Int,
    onTabSelected: (Int) -> Unit
) {
    NavigationBar(
        containerColor = SleekCardBg,
        tonalElevation = 0.dp,
        modifier = Modifier.border(1.dp, Color.White.copy(alpha = 0.05f), RoundedCornerShape(topStart = 24.dp, topEnd = 24.dp))
    ) {
        val tabItems = listOf(
            NavigationItemData("Home", Icons.Default.Home),
            NavigationItemData("Servers", Icons.Default.Place),
            NavigationItemData("Logs", Icons.Default.Edit),
            NavigationItemData("Architecture", Icons.Default.Info)
        )

        tabItems.forEachIndexed { index, item ->
            val isSelected = currentTab == index
            NavigationBarItem(
                selected = isSelected,
                onClick = { onTabSelected(index) },
                icon = {
                    Icon(
                        imageVector = item.icon,
                        contentDescription = item.label,
                        tint = if (isSelected) PurpleAccent else TextGray,
                        modifier = Modifier.size(24.dp)
                    )
                },
                label = {
                    Text(
                        text = item.label,
                        color = if (isSelected) PurpleAccent else TextGray,
                        style = TextStyle(fontWeight = FontWeight.Medium, fontSize = 10.sp)
                    )
                },
                colors = NavigationBarItemDefaults.colors(
                    indicatorColor = PurpleAccentDim
                )
            )
        }
    }
}

data class NavigationItemData(val label: String, val icon: ImageVector)

// Clean time duration formatting utility helper
fun formatTime(totalSeconds: Int): String {
    val hrs = totalSeconds / 3600
    val mins = (totalSeconds % 3600) / 60
    val secs = totalSeconds % 60
    return String.format("%02d:%02d:%02d", hrs, mins, secs)
}



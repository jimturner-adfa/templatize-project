package com.templatizeproject.app

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import com.templatizeproject.app.ui.theme.TemplatizeProjectTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            TemplatizeProjectTheme {
                Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    ConverterScreen()
                }
            }
        }
    }
}

/** Code On the Go always keeps projects here; project names are directories under this root. */
private const val PROJECTS_DIR = "/sdcard/CodeOnTheGoProjects"

private fun hasStoragePermission(): Boolean =
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
        Environment.isExternalStorageManager()
    } else {
        true // requested via the runtime permission launcher below on API < 30
    }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConverterScreen() {
    val context = LocalContext.current
    var hasPermission by remember { mutableStateOf(hasStoragePermission()) }

    val legacyPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { grants ->
        hasPermission = grants.values.all { it } || hasStoragePermission()
    }
    val allFilesLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) {
        hasPermission = hasStoragePermission()
    }

    var projectName by remember { mutableStateOf("") }
    var templateName by remember { mutableStateOf("") }
    var moduleName by remember { mutableStateOf("app") }
    var dryRun by remember { mutableStateOf(false) }
    var skipCleanup by remember { mutableStateOf(false) }

    var isRunning by remember { mutableStateOf(false) }
    val logLines = remember { mutableStateListOf<String>() }
    var summary by remember { mutableStateOf<String?>(null) }
    var errorMessage by remember { mutableStateOf<String?>(null) }

    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()

    LaunchedEffect(logLines.size) {
        if (logLines.isNotEmpty()) listState.animateScrollToItem(logLines.size - 1)
    }

    Scaffold(
        topBar = {
            TopAppBar(title = { Text("Templatize Project") })
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .padding(16.dp)
                .fillMaxSize()
        ) {
            if (!hasPermission) {
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        Text(
                            "This tool reads and writes Android project files on device " +
                                "storage, so it needs file access permission first.",
                            style = MaterialTheme.typography.bodyMedium,
                        )
                        Spacer(modifier = Modifier.height(12.dp))
                        Button(onClick = {
                            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                                val intent = Intent(
                                    Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                                    Uri.parse("package:${context.packageName}"),
                                )
                                allFilesLauncher.launch(intent)
                            } else {
                                legacyPermissionLauncher.launch(
                                    arrayOf(
                                        Manifest.permission.READ_EXTERNAL_STORAGE,
                                        Manifest.permission.WRITE_EXTERNAL_STORAGE,
                                    )
                                )
                            }
                        }) {
                            Text("Grant storage access")
                        }
                    }
                }
                return@Column
            }

            OutlinedTextField(
                value = projectName,
                onValueChange = { projectName = it },
                label = { Text("Project name to convert") },
                placeholder = { Text("MyApp") },
                supportingText = { Text("Looked up under $PROJECTS_DIR") },
                singleLine = true,
                enabled = !isRunning,
                modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
            )
            Spacer(modifier = Modifier.height(12.dp))
            OutlinedTextField(
                value = templateName,
                onValueChange = { templateName = it },
                label = { Text("Template name (written into template.json)") },
                placeholder = { Text("My Template") },
                singleLine = true,
                enabled = !isRunning,
                modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
            )
            Spacer(modifier = Modifier.height(12.dp))
            OutlinedTextField(
                value = moduleName,
                onValueChange = { moduleName = it },
                label = { Text("App module directory (default: app)") },
                singleLine = true,
                enabled = !isRunning,
                modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
            )
            Spacer(modifier = Modifier.height(8.dp))

            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = dryRun, onCheckedChange = { dryRun = it }, enabled = !isRunning)
                Text("Dry run (preview only, writes nothing)")
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = skipCleanup, onCheckedChange = { skipCleanup = it }, enabled = !isRunning)
                Text("Skip build/ and keystore cleanup")
            }

            Spacer(modifier = Modifier.height(12.dp))

            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(
                    onClick = {
                        errorMessage = null
                        summary = null
                        logLines.clear()
                        val dir = File(PROJECTS_DIR, projectName.trim())
                        val name = templateName.trim()
                        if (projectName.isBlank() || !dir.isDirectory) {
                            errorMessage = "\"$dir\" is not a directory that exists on this device."
                            return@Button
                        }
                        if (name.isBlank()) {
                            errorMessage = "Enter a template name to write into template.json."
                            return@Button
                        }
                        isRunning = true
                        scope.launch {
                            try {
                                val result = withContext(Dispatchers.IO) {
                                    createTemplateBundle(
                                        projectDir = dir,
                                        module = moduleName.trim().ifBlank { "app" },
                                        templateName = name,
                                        skipCleanup = skipCleanup,
                                        dryRun = dryRun,
                                        onLine = { line ->
                                            logLines.add(line)
                                        },
                                    )
                                }
                                summary = buildString {
                                    append("Modified ${result.report.changed.size}, ")
                                    append("skipped ${result.report.skipped.size}, ")
                                    append("removed ${result.report.removed.size}, ")
                                    append("${result.report.flagged.size} to review.\n")
                                    append("Bundle: ${result.outputDir}")
                                    if (result.cgtFile != null) append("\n.cgt file: ${result.cgtFile}")
                                }
                            } catch (e: Exception) {
                                errorMessage = "Conversion failed: ${e.message}"
                            } finally {
                                isRunning = false
                            }
                        }
                    },
                    enabled = !isRunning,
                ) {
                    Text(if (dryRun) "Preview conversion" else "Convert to .cgt template")
                }
                if (isRunning) {
                    CircularProgressIndicator(modifier = Modifier.padding(start = 4.dp))
                }
            }

            errorMessage?.let {
                Spacer(modifier = Modifier.height(8.dp))
                Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
            }

            summary?.let {
                Spacer(modifier = Modifier.height(12.dp))
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer),
                ) {
                    Text(it, modifier = Modifier.padding(16.dp), style = MaterialTheme.typography.bodyMedium)
                }
            }

            Spacer(modifier = Modifier.height(12.dp))
            Text("Log", style = MaterialTheme.typography.titleSmall)
            Spacer(modifier = Modifier.height(4.dp))
            LazyColumn(
                state = listState,
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth(),
            ) {
                items(logLines) { line ->
                    Text(
                        text = line,
                        fontFamily = FontFamily.Monospace,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(vertical = 1.dp),
                    )
                }
            }
        }
    }
}

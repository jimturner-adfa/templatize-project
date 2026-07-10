package org.appdevforall.templatizeproject.fragments

import android.app.AlertDialog
import android.graphics.Color
import android.os.Bundle
import android.util.Log
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageButton
import android.widget.ProgressBar
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.google.android.material.checkbox.MaterialCheckBox
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.itsaky.androidide.plugins.base.PluginFragmentHelper
import com.itsaky.androidide.plugins.services.IdeTemplateService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.appdevforall.templatizeproject.R
import org.appdevforall.templatizeproject.createTemplateBundle
import java.io.File

class TemplatizeProjectFragment : Fragment() {

    companion object {
        private const val PLUGIN_ID = "org.appdevforall.templatizeproject"
        private const val PROJECTS_DIR = "/sdcard/CodeOnTheGoProjects"
        private const val TAG = "TemplatizeProject"
    }

    private var projectNameLayout: TextInputLayout? = null
    private var projectNameInput: TextInputEditText? = null
    private var browseProjectsButton: ImageButton? = null
    private var templateNameInput: TextInputEditText? = null
    private var dryRunCheckbox: MaterialCheckBox? = null
    private var skipCleanupCheckbox: MaterialCheckBox? = null
    private var convertButton: MaterialButton? = null
    private var progressBar: ProgressBar? = null
    private var statusText: TextView? = null
    private var logText: TextView? = null

    override fun onGetLayoutInflater(savedInstanceState: Bundle?): LayoutInflater {
        val inflater = super.onGetLayoutInflater(savedInstanceState)
        return PluginFragmentHelper.getPluginInflater(PLUGIN_ID, inflater)
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View? {
        return inflater.inflate(R.layout.fragment_main, container, false)
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        projectNameLayout = view.findViewById(R.id.projectNameLayout)
        projectNameInput = view.findViewById(R.id.projectNameInput)
        browseProjectsButton = view.findViewById(R.id.browseProjectsButton)
        templateNameInput = view.findViewById(R.id.templateNameInput)
        dryRunCheckbox = view.findViewById(R.id.dryRunCheckbox)
        skipCleanupCheckbox = view.findViewById(R.id.skipCleanupCheckbox)
        convertButton = view.findViewById(R.id.convertButton)
        progressBar = view.findViewById(R.id.progressBar)
        statusText = view.findViewById(R.id.statusText)
        logText = view.findViewById(R.id.logText)

        convertButton?.setOnClickListener { onConvertClicked() }
        browseProjectsButton?.setOnClickListener { showProjectPicker() }
    }

    private fun showProjectPicker() {
        val activity = activity ?: return
        val projects = File(PROJECTS_DIR).listFiles { f -> f.isDirectory }
            ?.map { it.name }
            ?.sorted()
            ?.toTypedArray()
            ?: emptyArray()

        if (projects.isEmpty()) {
            showStatus("No projects found under $PROJECTS_DIR", isError = true)
            return
        }

        AlertDialog.Builder(activity)
            .setTitle("Select a project")
            .setItems(projects) { _, which ->
                projectNameInput?.setText(projects[which])
                projectNameInput?.setSelection(projects[which].length)
            }
            .show()
    }

    private fun onConvertClicked() {
        val projectName = projectNameInput?.text?.toString()?.trim().orEmpty()
        val templateName = templateNameInput?.text?.toString()?.trim().orEmpty()
        val dryRun = dryRunCheckbox?.isChecked ?: false
        val skipCleanup = skipCleanupCheckbox?.isChecked ?: false

        val projectDir = File(PROJECTS_DIR, projectName)
        if (projectName.isEmpty() || !projectDir.isDirectory) {
            showStatus("\"$projectDir\" is not a directory that exists on this device.", isError = true)
            return
        }
        if (templateName.isEmpty()) {
            showStatus("Enter a template name to write into template.json.", isError = true)
            return
        }

        setRunning(true)
        logText?.text = ""
        statusText?.visibility = View.GONE

        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    createTemplateBundle(
                        projectDir = projectDir,
                        module = "app",
                        templateName = templateName,
                        skipCleanup = skipCleanup,
                        dryRun = dryRun,
                        onLine = { line ->
                            appendLogLine(line)
                        },
                    )
                }
                val summary = buildString {
                    append("Modified ${result.report.changed.size}, ")
                    append("skipped ${result.report.skipped.size}, ")
                    append("removed ${result.report.removed.size}, ")
                    append("${result.report.flagged.size} to review.\n")
                    append("Bundle: ${result.outputDir}")
                    if (result.cgtFile != null) append("\n.cgt file: ${result.cgtFile}")
                }
                showStatus(summary, isError = false)

                if (result.cgtFile != null) {
                    promptInstall(result.cgtFile)
                }
            } catch (e: Exception) {
                Log.e(TAG, "Conversion failed", e)
                showStatus("Conversion failed: ${e.message}", isError = true)
            } finally {
                setRunning(false)
            }
        }
    }

    private fun promptInstall(cgtFile: File) {
        val activity = activity ?: return
        AlertDialog.Builder(activity)
            .setTitle("Install template?")
            .setMessage("Do you wish to install this template?")
            .setPositiveButton("Yes") { _, _ -> installTemplate(cgtFile) }
            .setNegativeButton("No", null)
            .show()
    }

    private fun installTemplate(cgtFile: File) {
        viewLifecycleOwner.lifecycleScope.launch {
            val installed = withContext(Dispatchers.IO) {
                runCatching {
                    val templateService = PluginFragmentHelper
                        .getServiceRegistry(PLUGIN_ID)
                        ?.get(IdeTemplateService::class.java)
                        ?: return@runCatching false
                    templateService.registerTemplate(cgtFile)
                }.onFailure { Log.e(TAG, "Install failed", it) }.getOrDefault(false)
            }
            appendLogLine(
                if (installed) "[OK]      Installed template: $cgtFile"
                else "[SKIP]    Could not install template: $cgtFile"
            )
        }
    }

    private fun appendLogLine(line: String) {
        activity?.runOnUiThread {
            val current = logText?.text
            logText?.text = if (current.isNullOrEmpty()) line else "$current\n$line"
        }
    }

    private fun showStatus(message: String, isError: Boolean) {
        statusText?.apply {
            text = message
            setTextColor(
                if (isError) Color.parseColor("#B3261E") else Color.parseColor("#0D6B42")
            )
            visibility = View.VISIBLE
        }
    }

    private fun setRunning(running: Boolean) {
        progressBar?.visibility = if (running) View.VISIBLE else View.GONE
        convertButton?.isEnabled = !running
        projectNameInput?.isEnabled = !running
        browseProjectsButton?.isEnabled = !running
        templateNameInput?.isEnabled = !running
        dryRunCheckbox?.isEnabled = !running
        skipCleanupCheckbox?.isEnabled = !running
    }

    override fun onDestroyView() {
        super.onDestroyView()
        projectNameLayout = null
        projectNameInput = null
        browseProjectsButton = null
        templateNameInput = null
        dryRunCheckbox = null
        skipCleanupCheckbox = null
        convertButton = null
        progressBar = null
        statusText = null
        logText = null
    }
}

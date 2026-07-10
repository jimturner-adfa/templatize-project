# Templatize Project

A [Code On the Go](https://github.com/appdevforall/CodeOnTheGo) plugin (CGP)
that converts an Android Studio project into a Code On the Go (`.cgt`)
template bundle by applying the substitutions described in "Template
Creation and Installation" — driven entirely from a tab inside the IDE
itself, with an option to install the result straight into the IDE's
template picker. The original project directory is never modified — the
plugin copies it into a new bundle directory next to it and templatizes the
copy.

This started as a `templatize_project.py` desktop script, was ported to a
standalone Kotlin/Compose Android app, and is now a proper Code On the Go
plugin so it runs inside the IDE with no separate app to install.

## UI

The plugin adds a **Templatize Project** item to the IDE's sidebar (under
"tools"), which opens a tab with:

- **Project** — read-only, always the project currently open in the IDE
  (via `IdeProjectService.getCurrentProject()`). There's nothing to type;
  if no project is open, conversion is disabled until one is.
- **Template name** — written into the generated `template.json`'s `name`
  field, and used as the template subdirectory / `templates.json` `path`
  entry.
- **Dry run** — preview the substitutions against a disposable copy without
  writing or deleting anything.
- **Skip cleanup** — skip `build/` and keystore removal.

**Convert to Template** stays disabled until a project is open and a
template name is entered. Tapping it runs the pipeline on a background
thread and streams a live log (mirroring the old CLI's `[OK]` / `[SKIP]` /
`[REMOVED]` / `[REVIEW]` output), followed by a summary with the output
paths. On a real (non-dry-run) conversion, an **Install Template** button
then appears below the log — so you can review the log first — and tapping
it registers the `.cgt` directly with the IDE via `IdeTemplateService`, so it
shows up immediately in the New Project / New File template picker.

## What the conversion does

For each run, it:

1. Creates a new output directory (`<project-dir>-cgt`, next to the
   project).
2. Copies the project into that directory as a subdirectory named after the
   template name, skipping `.git`, `.gradle`, `.cg`, `.idea`, `.claude`,
   `.androidide`, and `release.properties`.
3. Templatizes the copy:
   - Replaces concrete values (Gradle/AGP/Kotlin versions, package name, app
     name, SDK levels, Java compatibility levels) with Pebble tokens
     (`${{ TOKEN }}`).
   - Saves each modified file with a `.peb` suffix and removes the original.
   - Removes `build/` directories.
   - Deletes common keystore files (`*.jks`, `*.keystore`, `*.p12`) and flags
     other files that may contain machine-specific or personal information
     (`local.properties`, `google-services.json`, `key.properties`,
     `GoogleService-Info.plist`) for manual review.
4. Writes a `templates.json` at the top of the output directory, listing the
   template subdirectory.
5. Adds a `template/` directory inside the copied project containing
   `template.json` (the template's parameter/metadata schema, with `name`
   set to the entered template name) and a placeholder `thumb.png` (replace
   with a real thumbnail before shipping).
6. Zips the output directory into a `<template-name>.cgt` file next to it.
7. If the user opts in, registers that `.cgt` with the IDE via
   `IdeTemplateService.registerTemplate(...)`.

Both Kotlin DSL (`build.gradle.kts`) and Groovy DSL (`build.gradle`) projects
are supported.

## Files processed

- `gradle/wrapper/gradle-wrapper.properties` — Gradle version
- `settings.gradle.kts` / `settings.gradle` — `rootProject.name`
- `build.gradle.kts` / `build.gradle` (root) — AGP version
- `<module>/build.gradle.kts` / `build.gradle` — AGP/Kotlin plugin versions,
  namespace, applicationId, compileSdk, minSdk, targetSdk, Java source/target
  compatibility, `kotlinOptions.jvmTarget`
- `<module>/src/main/res/values/strings.xml` — `app_name`
- `<module>/src/main/**/*.java`, `*.kt` — package name (declarations and
  imports), plus flattening the package directory tree into `PACKAGE_NAME`

Assumes the app module is named `app`.

## Pebble whitespace quirk

A line/segment that ends with a Pebble token must be followed by at least
one character of whitespace, or the parser eats the next character (often a
newline, quote, semicolon, or dot). The conversion inserts a sacrificial
space after every inserted token — after the closing quote when the token is
immediately followed by one, so the quote itself isn't eaten.

## Project layout

- `Templatizer.kt` — the substitution pipeline, pure `java.io.File` logic
  with no dependency on the plugin API (so it's easy to reason about and
  test in isolation), plus the `.cgt` bundle writer/zipper.
- `TemplatizeProjectPlugin.kt` — the `IPlugin` entry point: registers the
  sidebar item and the main editor tab, and provides in-IDE tooltip help.
- `fragments/TemplatizeProjectFragment.kt` — the tab's UI: the input form,
  background execution, live log, and the install-template confirmation
  flow via `IdeTemplateService`.
- `libs/plugin-api.jar`, `libs/gradle-plugin.jar` — Code On the Go's plugin
  SDK and plugin-builder Gradle plugin (`com.itsaky.androidide.plugins.build`).
  Not published to Maven Central; copied from a working Code On the Go
  plugin checkout. Refresh by rebuilding them from a CodeOnTheGo source
  checkout if the plugin API changes upstream.

## Building

```
./gradlew assemblePlugin        # release .cgp -> build/plugin/templatize-project.cgp
./gradlew assemblePluginDebug   # debug .cgp   -> build/plugin/templatize-project-debug.cgp
```

Install the resulting `.cgp` through Code On the Go's Plugin Manager.

## Next steps

After running a conversion, inspect the generated `.peb` files in the output
bundle to confirm the substitutions and spacing look right, and replace the
placeholder `thumb.png` before distributing the `.cgt` file.

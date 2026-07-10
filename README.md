# Templatize Project

An Android app (Kotlin + Jetpack Compose) that packages an Android Studio
project into a [Code On the Go](https://codeonthego.app/) (`.cgt`) template
bundle by applying the substitutions described in "Template Creation and
Installation". It's meant to be built and run on-device, e.g. from inside
Code On the Go itself, so a project can be templatized without leaving the
IDE. The original project directory is never modified — the app copies it
into a new bundle directory next to it and templatizes the copy.

This is a Kotlin port of what was previously a standalone
`templatize_project.py` script; the substitution logic and output layout are
the same, just driven from a UI and running as an on-device app instead of a
desktop CLI.

## UI

Launching the app shows a form with:

- **Project name to convert** — the project's directory name under
  `/sdcard/CodeOnTheGoProjects` (Code On the Go's fixed projects root), e.g.
  `MyApp` for `/sdcard/CodeOnTheGoProjects/MyApp`.
- **Template name** — written into the generated `template.json`'s `name`
  field, and used as the template subdirectory / `templates.json` `path`
  entry.
- **App module directory** — defaults to `app`.
- **Dry run** — preview the substitutions against a disposable copy without
  writing or deleting anything.
- **Skip cleanup** — skip `build/` and keystore removal.

On first launch the app requests storage access (All Files Access on
Android 11+, legacy read/write permissions below that), since it needs to
read and write project files anywhere on device storage.

Tapping **Convert to .cgt template** runs the pipeline on a background
thread and streams a live log (mirroring the old CLI's `[OK]` / `[SKIP]` /
`[REMOVED]` / `[REVIEW]` output) into a scrollable list, followed by a
summary card with the output paths.

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

## Pebble whitespace quirk

A line/segment that ends with a Pebble token must be followed by at least
one character of whitespace, or the parser eats the next character (often a
newline, quote, semicolon, or dot). The conversion inserts a sacrificial
space after every inserted token — after the closing quote when the token is
immediately followed by one, so the quote itself isn't eaten.

## Project layout

- `Templatizer.kt` — the substitution pipeline (Kotlin port of the former
  Python script's logic), plus the `.cgt` bundle writer/zipper.
- `MainActivity.kt` — the Compose UI: input form, permission handling,
  background execution, and live log/result display.

## Building

```
./gradlew assembleDebug
```

The resulting APK can be installed and run like any other Android app,
including inside Code On the Go's own build/run flow.

## Next steps

After running a conversion, inspect the generated `.peb` files in the output
bundle to confirm the substitutions and spacing look right, and replace the
placeholder `thumb.png` before distributing the `.cgt` file.

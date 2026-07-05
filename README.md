# templatize-project

`templatize_project.py` packages an Android Studio project into a
[Code On the Go](https://codeonthego.app/) (`.cgt`) template bundle by
applying the substitutions described in "Template Creation and
Installation". The original project directory is never modified — the
script copies it into a new bundle directory and templatizes the copy.

For each run, it:

1. Creates a new output directory (default: `<project-dir>-cgt`, next to the
   project).
2. Copies the project into that directory as a subdirectory (default name:
   the project directory's name), skipping `.git`.
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
   `template.json` (the template's parameter/metadata schema) and a
   placeholder `icon.png` (replace with a real icon before shipping).

Both Kotlin DSL (`build.gradle.kts`) and Groovy DSL (`build.gradle`) projects
are supported.

## Usage

```
python templatize_project.py <path-to-android-project> [--module app] [--dry-run] [--skip-cleanup] [--output-dir DIR] [--template-name NAME]
```

- `--module` — app module directory name (default: `app`)
- `--dry-run` — preview changes against the original project directory
  without creating the output directory or writing/deleting anything
- `--skip-cleanup` — skip `build/` and keystore removal
- `--output-dir` — directory to create for the `.cgt` bundle (default:
  `<project-dir>-cgt`)
- `--template-name` — name of the template subdirectory / `path` entry in
  `templates.json` (default: the project directory's name)

## Files processed

- `gradle/wrapper/gradle-wrapper.properties` — Gradle version
- `settings.gradle.kts` — `rootProject.name`
- `build.gradle.kts` / `build.gradle` (root) — AGP version
- `<module>/build.gradle.kts` / `build.gradle` — AGP/Kotlin plugin versions,
  namespace, applicationId, compileSdk, minSdk, targetSdk, Java source/target
  compatibility, `kotlinOptions.jvmTarget`
- `<module>/src/main/res/values/strings.xml` — `app_name`
- `<module>/src/main/java/MainActivity.kt` / `MainActivity.java` — package
  name

## Pebble whitespace quirk

A line/segment that ends with a Pebble token must be followed by at least
one character of whitespace, or the parser eats the next character (often a
newline, quote, semicolon, or dot). The script inserts a sacrificial space
after every inserted token — after the closing quote when the token is
immediately followed by one, so the quote itself isn't eaten.

## Next steps

After running the script, inspect each generated `.peb` file in the output
directory to confirm the substitutions and spacing look right, replace the
placeholder `icon.png`, then package the output directory into a `.cgt`
file:

```
cd <output-dir> && zip -r -9 -D -X <destination>/<filename>.cgt *
```

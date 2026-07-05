#!/usr/bin/env python3
"""
templatize_project.py

Converts an Android Studio project into a Code On the Go (.cgt) template
by applying the changes described in "Template Creation and Installation":

  - Substitutes concrete values (versions, package name, app name, sdk
    levels, java compat levels) in the affected files with Pebble tokens
    (${{ TOKEN }}).
  - Saves each modified file with a .peb suffix and removes the original.
  - Removes build/ directories.
  - Deletes common keystore files and flags other files that may contain
    personal information for manual review.

IMPORTANT Pebble quirk handled throughout: a line/segment that ends with
a Pebble token must be followed by at least one space, or the parser will
eat the next character (often a newline, quote, semicolon, or dot). This
script inserts a single "sacrificial" space after every inserted token
(two spaces for the rootProject.name line, per the explicit note in the
source document) so the character that follows survives rendering.

Usage:
    python templatize_project.py <path-to-android-project> [--module app] [--dry-run]
"""

import argparse
import base64
import json
import re
import shutil
import sys
from pathlib import Path

TOKEN = "{name} "  # placeholder, formatted per-use as ${{{{{name}}}}}


def token(name: str) -> str:
    return "${{" + name + "}}"


class Report:
    def __init__(self):
        self.changed = []
        self.skipped = []
        self.removed = []
        self.flagged = []

    def ok(self, msg):
        self.changed.append(msg)
        print(f"  [OK]      {msg}")

    def skip(self, msg):
        self.skipped.append(msg)
        print(f"  [SKIP]    {msg}")

    def remove(self, msg):
        self.removed.append(msg)
        print(f"  [REMOVED] {msg}")

    def flag(self, msg):
        self.flagged.append(msg)
        print(f"  [REVIEW]  {msg}")


def write_peb(path: Path, new_text: str, report: Report, dry_run: bool):
    peb_path = path.with_name(path.name + ".peb")
    report.ok(f"{path} -> {peb_path.name}")
    if dry_run:
        return
    with open(peb_path, "w", encoding="utf-8", newline="") as f:
        f.write(new_text)
    path.unlink()


def sub_line_end_token(text: str, pattern: str, repl_token_name: str,
                        trailing_spaces: int = 1, space_after_suffix: bool = False):
    """
    Replace the value in group 2 of `pattern` with a Pebble token, inserting
    `trailing_spaces` sacrificial spaces immediately after the token unless
    `space_after_suffix` is set, in which case the spaces go after group 3
    instead (used when group 3 is a closing quote, so the quote sits right
    against the token and doesn't get eaten). `pattern` must have exactly
    three capturing groups: (prefix)(value-to-replace)(suffix). Always
    applied with re.MULTILINE so ^/$ anchors in patterns match per-line
    rather than only at the start/end of the whole file.
    """
    tok = token(repl_token_name)

    def _repl(m):
        if space_after_suffix:
            return m.group(1) + tok + m.group(3) + (" " * trailing_spaces)
        return m.group(1) + tok + (" " * trailing_spaces) + m.group(3)

    new_text, n = re.subn(pattern, _repl, text, flags=re.MULTILINE)
    return new_text, n


def sub_first_match_token(text: str, patterns, repl_token_name: str,
                           trailing_spaces: int = 1, space_after_suffix: bool = False):
    """
    Try each candidate pattern (Kotlin DSL vs. Groovy DSL variants, e.g.) in
    turn and apply the first one that matches. Since the two DSLs use
    mutually exclusive syntax (`=` and double quotes vs. bare assignment and
    single/double quotes), only one pattern is ever expected to match a
    given file.
    """
    for pattern in patterns:
        new_text, n = sub_line_end_token(text, pattern, repl_token_name, trailing_spaces, space_after_suffix)
        if n:
            return new_text, n
    return text, 0


def process_gradle_wrapper(project_dir: Path, report: Report, dry_run: bool):
    path = project_dir / "gradle" / "wrapper" / "gradle-wrapper.properties"
    if not path.exists():
        report.skip(f"{path} not found")
        return
    text = path.read_text(encoding="utf-8")
    new_text, n = sub_line_end_token(
        text,
        r"(distributionUrl=https\\://services\.gradle\.org/distributions/gradle-)([^-\s]+)(-bin\.zip)",
        "GRADLE_VERSION",
    )
    if n == 0:
        report.skip(f"{path}: distributionUrl pattern not found, no changes made")
        return
    write_peb(path, new_text, report, dry_run)


def process_settings_gradle(project_dir: Path, report: Report, dry_run: bool):
    path = project_dir / "settings.gradle.kts"
    if not path.exists():
        report.skip(f"{path} not found")
        return
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    n = 0
    for i, line in enumerate(lines):
        m = re.match(r'^([ \t]*rootProject\.name[ \t]*=[ \t]*")([^"]*)("[ \t]*)(\r?\n)?$', line)
        if m:
            newline = m.group(4) or ""
            lines[i] = f'{m.group(1)}{token("APP_NAME")}"  {newline}'
            n += 1
    if n == 0:
        report.skip(f"{path}: rootProject.name pattern not found, no changes made")
        return
    write_peb(path, "".join(lines), report, dry_run)


def process_root_build_gradle(project_dir: Path, report: Report, dry_run: bool):
    path = project_dir / "build.gradle.kts"
    if not path.exists():
        path = project_dir / "build.gradle"
    if not path.exists():
        report.skip(f"{project_dir / 'build.gradle.kts'} or build.gradle not found")
        return
    text = path.read_text(encoding="utf-8")
    new_text, n = sub_first_match_token(
        text,
        [
            r'(id\("com\.android\.(?:application|library)"\)\s+apply\s+false\s+version\s+")([^"]+)(")',
            r"(id\s+'com\.android\.(?:application|library)'\s+apply\s+false\s+version\s+')([^']+)(')",
            r'(id\s+"com\.android\.(?:application|library)"\s+apply\s+false\s+version\s+")([^"]+)(")',
        ],
        "AGP_VERSION",
        space_after_suffix=True,
    )
    if n == 0:
        report.skip(f"{path}: no 'apply false version' plugin lines found, no changes made")
        return
    report.ok(f"{path}: replaced {n} AGP version occurrence(s)")
    write_peb(path, new_text, report, dry_run)


def process_app_build_gradle(project_dir: Path, module: str, report: Report, dry_run: bool):
    path = project_dir / module / "build.gradle.kts"
    if not path.exists():
        path = project_dir / module / "build.gradle"
    if not path.exists():
        report.skip(f"{project_dir / module / 'build.gradle.kts'} or build.gradle not found")
        return
    text = path.read_text(encoding="utf-8")
    total = 0

    # com.android.application plugin version (Kotlin DSL, then Groovy single/double quotes)
    text, n = sub_first_match_token(
        text,
        [
            r'(id\("com\.android\.application"\)\s+version\s+")([^"]+)(")',
            r"(id\s+'com\.android\.application'\s+version\s+')([^']+)(')",
            r'(id\s+"com\.android\.application"\s+version\s+")([^"]+)(")',
        ],
        "AGP_VERSION",
        space_after_suffix=True,
    )
    total += n

    # Kotlin plugin version, wrapped in a Pebble LANGUAGE conditional. Tries
    # Kotlin DSL's kotlin("android") shorthand, then Groovy's `id '...'` form
    # (either the org.jetbrains.kotlin.android id or the kotlin-android alias).
    kts_kotlin_pattern = re.compile(
        r'^([ \t]*)kotlin\("android"\)\s+version\s+"([^"]+)"[ \t]*\r?\n?',
        re.MULTILINE,
    )
    groovy_kotlin_pattern = re.compile(
        r"^([ \t]*)id\s+(['\"])(?:org\.jetbrains\.kotlin\.android|kotlin-android)\2"
        r"\s+version\s+\2([^'\"]+)\2[ \t]*\r?\n?",
        re.MULTILINE,
    )
    m = kts_kotlin_pattern.search(text)
    if m:
        indent = m.group(1)
        plugin_line = f'kotlin("android") version "{token("KOTLIN_VERSION")}" '
    else:
        m = groovy_kotlin_pattern.search(text)
        if m:
            indent, quote = m.group(1), m.group(2)
            plugin_line = (
                f"id {quote}org.jetbrains.kotlin.android{quote} "
                f"version {quote}{token('KOTLIN_VERSION')}{quote} "
            )

    if m:
        replacement = (
            f'{indent}${{% if LANGUAGE == \'kotlin\' %}} \n'
            f'{indent}{plugin_line}\n'
            f'{indent}${{% endif %}} \n'
        )
        text = text[:m.start()] + replacement + text[m.end():]
        total += 1
        report.ok(f"{path}: wrapped Kotlin plugin declaration in LANGUAGE conditional")
    else:
        report.skip(f"{path}: Kotlin plugin line not found (ok for Java-only projects)")

    text, n = sub_first_match_token(
        text,
        [
            r'(namespace\s*=\s*")([^"]+)(")',
            r"(namespace\s+')([^']+)(')",
            r'(namespace\s+")([^"]+)(")',
        ],
        "PACKAGE_NAME",
        space_after_suffix=True,
    )
    total += n
    text, n = sub_first_match_token(
        text,
        [
            r'(compileSdk\s*=\s*)(\d+)()',
            r'(compileSdk(?:Version)?\s+)(\d+)()',
        ],
        "COMPILE_SDK",
    )
    total += n
    text, n = sub_first_match_token(
        text,
        [
            r'(applicationId\s*=\s*")([^"]+)(")',
            r"(applicationId\s+')([^']+)(')",
            r'(applicationId\s+")([^"]+)(")',
        ],
        "PACKAGE_NAME",
        space_after_suffix=True,
    )
    total += n
    text, n = sub_first_match_token(
        text,
        [
            r'(minSdk\s*=\s*)(\d+)()',
            r'(minSdk(?:Version)?\s+)(\d+)()',
        ],
        "MIN_SDK",
    )
    total += n
    text, n = sub_first_match_token(
        text,
        [
            r'(targetSdk\s*=\s*)(\d+)()',
            r'(targetSdk(?:Version)?\s+)(\d+)()',
        ],
        "TARGET_SDK",
    )
    total += n
    text, n = sub_first_match_token(
        text,
        [
            r'(sourceCompatibility\s*=\s*)(JavaVersion\.\w+|[\d.]+)()',
            r'(sourceCompatibility\s+)(JavaVersion\.\w+|[\d.]+)()',
        ],
        "JAVA_SOURCE_COMPAT",
    )
    total += n
    text, n = sub_first_match_token(
        text,
        [
            r'(targetCompatibility\s*=\s*)(JavaVersion\.\w+|[\d.]+)()',
            r'(targetCompatibility\s+)(JavaVersion\.\w+|[\d.]+)()',
        ],
        "JAVA_TARGET_COMPAT",
    )
    total += n
    text, n = sub_first_match_token(
        text,
        [
            r'(jvmTarget\s*=\s*")([^"]+)(")',
            r"(jvmTarget\s*=\s*')([^']+)(')",
        ],
        "JAVA_TARGET_COMPAT",
        space_after_suffix=True,
    )
    total += n

    if total == 0:
        report.skip(f"{path}: no recognized patterns found, no changes made")
        return
    report.ok(f"{path}: made {total} substitution(s)")
    write_peb(path, text, report, dry_run)


def process_strings_xml(project_dir: Path, module: str, report: Report, dry_run: bool):
    path = project_dir / module / "src" / "main" / "res" / "values" / "strings.xml"
    if not path.exists():
        report.skip(f"{path} not found")
        return
    text = path.read_text(encoding="utf-8")
    new_text, n = sub_line_end_token(
        text,
        r'(<string name="app_name">)([^<]*)(</string>)',
        "APP_NAME",
    )
    if n == 0:
        report.skip(f"{path}: app_name string not found, no changes made")
        return
    write_peb(path, new_text, report, dry_run)


def process_main_activity_kt(project_dir: Path, module: str, report: Report, dry_run: bool):
    path = project_dir / module / "src" / "main" / "java" / "MainActivity.kt"
    if not path.exists():
        report.skip(f"{path} not found (ok if this project is Java-only)")
        return
    text = path.read_text(encoding="utf-8")
    pkg_match = re.search(r'^package\s+([\w.]+)\s*$', text, re.MULTILINE)
    if not pkg_match:
        report.skip(f"{path}: package declaration not found, no changes made")
        return
    package_name = pkg_match.group(1)

    text, n = sub_line_end_token(text, r'^(package\s+)([\w.]+)([ \t]*)$', "PACKAGE_NAME")
    total = n

    # Optional: import <package>.databinding.XxxBinding
    import_pattern = re.compile(
        rf'^(import\s+){re.escape(package_name)}(\.databinding\.\w+)([ \t]*)$',
        re.MULTILINE,
    )
    text, n2 = import_pattern.subn(
        lambda m: f'{m.group(1)}{token("PACKAGE_NAME")} {m.group(2)}{m.group(3)}',
        text,
    )
    total += n2

    if total == 0:
        report.skip(f"{path}: no substitutions made")
        return
    report.ok(f"{path}: made {total} substitution(s)")
    write_peb(path, text, report, dry_run)


def process_main_activity_java(project_dir: Path, module: str, report: Report, dry_run: bool):
    path = project_dir / module / "src" / "main" / "java" / "MainActivity.java"
    if not path.exists():
        report.skip(f"{path} not found (ok if this project is Kotlin-only)")
        return
    text = path.read_text(encoding="utf-8")
    pkg_match = re.search(r'^package[ \t]+([\w.]+)[ \t]*;[ \t]*$', text, re.MULTILINE)
    if not pkg_match:
        report.skip(f"{path}: package declaration not found, no changes made")
        return
    package_name = pkg_match.group(1)

    # package <pkg>; -> package ${{PACKAGE_NAME}} ;
    # (space before the semicolon protects it from being eaten)
    text, n = re.subn(
        r'^(package[ \t]+)([\w.]+)([ \t]*;[ \t]*)$',
        lambda m: f'{m.group(1)}{token("PACKAGE_NAME")} ;',
        text,
        flags=re.MULTILINE,
    )
    total = n

    import_pattern = re.compile(
        rf'^(import[ \t]+){re.escape(package_name)}(\.databinding\.\w+)([ \t]*;[ \t]*)$',
        re.MULTILINE,
    )
    text, n2 = import_pattern.subn(
        lambda m: f'{m.group(1)}{token("PACKAGE_NAME")} {m.group(2)};',
        text,
    )
    total += n2

    if total == 0:
        report.skip(f"{path}: no substitutions made")
        return
    report.ok(f"{path}: made {total} substitution(s)")
    write_peb(path, text, report, dry_run)


def cleanup_build_dirs(project_dir: Path, report: Report, dry_run: bool):
    # Match both the original files and any already-renamed .peb versions,
    # since this runs after the file substitution steps.
    module_roots = set()
    for pattern in ("build.gradle.kts", "build.gradle",
                    "build.gradle.kts.peb", "build.gradle.peb"):
        module_roots |= {p.parent for p in project_dir.rglob(pattern)}
    module_roots.add(project_dir)
    for mod in sorted(module_roots):
        build_dir = mod / "build"
        if build_dir.is_dir():
            report.remove(str(build_dir))
            if not dry_run:
                shutil.rmtree(build_dir)


def cleanup_keystores(project_dir: Path, report: Report, dry_run: bool):
    patterns = ["*.jks", "*.keystore", "*.p12"]
    for pattern in patterns:
        for f in project_dir.rglob(pattern):
            report.remove(str(f))
            if not dry_run:
                f.unlink()


def flag_personal_info_files(project_dir: Path, report: Report):
    candidates = ["local.properties", "google-services.json", "key.properties",
                  "GoogleService-Info.plist"]
    for name in candidates:
        for f in project_dir.rglob(name):
            report.flag(f"{f} may contain machine-specific or personal information; review/delete manually")


def run_pipeline(project_dir: Path, module: str, dry_run: bool, skip_cleanup: bool, report: Report):
    print(f"\n=== Templatizing {project_dir} ===\n")
    if dry_run:
        print("(dry run: no files will be modified)\n")

    print("-- gradle/wrapper/gradle-wrapper.properties --")
    process_gradle_wrapper(project_dir, report, dry_run)

    print("\n-- settings.gradle.kts --")
    process_settings_gradle(project_dir, report, dry_run)

    print("\n-- build.gradle.kts / build.gradle (root) --")
    process_root_build_gradle(project_dir, report, dry_run)

    print(f"\n-- {module}/build.gradle.kts / build.gradle --")
    process_app_build_gradle(project_dir, module, report, dry_run)

    print(f"\n-- {module}/src/main/res/values/strings.xml --")
    process_strings_xml(project_dir, module, report, dry_run)

    print(f"\n-- {module}/src/main/java/MainActivity.kt --")
    process_main_activity_kt(project_dir, module, report, dry_run)

    print(f"\n-- {module}/src/main/java/MainActivity.java --")
    process_main_activity_java(project_dir, module, report, dry_run)

    if not skip_cleanup:
        print("\n-- Removing build/ directories --")
        cleanup_build_dirs(project_dir, report, dry_run)

        print("\n-- Removing keystore files --")
        cleanup_keystores(project_dir, report, dry_run)

        print("\n-- Flagging files that may contain personal information --")
        flag_personal_info_files(project_dir, report)


# A minimal valid 1x1 transparent PNG, used as a stand-in template icon until
# a real one is supplied.
PLACEHOLDER_ICON_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YA"
    "AAAASUVORK5CYII="
)


def build_template_json() -> dict:
    return {
        "name": "Ext Activity",
        "description": "Creates a new test activity",
        "version": "0.1",
        "parameters": {
            "required": {
                "appName": {"identifier": "APP_NAME"},
                "packageName": {"identifier": "PACKAGE_NAME"},
                "saveLocation": {"identifier": "SAVE_LOCATION"},
            },
            "optional": {
                "language": {"identifier": "LANGUAGE"},
                "minsdk": {"identifier": "MIN_SDK"},
            },
            "system": {
                "agpVersion": {"identifier": "AGP_VERSION"},
                "kotlinVersion": {"identifier": "KOTLIN_VERSION"},
                "gradleVersion": {"identifier": "GRADLE_VERSION"},
                "compileSdk": {"identifier": "COMPILE_SDK"},
                "targetSdk": {"identifier": "TARGET_SDK"},
                "javaSourceCompat": {"identifier": "JAVA_SOURCE_COMPAT"},
                "javaTargetCompat": {"identifier": "JAVA_TARGET_COMPAT"},
                "javaTarget": {"identifier": "JAVA_TARGET"},
            },
        },
    }


def create_template_bundle(project_dir: Path, module: str, output_dir: Path,
                            template_name: str, skip_cleanup: bool, dry_run: bool,
                            report: Report):
    dest = output_dir / template_name

    if dry_run:
        print(f"\n=== Dry run: would create {output_dir} ===")
        print(f"  would copy {project_dir} -> {dest}")
        run_pipeline(project_dir, module, dry_run, skip_cleanup, report)
        print(f"\n  would write {output_dir / 'templates.json'}")
        print(f"  would write {dest / 'template' / 'template.json'}")
        print(f"  would write {dest / 'template' / 'icon.png'}")
        return

    if dest.exists():
        print(f"Error: {dest} already exists", file=sys.stderr)
        sys.exit(1)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(project_dir, dest, ignore=shutil.ignore_patterns(".git"))
    report.ok(f"{project_dir} -> {dest}")

    run_pipeline(dest, module, dry_run, skip_cleanup, report)

    templates_json = output_dir / "templates.json"
    templates_json.write_text(
        json.dumps({"templates": [{"path": template_name}]}, indent=2) + "\n",
        encoding="utf-8",
    )
    report.ok(str(templates_json))

    template_dir = dest / "template"
    template_dir.mkdir(exist_ok=True)

    template_json = template_dir / "template.json"
    template_json.write_text(json.dumps(build_template_json(), indent=4) + "\n", encoding="utf-8")
    report.ok(str(template_json))

    icon_png = template_dir / "icon.png"
    icon_png.write_bytes(base64.b64decode(PLACEHOLDER_ICON_PNG_B64))
    report.ok(f"{icon_png} (placeholder icon, replace with a real one)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project_dir", type=Path, help="Path to the Android Studio project root")
    parser.add_argument("--module", default="app", help="App module directory name (default: app)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing/deleting anything")
    parser.add_argument("--skip-cleanup", action="store_true", help="Skip build/ and keystore removal")
    parser.add_argument("--output-dir", type=Path, default=None,
                         help="Directory to create for the .cgt bundle (default: <project-dir>-cgt next to the project)")
    parser.add_argument("--template-name", default=None,
                         help="Name of the template subdirectory/'path' entry in templates.json (default: project directory name)")
    args = parser.parse_args()

    project_dir = args.project_dir.resolve()
    if not project_dir.is_dir():
        print(f"Error: {project_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    output_dir = (args.output_dir or project_dir.parent / f"{project_dir.name}-cgt").resolve()
    template_name = args.template_name or project_dir.name

    report = Report()

    create_template_bundle(project_dir, args.module, output_dir, template_name,
                            args.skip_cleanup, args.dry_run, report)

    print("\n=== Summary ===")
    print(f"  Modified:  {len(report.changed)}")
    print(f"  Skipped:   {len(report.skipped)}")
    print(f"  Removed:   {len(report.removed)}")
    print(f"  To review: {len(report.flagged)}")
    print(
        "\nNext step: inspect the generated .peb files in "
        f"{output_dir} to confirm the Pebble substitutions and spacing look "
        "right, then package the directory into a .cgt file, e.g.:\n"
        f"  cd {output_dir} && zip -r -9 -D -X <destination>/<filename>.cgt *\n"
    )


if __name__ == "__main__":
    main()

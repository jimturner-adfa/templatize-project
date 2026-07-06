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
import subprocess
import sys
import tempfile
from pathlib import Path

COPY_IGNORE = shutil.ignore_patterns(".git", ".gradle", ".cg", ".idea", ".claude")

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
        path = project_dir / "settings.gradle"
    if not path.exists():
        report.skip(f"{project_dir / 'settings.gradle.kts'} or settings.gradle not found")
        return
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    n = 0
    for i, line in enumerate(lines):
        m = re.match(
            r'^([ \t]*rootProject\.name[ \t]*=[ \t]*)(["\'])([^"\']*)(\2[ \t]*)(\r?\n)?$', line
        )
        if m:
            quote = m.group(2)
            newline = m.group(5) or ""
            lines[i] = f'{m.group(1)}{quote}{token("APP_NAME")}{quote}  {newline}'
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


def process_app_build_gradle(project_dir: Path, module: str, report: Report, dry_run: bool,
                              wrap_kotlin_in_language_conditional: bool = True):
    path = project_dir / module / "build.gradle.kts"
    if not path.exists():
        path = project_dir / module / "build.gradle"
    if not path.exists():
        report.skip(f"{project_dir / module / 'build.gradle.kts'} or build.gradle not found")
        return None
    text = path.read_text(encoding="utf-8")
    total = 0

    namespace_patterns = [
        r'(namespace\s*=\s*")([^"]+)(")',
        r"(namespace\s+')([^']+)(')",
        r'(namespace\s+")([^"]+)(")',
    ]
    application_id_patterns = [
        r'(applicationId\s*=\s*")([^"]+)(")',
        r"(applicationId\s+')([^']+)(')",
        r'(applicationId\s+")([^"]+)(")',
    ]

    def _find_first(patterns):
        for pattern in patterns:
            m = re.search(pattern, text, re.MULTILINE)
            if m:
                return m.group(2)
        return None

    # Captured before substitution runs below, so it reflects the concrete
    # package name even though the build.gradle occurrence gets tokenized.
    base_package = _find_first(namespace_patterns) or _find_first(application_id_patterns)

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
        if wrap_kotlin_in_language_conditional:
            replacement = (
                f'{indent}${{% if LANGUAGE == \'kotlin\' %}} \n'
                f'{indent}{plugin_line}\n'
                f'{indent}${{% endif %}} \n'
            )
            report.ok(f"{path}: wrapped Kotlin plugin declaration in LANGUAGE conditional")
        else:
            replacement = f'{indent}{plugin_line}\n'
            report.ok(
                f"{path}: templatized Kotlin plugin version "
                "(single-language project, no LANGUAGE conditional needed)"
            )
        text = text[:m.start()] + replacement + text[m.end():]
        total += 1
    else:
        report.skip(f"{path}: Kotlin plugin line not found (ok for Java-only projects)")

    text, n = sub_first_match_token(
        text,
        namespace_patterns,
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
        application_id_patterns,
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
        "JAVA_TARGET",
        space_after_suffix=True,
    )
    total += n

    if total == 0:
        report.skip(f"{path}: no recognized patterns found, no changes made")
        return base_package
    report.ok(f"{path}: made {total} substitution(s)")
    write_peb(path, text, report, dry_run)
    return base_package


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


def process_java_kt_sources(project_dir: Path, module: str, base_package: str,
                             report: Report, dry_run: bool):
    """
    Replace every occurrence of the app's base package (as found in the
    module build.gradle) with the PACKAGE_NAME token across all .java and
    .kt files under the module's source tree - both in `package ...`
    declarations and in `import ...` statements that reference the base
    package or one of its subpackages (e.g. `<base>.databinding.XxxBinding`).
    """
    if not base_package:
        report.skip(
            f"{project_dir / module}: no base package found in build.gradle, "
            "skipping .java/.kt package substitution"
        )
        return

    src_main = project_dir / module / "src" / "main"
    source_files = sorted(src_main.rglob("*.java")) + sorted(src_main.rglob("*.kt"))
    if not source_files:
        report.skip(f"{src_main}: no .java/.kt source files found")
        return

    base_escaped = re.escape(base_package)

    for path in source_files:
        text = path.read_text(encoding="utf-8")
        is_java = path.suffix == ".java"

        if is_java:
            package_pattern = rf'^([ \t]*package[ \t]+)({base_escaped})((?:\.[\w.]*)?[ \t]*;[ \t]*)$'
            import_pattern = rf'^([ \t]*import[ \t]+)({base_escaped})((?:\.[\w.*]*)?[ \t]*;[ \t]*)$'
        else:
            package_pattern = rf'^([ \t]*package[ \t]+)({base_escaped})((?:\.[\w.]*)?[ \t]*)$'
            import_pattern = rf'^([ \t]*import[ \t]+)({base_escaped})((?:\.[\w.*]*)?[ \t]*)$'

        text, n1 = sub_line_end_token(text, package_pattern, "PACKAGE_NAME")
        # No sacrificial space here: unlike other substitutions, imports are
        # always followed by more package/class path text, not eaten by Pebble.
        text, n2 = sub_line_end_token(text, import_pattern, "PACKAGE_NAME", trailing_spaces=0)
        total = n1 + n2

        if total == 0:
            report.skip(f"{path}: no substitutions made")
            continue
        report.ok(f"{path}: made {total} substitution(s)")
        write_peb(path, text, report, dry_run)


def flatten_package_directory(project_dir: Path, module: str, base_package: str,
                               report: Report, dry_run: bool):
    """
    Move the base package's directory tree under src/main/java (e.g.
    com/example/jme2, including any subpackages nested beneath it) into a
    single directory literally named PACKAGE_NAME, then remove whatever
    now-empty parent directories (e.g. com/example) it leaves behind.
    """
    if not base_package:
        report.skip("no base package found, skipping java/ package directory flattening")
        return

    java_root = project_dir / module / "src" / "main" / "java"
    if not java_root.is_dir():
        report.skip(f"{java_root} not found")
        return

    pkg_dir = java_root.joinpath(*base_package.split("."))
    if not pkg_dir.is_dir():
        report.skip(f"{pkg_dir} not found, skipping java/ package directory flattening")
        return

    target_dir = java_root / "PACKAGE_NAME"
    report.ok(f"{pkg_dir} -> {target_dir}")
    if dry_run:
        return

    shutil.move(str(pkg_dir), str(target_dir))

    parent = pkg_dir.parent
    while parent != java_root and parent.is_dir() and not any(parent.iterdir()):
        next_parent = parent.parent
        parent.rmdir()
        parent = next_parent


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


def run_gradlew_clean(project_dir: Path, report: Report, dry_run: bool):
    gradlew = project_dir / ("gradlew.bat" if sys.platform == "win32" else "gradlew")
    if not gradlew.exists():
        report.skip(f"{gradlew} not found, skipping gradlew clean")
        return
    if dry_run:
        report.ok(f"would run {gradlew} clean")
        return
    result = subprocess.run([str(gradlew), "clean"], cwd=project_dir)
    if result.returncode != 0:
        print(f"Error: {gradlew} clean failed with exit code {result.returncode}", file=sys.stderr)
        sys.exit(1)
    report.ok(f"{gradlew} clean")


def flag_personal_info_files(project_dir: Path, report: Report):
    candidates = ["local.properties", "google-services.json", "key.properties",
                  "GoogleService-Info.plist"]
    for name in candidates:
        for f in project_dir.rglob(name):
            report.flag(f"{f} may contain machine-specific or personal information; review/delete manually")


def detect_source_languages(project_dir: Path, module: str) -> set:
    src_main = project_dir / module / "src" / "main"
    languages = set()
    if any(src_main.rglob("*.java")):
        languages.add("java")
    if any(src_main.rglob("*.kt")):
        languages.add("kotlin")
    return languages


def run_pipeline(project_dir: Path, module: str, dry_run: bool, skip_cleanup: bool, report: Report):
    print(f"\n=== Templatizing {project_dir} ===\n")
    if dry_run:
        print("(dry run: no files will be modified)\n")

    print("-- gradle/wrapper/gradle-wrapper.properties --")
    process_gradle_wrapper(project_dir, report, dry_run)

    print("\n-- settings.gradle.kts / settings.gradle --")
    process_settings_gradle(project_dir, report, dry_run)

    print("\n-- build.gradle.kts / build.gradle (root) --")
    process_root_build_gradle(project_dir, report, dry_run)

    languages = detect_source_languages(project_dir, module)
    is_mixed_language = len(languages) > 1

    print(f"\n-- {module}/build.gradle.kts / build.gradle --")
    base_package = process_app_build_gradle(project_dir, module, report, dry_run, is_mixed_language)

    print(f"\n-- {module}/src/main/res/values/strings.xml --")
    process_strings_xml(project_dir, module, report, dry_run)

    print(f"\n-- {module}/src/main/**/*.java, *.kt --")
    process_java_kt_sources(project_dir, module, base_package, report, dry_run)

    print(f"\n-- {module}/src/main/java package directory --")
    flatten_package_directory(project_dir, module, base_package, report, dry_run)

    if not skip_cleanup:
        print("\n-- Removing build/ directories --")
        cleanup_build_dirs(project_dir, report, dry_run)

        print("\n-- Removing keystore files --")
        cleanup_keystores(project_dir, report, dry_run)

        print("\n-- Flagging files that may contain personal information --")
        flag_personal_info_files(project_dir, report)

    return languages


# Generic template thumbnail (phone mockup), used as a stand-in until a
# project-specific one is supplied.
THUMB_PNG_B64 = (
    "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"
    "AAAASUVORK5CYII="
)


def build_template_json(include_language: bool = True) -> dict:
    optional = {}
    if include_language:
        optional["language"] = {"identifier": "LANGUAGE"}
    optional["minsdk"] = {"identifier": "MIN_SDK"}

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
            "optional": optional,
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
        }
    }


def create_template_bundle(project_dir: Path, module: str, output_dir: Path,
                            template_name: str, skip_cleanup: bool, dry_run: bool,
                            report: Report):
    dest = output_dir / template_name

    if dry_run:
        print(f"\n=== Dry run: would create {output_dir} ===")
        print("\n-- gradlew clean --")
        run_gradlew_clean(project_dir, report, dry_run)
        if dest.exists():
            print(f"  would remove existing {dest}")
        print(f"  would copy {project_dir} -> {dest}")

        # Preview against a disposable copy in a temp dir rather than `dest`
        # itself, so a dry run can never touch anything that already exists
        # at the real output path.
        tmp_root = Path(tempfile.mkdtemp(prefix=f"{template_name}-dryrun-"))
        tmp_dest = tmp_root / template_name
        try:
            shutil.copytree(project_dir, tmp_dest, ignore=COPY_IGNORE)
            run_pipeline(tmp_dest, module, dry_run, skip_cleanup, report)
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

        print(f"\n  would write {output_dir / 'templates.json'}")
        print(f"  would write {dest / 'template' / 'template.json'}")
        print(f"  would write {dest / 'template' / 'thumb.png'}")
        return

    print("\n-- gradlew clean --")
    run_gradlew_clean(project_dir, report, dry_run)

    if dest.exists():
        report.remove(str(dest))
        shutil.rmtree(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(project_dir, dest, ignore=COPY_IGNORE)
    report.ok(f"{project_dir} -> {dest}")

    languages = run_pipeline(dest, module, dry_run, skip_cleanup, report)
    include_language = len(languages) > 1

    templates_json = output_dir / "templates.json"
    templates_json.write_text(
        json.dumps({"templates": [{"path": template_name}]}, indent=2) + "\n",
        encoding="utf-8",
    )
    report.ok(str(templates_json))

    template_dir = dest / "template"
    template_dir.mkdir(exist_ok=True)

    template_json = template_dir / "template.json"
    template_json.write_text(
        json.dumps(build_template_json(include_language), indent=4) + "\n", encoding="utf-8"
    )
    report.ok(str(template_json))

    thumb_png = template_dir / "thumb.png"
    thumb_png.write_bytes(base64.b64decode(THUMB_PNG_B64))
    report.ok(f"{thumb_png} (generic thumbnail, replace with an app-specific one if desired)")


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

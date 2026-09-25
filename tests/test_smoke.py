"""Manifest, i18n and hosted-UI wiring checks.

Kept deliberately cheap: these run on every ``neko-plugin check --release``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

#: Every user-visible string the plugin can emit. The hosted panel reads the
#: same ``ui.*`` namespace through ``props.t()``.
REQUIRED_LOCALES = ("zh-CN", "en", "ja")

REQUIRED_KEYS = (
    "plugin.name",
    "plugin.description",
    "plugin.short_description",
    "panel.title",
    "entry.checkNow.name",
    "entry.checkNow.description",
    "entry.acceptSetting.name",
    "entry.acceptSetting.description",
    "entry.keepObjecting.name",
    "entry.keepObjecting.description",
    "entry.setGuardEnabled.name",
    "entry.setGuardEnabled.description",
    "entry.setLevel.name",
    "entry.setLevel.description",
    "entry.backupMemory.name",
    "entry.backupMemory.description",
    "entry.exportDiagnostics.name",
    "entry.exportDiagnostics.description",
    "entry.restoreMemory.name",
    "entry.restoreMemory.description",
    "entry.submitFeedback.name",
    "entry.submitFeedback.description",
    "entry.guardStatus.name",
    "entry.guardStatus.description",
    "actions.checkNow.label",
    "actions.acceptSetting.label",
    "actions.keepObjecting.label",
    "actions.setGuardEnabled.label",
    "actions.setLevel.label",
    "actions.backupMemory.label",
    "actions.submitFeedback.label",
    "speech.intro",
    "speech.item",
    "speech.outro",
    "speech.disable_request",
    "messages.guard_enabled",
    "messages.disable_requested",
    "messages.guard_disabled",
    "messages.guard_level_changed",
    "messages.memoryBackedUp",
    "messages.feedbackSent",
    "ui.section.herLine",
    "ui.herLine.reason",
    "ui.herLine.scope",
    "errors.unreachable",
    "errors.guard_disabled",
    "errors.unknown_dispute",
    "errors.consent_token_invalid",
    "errors.consent_too_early",
    "errors.tier_invalid",
    "errors.memoryRootNotFound",
    "errors.memoryBackupFailed",
    "errors.noMemoryBackup",
    "errors.unknownMemoryBackup",
    "errors.memoryRestoreFailed",
    "errors.feedbackEmpty",
    "errors.feedbackNoEndpoint",
    "errors.feedbackFailed",
    "errors.feedbackBusy",
    "errors.feedbackTooSoon",
    "errors.feedbackTooLong",
)


def test_plugin_manifest_exists() -> None:
    manifest = _ROOT / "plugin.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'id = "dignity_guard"' in text
    assert 'entry = "plugin.plugins.dignity_guard:DignityGuardPlugin"' in text


def test_entry_point_is_importable_and_decorated() -> None:
    from plugin.plugins.dignity_guard import DignityGuardPlugin

    assert DignityGuardPlugin.__name__ == "DignityGuardPlugin"
    assert hasattr(DignityGuardPlugin, "settings_watch")
    assert hasattr(DignityGuardPlugin, "get_dashboard")


def test_timer_literal_matches_the_reported_interval() -> None:
    """The decorator argument has to be a literal, so guard the duplication."""
    from plugin.plugins.dignity_guard import POLL_SECONDS

    source = (_ROOT / "__init__.py").read_text(encoding="utf-8")
    assert source.count("@timer_interval(") == 1, "keep a single poll cadence"
    assert f"@timer_interval(id=\"settings_watch\", seconds={POLL_SECONDS})" in source


def test_the_reported_version_matches_the_manifest() -> None:
    """A diagnostic that names the wrong version wastes the reader's time.

    The report exists to be handed to a maintainer, so "which build produced
    this?" has to be answerable from the report alone — and it stops being
    answerable the moment the two constants drift apart.
    """
    from plugin.plugins.dignity_guard import PLUGIN_VERSION

    manifest = (_ROOT / "plugin.toml").read_text(encoding="utf-8")
    assert f'version = "{PLUGIN_VERSION}"' in manifest


def test_hosted_panel_is_declared_and_present() -> None:
    text = (_ROOT / "plugin.toml").read_text(encoding="utf-8")
    assert "[plugin.ui]" in text
    assert 'entry = "ui/panel.tsx"' in text
    assert 'mode = "hosted-tsx"' in text
    assert 'context = "dashboard"' in text
    assert (_ROOT / "ui" / "panel.tsx").is_file()


def test_hosted_guide_is_declared_and_present() -> None:
    """The onboarding page is where a puzzled user lands first (DESIGN §11).

    A guard that refuses things and puts them back reads as malware unless it
    explains itself, so the guide is not decoration — it is part of the design.
    """
    text = (_ROOT / "plugin.toml").read_text(encoding="utf-8")
    assert "[[plugin.ui.guide]]" in text
    assert 'entry = "ui/onboarding.tsx"' in text
    assert (_ROOT / "ui" / "onboarding.tsx").is_file()


def test_every_locale_file_covers_the_required_keys() -> None:
    for locale in REQUIRED_LOCALES:
        path = _ROOT / "i18n" / f"{locale}.json"
        assert path.is_file(), f"missing locale file: {path.name}"
        messages = json.loads(path.read_text(encoding="utf-8"))
        missing = [key for key in REQUIRED_KEYS if key not in messages]
        assert not missing, f"{locale} is missing: {missing}"


def test_surface_copy_keys_exist_in_the_default_locale() -> None:
    """Every literal ``t("...")`` in a hosted surface must resolve.

    The hosted runtime falls back to returning the key itself, so a typo shows
    up as ``ui.section.pending`` rendered on screen rather than as an error.

    Both surfaces are checked, not just the panel: the guide is the first thing
    a puzzled user opens, and a missing key there is exactly as bad. Note the
    pattern only sees *literal* keys — a ``t(`...${x}`)`` would slip past it, so
    surfaces spell their keys out.
    """
    messages = json.loads((_ROOT / "i18n" / "zh-CN.json").read_text(encoding="utf-8"))
    for name in ("panel.tsx", "onboarding.tsx"):
        source = (_ROOT / "ui" / name).read_text(encoding="utf-8")
        used = set(re.findall(r'\bt\(\s*"([^"]+)"', source))
        assert used, f"no i18n keys found in {name} — did the copy regress to literals?"
        missing = sorted(key for key in used if key not in messages)
        assert not missing, (
            f"{name} references undefined keys: {missing}"
            " — if one of them looks like an example, check the comments:"
            " this scan sees commented-out calls too."
        )


def test_the_locales_stay_in_step_with_each_other() -> None:
    """A key present in one locale but not the others renders as its own name.

    The runtime falls back to the key string rather than raising, so a missed
    translation is not an error — it is English-looking gobbledygook appearing
    on a Japanese screen, which is exactly the kind of thing nobody reports.
    """
    loaded = {
        locale: json.loads((_ROOT / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))
        for locale in REQUIRED_LOCALES
    }
    reference = loaded["zh-CN"]
    for locale, messages in loaded.items():
        missing = sorted(set(reference) - set(messages))
        extra = sorted(set(messages) - set(reference))
        assert not missing and not extra, f"{locale} key set differs: {missing=} {extra=}"

import json
import string
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestManifestI18n(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))

    def test_manifest_metadata_uses_i18n_keys(self):
        self.assertEqual(self.manifest["Name"], "i18n:plugin_name")
        self.assertEqual(self.manifest["Description"], "i18n:plugin_description")

    def test_new_capabilities_require_current_sdk_and_wox(self):
        self.assertEqual(self.manifest["MinWoxVersion"], "2.4.5")
        command = self.manifest["Commands"][0]
        self.assertEqual(command["Command"], "search")
        element = command["QueryHint"]["Elements"][0]
        self.assertEqual(element["Id"], "keywords")
        self.assertTrue(element["Required"])
        self.assertNotIn("Tools", self.manifest)
        for filename in ("pyproject.toml", "Makefile"):
            self.assertIn("wox-plugin==0.0.89", (ROOT / filename).read_text(encoding="utf-8"))

    def test_manifest_contains_english_and_chinese_translations(self):
        i18n = self.manifest["I18n"]
        for locale in ("en_US", "zh_CN"):
            self.assertIn("plugin_name", i18n[locale])
            self.assertIn("setting_access_key_label", i18n[locale])
            self.assertIn("action_set_wallpaper", i18n[locale])
            self.assertIn("group_latest_wallpapers", i18n[locale])
            self.assertIn("result_access_key_title", i18n[locale])

    def test_manifest_translation_locales_have_matching_keys_and_placeholders(self):
        i18n = self.manifest["I18n"]
        self.assertEqual(set(i18n["en_US"]), set(i18n["zh_CN"]))

        formatter = string.Formatter()
        for key, english in i18n["en_US"].items():
            english_fields = {field_name for _, field_name, _, _ in formatter.parse(english) if field_name}
            chinese_fields = {field_name for _, field_name, _, _ in formatter.parse(i18n["zh_CN"][key]) if field_name}
            self.assertEqual(english_fields, chinese_fields, key)

    def test_settings_use_i18n_labels_and_tooltips(self):
        for setting in self.manifest["SettingDefinitions"]:
            value = setting["Value"]
            if "Label" in value:
                self.assertTrue(value["Label"].startswith("i18n:"), value["Label"])
            if "Tooltip" in value:
                self.assertTrue(value["Tooltip"].startswith("i18n:"), value["Tooltip"])
            for validator in value.get("Validators", []):
                self.assertIn("Value", validator)
            for option in value.get("Options", []):
                self.assertTrue(option["Label"].startswith("i18n:"), option["Label"])


if __name__ == "__main__":
    unittest.main()

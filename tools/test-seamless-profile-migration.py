#!/usr/bin/env python3
"""Run the production per-profile migration with recording settings/save stubs."""
from pathlib import Path
import runpy
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
function = runpy.run_path(str(ROOT / "tools/test-render-slot-publication.py"))["function"]
source = (ROOT / "xbmc/settings/Settings.cpp").read_text()
method = function(source, "bool CSettings::MigrateSeamlessTransitions(const std::string& file, bool& updated)")
load = function(source, "bool CSettings::Load(const std::string &file)")
assert "MigrateSeamlessTransitions(file, updated)" in load
assert "return false;" in load[load.index("if (!MigrateSeamlessTransitions"):]
assert "CSysfsPath" not in method and "aml_apply_" not in method
manager = (ROOT / "xbmc/settings/lib/SettingsManager.cpp").read_text()
changing = function(manager, "bool CSettingsManager::OnSettingChanging(const std::shared_ptr<const CSetting>& setting)")
assert "if (!m_loaded)" in changing and "return true;" in changing
xml = ET.parse(ROOT / "system/settings/settings.xml")
toggle = xml.find(".//setting[@id='coreelec.amlogic.hdr10.seamless.transitions']")
marker = xml.find(".//setting[@id='coreelec.amlogic.hdr10.seamless.migration.version']")
assert toggle.findtext("default") == "true" and toggle.findtext("level") == "2"
assert marker.findtext("default") == "0" and marker.findtext("visible") == "false"
assert marker.findtext("level") == "4" and marker.get("type") == "integer"

HARNESS = r'''
#include <cassert>
#include <iostream>
#include <mutex>
#include <string>
#define HAS_LIBAMCODEC
using CCriticalSection = std::recursive_mutex;
enum { LOGERROR, LOGINFO };
struct CLog {
  static int errors;
  template<typename... T> static void Log(int level, const char*, T&&...) {
    if (level == LOGERROR) ++errors;
  }
};
int CLog::errors = 0;
struct Manager {
  bool available = true;
  const void* GetSetting(const char*) { return available ? this : nullptr; }
};
struct CSettings {
  static constexpr const char* SETTING_COREELEC_AMLOGIC_HDR10_SEAMLESS_TRANSITIONS = "toggle";
  static constexpr const char* SETTING_COREELEC_AMLOGIC_HDR10_SEAMLESS_MIGRATION_VERSION = "version";
  CCriticalSection m_critical;
  Manager manager;
  bool enabled = true, savedEnabled = true, saveSucceeds = true, reenter = false;
  bool rejectEnable = false;
  int version = 0, savedVersion = 0, saves = 0, callbacks = 0;
  Manager* GetSettingsManager() { return &manager; }
  int GetInt(const char*) { return version; }
  bool GetBool(const char*) { return enabled; }
  bool SetInt(const char*, int value) { version = value; return true; }
  bool SetBool(const char*, bool value) {
    if (rejectEnable && value) return false;
    enabled = value;
    if (reenter) {
      reenter = false;
      ++callbacks;
      bool updated = false;
      assert(MigrateSeamlessTransitions("profile", updated));
      assert(saves == 0);
    }
    return true;
  }
  bool Save(const std::string&) {
    ++saves;
    if (!saveSucceeds) return false;
    savedEnabled = enabled; savedVersion = version;
    return true;
  }
  bool MigrateSeamlessTransitions(const std::string& file, bool& updated);
};
@METHOD@
int main() {
  // A new profile and both old saved values migrate once, including explicit Off.
  for (bool oldValue : {false, true}) {
    CSettings settings;
    settings.enabled = settings.savedEnabled = oldValue;
    settings.reenter = true;
    bool updated = true;
    assert(settings.MigrateSeamlessTransitions("profile", updated));
    assert(settings.enabled && settings.version == 1);
    assert(settings.savedEnabled && settings.savedVersion == 1);
    assert(settings.saves == 1 && settings.callbacks == 1 && !updated);
    assert(settings.MigrateSeamlessTransitions("profile", updated));
    assert(settings.saves == 1);
    // User Off is persisted normally and is never overridden on restart/reload.
    assert(settings.SetBool("toggle", false));
    assert(settings.Save("profile"));
    CSettings restarted;
    restarted.enabled = settings.savedEnabled;
    restarted.version = settings.savedVersion;
    assert(restarted.MigrateSeamlessTransitions("profile", updated));
    assert(!restarted.enabled && restarted.version == 1 && restarted.saves == 0);
  }
  CSettings fresh;
  bool updated = false;
  assert(fresh.enabled && fresh.MigrateSeamlessTransitions("fresh", updated));
  assert(fresh.savedEnabled && fresh.savedVersion == 1 && fresh.saves == 1);
  for (bool oldValue : {false, true}) {
    CSettings failed;
    failed.enabled = failed.savedEnabled = oldValue;
    failed.saveSucceeds = false;
    int errors = CLog::errors;
    updated = true;
    assert(!failed.MigrateSeamlessTransitions("profile", updated));
    assert(failed.enabled == oldValue && failed.version == 0);
    assert(failed.savedEnabled == oldValue && failed.savedVersion == 0);
    assert(updated && CLog::errors > errors);
    failed.saveSucceeds = true;
    assert(failed.MigrateSeamlessTransitions("profile", updated));
    assert(failed.savedEnabled && failed.savedVersion == 1);
  }
  CSettings missing;
  missing.manager.available = false;
  int errors = CLog::errors;
  assert(!missing.MigrateSeamlessTransitions("profile", updated));
  assert(missing.saves == 0 && missing.version == 0 && CLog::errors > errors);
  CSettings veto;
  veto.enabled = veto.savedEnabled = false;
  veto.rejectEnable = true;
  assert(!veto.MigrateSeamlessTransitions("profile", updated));
  assert(!veto.enabled && veto.version == 0 && veto.saves == 0);
  CSettings newer;
  newer.version = 2; newer.enabled = false;
  assert(newer.MigrateSeamlessTransitions("profile", updated));
  assert(!newer.enabled && newer.version == 2 && newer.saves == 0);
  std::cout << "PASS: fresh/old false/old true once, reentrant callbacks, persisted Off, "
               "future marker, save failure rollback/retry and missing definitions\n";
}
'''


def run(code, negative=False):
    with tempfile.TemporaryDirectory(prefix="seamless-migration-") as directory:
        path = Path(directory)
        (path / "test.cpp").write_text(code)
        subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
                        str(path / "test.cpp"), "-o", str(path / "test")], check=True)
        result = subprocess.run([str(path / "test")], capture_output=True, text=True)
        if negative:
            assert result.returncode != 0 and "Assertion" in result.stderr, result.stderr
        else:
            assert result.returncode == 0, result.stderr
            print(result.stdout.strip())


run(HARNESS.replace("@METHOD@", method))
for label, old, new in [
    ("force every restart", "if (version >= 1)", "if (false)"),
    ("old Off preserved instead of migrated", "TRANSITIONS, true)", "TRANSITIONS, enabled)"),
    ("save failure hidden", " || !Save(file)", " || (Save(file), false)"),
    ("marker not persisted", "MIGRATION_VERSION, 1)", "MIGRATION_VERSION, 0)"),
    ("failed save leaves marker complete", "MIGRATION_VERSION, version)", "MIGRATION_VERSION, 1)"),
]:
    assert old in method, label
    run(HARNESS.replace("@METHOD@", method.replace(old, new)), negative=True)
    print("REJECTED:", label)

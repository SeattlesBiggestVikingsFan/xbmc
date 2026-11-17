#!/usr/bin/env python3
"""Check the seamless SDR/HDR10 setting, its placement and its kernel sysfs write.

A recording sysfs substitute proves Kodi policy only, not device HDMI output.
"""
import argparse
import ast
from pathlib import Path
import re
import runpy
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
function = runpy.run_path(str(ROOT / 'tools/test-render-slot-publication.py'))['function']
SETTING = 'coreelec.amlogic.hdr10.seamless.transitions'
PREVIOUS = 'coreelec.amlogic.dolbyvision.reassert.after.modeswitch'
NODE = '/sys/class/amhdmitx/amhdmitx0/native_hdr_mute_bypass'

KODI = r'''
#include <cassert>
#include <iostream>
#include <map>
#include <string>
#include <vector>
struct CSettings {static constexpr const char* SETTING_COREELEC_AMLOGIC_HDR10_SEAMLESS_TRANSITIONS="s";};
struct Settings {std::map<std::string,bool> values;bool GetBool(const std::string& s){return values.at(s);}} storage;
Settings* settings(){return &storage;}
std::map<std::string,bool> nodes;
std::vector<std::pair<std::string,int>> writes;
struct CSysfsPath {
 std::string path;CSysfsPath(const std::string& s):path(s){}
 bool Exists(){return nodes[path];}
 void Set(int v){assert(Exists());writes.emplace_back(path,v);}
};
@METHODS@
int main(){
 const std::string node="@NODE@";
 for(bool present:{false,true})for(bool enabled:{false,true})for(int repeat=0;repeat<2;repeat++){
  nodes.clear();nodes[node]=present;storage.values={{"s",enabled}};writes.clear();
  assert(aml_hdr10_seamless_transitions_supported()==present);
  aml_apply_hdr10_seamless_transitions();
  const std::vector<std::pair<std::string,int>> expected=present?
    std::vector<std::pair<std::string,int>>{{node,enabled?1:0}}:std::vector<std::pair<std::string,int>>{};
  assert(writes==expected);
 }
 std::cout<<"PASS: missing kernel knob writes nothing; on/off write exactly 1/0 (ASan/UBSan)\n";
}
'''


def run(code, negative=False):
    with tempfile.TemporaryDirectory(prefix='hdr10-seamless-') as directory:
        path = Path(directory)
        (path / 'test.cpp').write_text(code)
        subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        str(path / 'test.cpp'), '-o', str(path / 'test')], check=True)
        result = subprocess.run([str(path / 'test')], text=True, capture_output=True, timeout=20)
        if negative:
            assert result.returncode and 'Assertion' in result.stderr, result.stdout + result.stderr
        else:
            assert result.returncode == 0, result.stdout + result.stderr
            print(result.stdout.strip())


def kodi_code():
    source = (ROOT / 'xbmc/utils/AMLUtils.cpp').read_text()
    constant = re.search(r'static const char\* const AML_NATIVE_HDR_MUTE_BYPASS =\s*"[^"]+";', source)
    assert constant, 'sysfs path constant'
    methods = '\n'.join([constant.group(0)] + [function(source, sig) for sig in (
        'bool aml_hdr10_seamless_transitions_supported()',
        'void aml_apply_hdr10_seamless_transitions()')])
    return KODI.replace('@METHODS@', methods).replace('@NODE@', NODE)


def po_entries(path):
    entries, fields, key = {}, {}, None
    for line in path.read_text().splitlines() + ['']:
        if not line:
            if fields.get('msgctxt', '').startswith('#'):
                assert fields['msgctxt'] not in entries, fields['msgctxt']
                entries[fields['msgctxt']] = fields
            fields, key = {}, None
        elif line.startswith(('msgctxt ', 'msgid ', 'msgstr ')):
            key, value = line.split(' ', 1)
            fields[key] = ast.literal_eval(value)
        elif line.startswith('"') and key:
            fields[key] += ast.literal_eval(line)
    return entries


def settings_check(xml_text):
    tree = ET.ElementTree(ET.fromstring(xml_text))
    ids = [s.get('id') for s in tree.iter('setting')]
    assert len(ids) == len(set(ids)), 'duplicate setting ids'
    group = next(g for g in tree.iter('group')
                 if any(s.get('id') == PREVIOUS for s in g.findall('setting')))
    siblings = [s.get('id') for s in group.findall('setting')]
    assert siblings[siblings.index(PREVIOUS) + 1] == SETTING, 'must directly follow re-assert setting'
    node = group.find(f'setting[@id="{SETTING}"]')
    assert node.get('type') == 'boolean' and node.get('parent') is None
    assert node.findtext('requirement') == 'HAVE_AMCODEC' and node.findtext('visible') == 'false'
    assert node.findtext('level') == '2' and node.findtext('default') == 'false'
    assert node.find('control').get('type') == 'toggle' and node.find('dependencies') is None
    en = po_entries(ROOT / 'addons/resource.language.en_gb/resources/strings.po')
    for field in ('label', 'help'):
        assert en['#' + node.get(field)]['msgid'], field
    print('PASS: setting directly follows HDR re-assert, defaults off, hidden until kernel support, EN strings')


def wiring_check(source):
    header = (ROOT / 'xbmc/settings/Settings.h').read_text()
    assert re.search(r'SETTING_COREELEC_AMLOGIC_HDR10_SEAMLESS_TRANSITIONS =\s*"' + re.escape(SETTING) + '"', header)
    name = 'CSettings::SETTING_COREELEC_AMLOGIC_HDR10_SEAMLESS_TRANSITIONS'
    setup = source[:source.index('settingsManager->RegisterCallback(this, settingSet);')]
    assert f'settingSet.insert({name});' in setup, 'callback registration'
    assert re.search(r'set_visible\(' + re.escape(name) + r',\s*aml_hdr10_seamless_transitions_supported\(\)\);\s*'
                     r'aml_apply_hdr10_seamless_transitions\(\);', source), 'startup visibility/apply'
    apply = function(source, 'void CDolbyVisionAML::apply_native_setting(const std::string& settingId)\n{')
    assert re.search(r'settingId == ' + re.escape(name) + r'\)\s*\{\s*aml_apply_hdr10_seamless_transitions\(\);', apply)
    changed = source[source.index('// Queue independent native switches'):]
    changed = changed[:changed.index('schedule_native_setting_apply(setting->GetId());')]
    assert f'setting->GetId() == {name}' in changed, 'change routing'
    dv_visible = function(source, 'static void set_dv_settings_visible(bool show)\n{')
    assert name not in dv_visible, 'must not be hidden with Dolby Vision controls'
    print('PASS: startup apply, change callback routing and DV-independent visibility')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--negative-controls', action='store_true')
    args = parser.parse_args()
    code = kodi_code()
    xml_text = (ROOT / 'system/settings/settings.xml').read_text()
    dv = (ROOT / 'xbmc/windowing/amlogic/DolbyVisionAML.cpp').read_text()
    run(code)
    settings_check(xml_text)
    wiring_check(dv)
    if not args.negative_controls:
        return
    for label, old, new in [
        ('ignore missing knob', 'if (!aml_hdr10_seamless_transitions_supported())', 'if (false)'),
        ('invert setting', 'enabled ? 1 : 0', 'enabled ? 0 : 1'),
        ('always enable', 'enabled ? 1 : 0', 'enabled ? 1 : 1'),
    ]:
        assert old in code, label
        run(code.replace(old, new), negative=True)
        print('REJECTED:', label)
    block = re.search(r'\s*<setting id="' + re.escape(SETTING) + r'".*?</setting>', xml_text, re.S).group(0)
    anchor = f'<setting id="{PREVIOUS}"'
    moved = xml_text.replace(block, '', 1).replace(anchor, block.strip() + '\n        ' + anchor, 1)
    for label, mutant in [
        ('moved before re-assert setting', moved),
        ('default on', xml_text.replace(block, block.replace('<default>false</default>', '<default>true</default>'), 1)),
        ('parented to DV type', xml_text.replace(block, block.replace('type="boolean"', 'type="boolean" parent="coreelec.amlogic.dolbyvision.type"'), 1)),
    ]:
        assert mutant != xml_text, label
        try:
            settings_check(mutant)
        except (AssertionError, StopIteration):
            print('REJECTED:', label)
        else:
            raise AssertionError('mutant passed: ' + label)
    name = 'CSettings::SETTING_COREELEC_AMLOGIC_HDR10_SEAMLESS_TRANSITIONS'
    for label, old, new in [
        ('no startup apply', '  aml_apply_hdr10_seamless_transitions();\n\n', '\n'),
        ('no callback', f'  settingSet.insert({name});\n', ''),
        ('no change routing', f' ||\n      setting->GetId() == {name})', ')'),
    ]:
        assert old in dv, label
        try:
            wiring_check(dv.replace(old, new, 1))
        except AssertionError:
            print('REJECTED:', label)
        else:
            raise AssertionError('mutant passed: ' + label)


if __name__ == '__main__':
    main()

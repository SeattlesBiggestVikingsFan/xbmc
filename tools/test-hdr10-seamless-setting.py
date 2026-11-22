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
SDR = '/sys/module/am_vecm/parameters/sdr_bt2020_out'
ARM = '/sys/class/amhdmitx/amhdmitx0/hdr_prearm'
GATE = '/sys/class/amhdmitx/amhdmitx0/seamless_mode_refresh'

KODI = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <map>
#include <mutex>
#include <string>
#include <vector>
// Records whether the pre-arm lock is held, so every pre-arm write can prove it.
struct CheckedMutex {bool held=false;void lock(){assert(!held);held=true;}void unlock(){assert(held);held=false;}};
struct CSettings {static constexpr const char* SETTING_COREELEC_AMLOGIC_HDR10_SEAMLESS_TRANSITIONS="s";
 static constexpr const char* SETTING_COREELEC_AMLOGIC_DV_VS10_HDR10="vs10.hdr10";
 static constexpr const char* SETTING_COREELEC_AMLOGIC_DV_VS10_HDR10PLUS="vs10.hdr10plus";
 static constexpr const char* SETTING_COREELEC_AMLOGIC_DV_HDR10PLUS_CONVERT="convert";};
struct Settings {std::map<std::string,bool> values;bool GetBool(const std::string& s){return values.at(s);}} storage;
Settings* settings(){return &storage;}
enum class StreamHdrType {HDR_TYPE_NONE,HDR_TYPE_HDR10,HDR_TYPE_HDR10PLUS,HDR_TYPE_DOLBYVISION,HDR_TYPE_HLG};
#define DOLBY_VISION_OUTPUT_MODE_BYPASS (unsigned int)(5)
bool dvOn=false, dvPipeline=false;
std::map<std::string,unsigned> vs10;
bool aml_is_dv_enable(){return dvOn;}
bool aml_dolby_vision_enabled(){return dvPipeline;}
unsigned int aml_vs10_by_setting(const std::string s){return vs10.at(s);}
enum {LOGINFO};
struct CLog {template<typename... A> static void Log(int, const char*, A&&...){}};
std::map<std::string,bool> nodes;
bool reject = false;
std::vector<std::pair<std::string,int>> writes;
extern CheckedMutex s_hdrPrearmMutex;
struct CSysfsPath {
 std::string path;CSysfsPath(const std::string& s):path(s){}
 bool Exists(){return nodes[path];}
 void Set(int v){assert(Exists());writes.emplace_back(path,v);}
};
struct FakeOut {
 std::string path;bool ok;
 explicit FakeOut(const std::string& p):path(p),ok(nodes[p]){}
 FakeOut& operator<<(unsigned v){assert(ok);assert(path!="@ARM@"||s_hdrPrearmMutex.held);writes.emplace_back(path,(int)v);if(reject&&v)ok=false;return *this;}
 void close(){}
 explicit operator bool() const {return ok;}
};
void aml_hdr10_prearm_cancel();
@METHODS@
static void route_cases(){
 using H=StreamHdrType;
 for(bool on:{false,true})for(bool pipe:{false,true})for(unsigned m10:{0u,2u,5u})for(unsigned mp:{0u,5u})for(bool conv:{false,true})
 for(H h:{H::HDR_TYPE_HDR10,H::HDR_TYPE_DOLBYVISION}){
  dvOn=on;dvPipeline=pipe;vs10={{"vs10.hdr10",m10},{"vs10.hdr10plus",mp}};storage.values={{"convert",conv}};
  const bool plain=!on&&(!pipe||(h==H::HDR_TYPE_HDR10&&m10==5&&mp==5&&!conv));
  assert(aml_hdr10_prearm_plain_route(h)==plain);
 }
 std::cout<<"PASS: pre-arm only when no DV output, VS10 or HDR10+ to DV route can take the stream\n";
}
int main(){
 route_cases();
 const std::string node="@NODE@", sdr="@SDR@", arm="@ARM@", gate="@GATE@";
 for(bool present:{false,true})for(bool extra:{false,true})for(bool gatePresent:{false,true})
 for(bool enabled:{false,true})for(int repeat=0;repeat<2;repeat++){
  nodes.clear();nodes[node]=present;nodes[sdr]=present&&extra;nodes[arm]=present&&extra;
  nodes[gate]=gatePresent;
  storage.values={{"s",enabled}};writes.clear();
  assert(aml_hdr10_seamless_transitions_supported()==present);
  aml_apply_hdr10_seamless_transitions();
  std::vector<std::pair<std::string,int>> expected;
  if(present&&enabled){expected.push_back({node,1});if(extra)expected.push_back({sdr,1});if(gatePresent)expected.push_back({gate,1});}
  if(present&&!enabled){if(gatePresent)expected.push_back({gate,0});if(extra){expected.push_back({arm,0});expected.push_back({sdr,0});}expected.push_back({node,0});}
  assert(writes==expected);
 }
 std::cout<<"PASS: knobs on (bypass, BT.2020 SDR, gate) / off (gate, cancel, BT.2020 SDR, bypass); missing knobs skipped\n";
 for(bool present:{false,true})for(bool enabled:{false,true})for(bool rej:{false,true}){
  nodes.clear();nodes[arm]=present;storage.values={{"s",enabled}};writes.clear();reject=rej;
  const bool eligible=present&&enabled;
  const uint64_t token=aml_hdr10_prearm();
  assert((token!=0)==(eligible&&!rej));
  assert(writes==(eligible?std::vector<std::pair<std::string,int>>{{arm,1}}:std::vector<std::pair<std::string,int>>{}));
  writes.clear();aml_hdr10_prearm_cancel(token);
  assert(writes==(token&&present?std::vector<std::pair<std::string,int>>{{arm,0}}:std::vector<std::pair<std::string,int>>{}));
  writes.clear();aml_hdr10_prearm_cancel();
  assert(writes==(present?std::vector<std::pair<std::string,int>>{{arm,0}}:std::vector<std::pair<std::string,int>>{}));
 }
 reject=false;
 std::cout<<"PASS: pre-arm only with setting+knob, reports kernel rejection, cancel tolerates missing knob (ASan/UBSan)\n";
 // Stream change: B opens before A closes; A's close must not cancel B's (or a taken-over) pre-arm.
 for(bool rejB:{false,true}){
  nodes.clear();nodes[arm]=true;storage.values={{"s",true}};reject=false;
  const uint64_t a=aml_hdr10_prearm();assert(a);
  reject=rejB;const uint64_t b=aml_hdr10_prearm();assert(b&&b!=a);reject=false;
  writes.clear();aml_hdr10_prearm_cancel(a);assert(writes.empty());
  aml_hdr10_prearm_cancel(b);assert((writes==std::vector<std::pair<std::string,int>>{{arm,0}}));
  writes.clear();aml_hdr10_prearm_cancel(b);assert(writes.empty());
 }
 // A non-HDR10 successor never arms, so the old owner still cancels its pending pre-arm.
 {const uint64_t a=aml_hdr10_prearm();storage.values={{"s",false}};assert(!aml_hdr10_prearm());
  writes.clear();aml_hdr10_prearm_cancel(a);assert((writes==std::vector<std::pair<std::string,int>>{{arm,0}}));}
 std::cout<<"PASS: only the latest HDR10 opener cancels; a rejected successor takes over a pending pre-arm\n";
}
'''

GUI_RESTORE = r'''
#include <cassert>
#include <iostream>
#include <mutex>
enum {LOGDEBUG,LOGINFO};
struct CLog {template<typename... A> static void Log(int, const char*, A&&...){}};
struct CDVCoreGuard {explicit CDVCoreGuard(const char*){}};
enum {DV_MODE_ON,DV_MODE_ON_DEMAND,DV_MODE_OFF};
#define DOLBY_VISION_OUTPUT_MODE_IPT (unsigned int)(0)
#define DOLBY_VISION_OUTPUT_MODE_IPT_TUNNEL (unsigned int)(1)
static std::mutex s_dvStartMutex;
int kodiMode=DV_MODE_ON;bool playing=false,dvOn=false;unsigned coreMode=5;int starts=0;
static int s_dvCodecsPending = 0;
static bool s_dvGuiSdrHold = false;
int aml_dv_mode(){return kodiMode;}
bool aml_dv_playback_active(){return playing;}
bool aml_is_dv_enable(){return dvOn;}
unsigned int aml_dv_dolby_vision_mode(){return coreMode;}
const char* aml_dv_output_mode_to_string(unsigned){return "";}
void _dv_start_locked(){s_dvGuiSdrHold=false;starts++;}
@METHOD@
@PENDING@
int main(){
 for(int m:{DV_MODE_ON,DV_MODE_ON_DEMAND,DV_MODE_OFF})for(bool p:{false,true})for(bool e:{false,true})for(unsigned c:{0u,1u,2u,5u})for(int q:{0,1})for(bool hold:{false,true}){
  kodiMode=m;playing=p;dvOn=e;coreMode=c;starts=0;s_dvCodecsPending=q;s_dvGuiSdrHold=hold;
  const bool restore=m==DV_MODE_ON&&!p&&!q&&!(hold&&!e);
  const bool switched=aml_dv_restore_gui_ipt("t");
  assert(starts==(restore?1:0));
  assert(switched==(restore&&(!e||c>1u)));
  assert(s_dvGuiSdrHold==(restore?false:hold));
 }
 std::cout<<"PASS: always-on sticky SDR suppresses restore only while DV disabled; non-sticky restore, playback/pending and Off/OnDemand guards retained\n";
 // Stream change: B opens before A's decoder starts; one release must not unblock restores.
 kodiMode=DV_MODE_ON;playing=false;dvOn=false;s_dvCodecsPending=0;s_dvGuiSdrHold=false;
 aml_dv_codec_pending(true);aml_dv_codec_pending(true);aml_dv_codec_pending(false);
 starts=0;aml_dv_restore_gui_ipt("t");assert(starts==0);
 aml_dv_codec_pending(false);aml_dv_restore_gui_ipt("t");assert(starts==1);
 aml_dv_codec_pending(false);assert(s_dvCodecsPending==0);
 std::cout<<"PASS: GUI restore waits for every opened codec's decoder start; release never underflows\n";
}
'''


def gui_restore_code():
    source = (ROOT / 'xbmc/utils/AMLUtils.cpp').read_text()
    close = function(source, 'void aml_dv_close()')
    hold = 's_dvGuiSdrHold = !aml_is_dv_enable();'
    assert close.count(hold) == 1
    assert re.search(r'if \(aml_dv_mode\(\) == DV_MODE_ON\)\s*\{\s*' + re.escape(hold), close), \
        'sticky close only in always-on DV mode'
    start = function(source, 'static void _dv_start_locked()')
    assert start.index('s_dvGuiSdrHold = false;') < start.index('if (aml_is_dv_enable())'), \
        'explicit DV start clears hold before choosing a route'
    return GUI_RESTORE.replace('@METHOD@', function(source, 'bool aml_dv_restore_gui_ipt(const char* reason)')).replace(
        '@PENDING@', function(source, 'void aml_dv_codec_pending(bool pending)'))


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
    constants = []
    for name in ('AML_NATIVE_HDR_MUTE_BYPASS', 'AML_SDR_BT2020_OUT', 'AML_HDR_PREARM',
                 'AML_SEAMLESS_MODE_REFRESH'):
        constant = re.search(r'static const char\* const ' + name + r' =\s*"[^"]+";', source)
        assert constant, 'sysfs path constant ' + name
        constants.append(constant.group(0))
    methods = '\n'.join(constants + [function(source, sig) for sig in (
        'bool aml_hdr10_seamless_transitions_supported()',
        'void aml_apply_hdr10_seamless_transitions()',
        'bool aml_hdr10_prearm_plain_route(StreamHdrType hdrType)',
        'uint64_t aml_hdr10_prearm()',
        'void aml_hdr10_prearm_cancel(uint64_t token)',
        'void aml_hdr10_prearm_cancel()')]).replace('std::ofstream', 'FakeOut')
    owner = re.search(r'static std::atomic<uint64_t> s_hdrPrearmOwner\{0\};\nstatic std::atomic<uint64_t> s_hdrPrearmTokens\{0\};', source)
    assert owner, 'pre-arm ownership state'
    lock = re.search(r'static std::mutex s_hdrPrearmMutex;', source)
    assert lock, 'pre-arm write/ownership lock'
    locked = function(source, 'static void aml_hdr10_prearm_cancel_locked()').replace('std::ofstream', 'FakeOut')
    methods = methods.replace('std::lock_guard<std::mutex>', 'std::lock_guard<CheckedMutex>')
    split = methods.index(constants[-1]) + len(constants[-1])
    methods = (owner.group(0) + '\nCheckedMutex s_hdrPrearmMutex;\n' + methods[:split] + '\n' +
               locked + methods[split:])
    return KODI.replace('@METHODS@', methods).replace('@NODE@', NODE).replace(
        '@SDR@', SDR).replace('@ARM@', ARM).replace('@GATE@', GATE)


def codec_check(source):
    open_ = function(source, 'bool CDVDVideoCodecAmlogic::Open(CDVDStreamInfo &hints, CDVDCodecOptions &options)\n{')
    tail = open_[open_.rindex('m_has_keyframe = false;'):open_.index('return true;\nFAIL:')]
    hook = re.search(r'if \((.*?)\)\s*m_hdrPrearm = aml_hdr10_prearm\(\);', tail, re.S)
    assert hook, 'pre-arm only after a successful open'
    cond = ' '.join(hook.group(1).split())
    assert 'm_hints.colorTransferCharacteristic == AVCOL_TRC_SMPTE2084 &&' in cond, 'PQ only'
    assert 'm_hints.colorPrimaries == AVCOL_PRI_BT2020 &&' in cond, 'BT.2020 only'
    assert ('(m_hints.hdrType == StreamHdrType::HDR_TYPE_HDR10 || m_hints.hdrType == '
            'StreamHdrType::HDR_TYPE_DOLBYVISION) && aml_hdr10_prearm_plain_route(m_hints.hdrType)') in cond, \
        'HDR10 / DV base layer on a plain HDR10 route only'
    assert 'HDR10PLUS' not in cond and 'HLG' not in cond
    close = function(source, 'void CDVDVideoCodecAmlogic::Close(void)\n{')
    assert re.search(r'if \(m_hdrPrearm\)\s*\{\s*aml_hdr10_prearm_cancel\(m_hdrPrearm\);\s*m_hdrPrearm = 0;\s*\}', close), 'close cancels its own pre-arm'
    assert close.index('aml_hdr10_prearm_cancel(m_hdrPrearm);') < close.index('m_Codec->CloseDecoder('), 'cancel before teardown'
    release = re.compile(r'if \(m_dvCodecPending\)\s*\{\s*aml_dv_codec_pending\(false\);\s*m_dvCodecPending = false;\s*\}')
    assert re.search(r'if \(!m_dvCodecPending\)\s*\{\s*aml_dv_codec_pending\(true\);\s*m_dvCodecPending = true;\s*\}', tail), \
        'successful open marks a codec pending'
    assert release.search(close) and release.search(close).start() < close.index('m_Codec->CloseDecoder('), \
        'close releases pending before teardown'
    add = function(source, 'bool CDVDVideoCodecAmlogic::AddData(const DemuxPacket &packet)\n{')
    started = add[add.index('m_Codec->OpenDecoder()'):]
    started = started[started.index('m_opened = true;'):]
    assert release.match(started[len('m_opened = true;'):].lstrip()), 'decoder start releases pending'
    print('PASS: codec arms only PQ/BT.2020 HDR10 (or DV base layer) on a plain HDR10 route after open and cancels its own on close; '
          'GUI restore pending from open to decoder start')


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
    assert node.findtext('level') == '2' and node.findtext('default') == 'true'
    assert node.find('control').get('type') == 'toggle' and node.find('dependencies') is None
    en = po_entries(ROOT / 'addons/resource.language.en_gb/resources/strings.po')
    for field in ('label', 'help'):
        assert en['#' + node.get(field)]['msgid'], field
    print('PASS: setting directly follows HDR re-assert, defaults on, hidden until kernel support, EN strings')


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
    codec = (ROOT / 'xbmc/cores/VideoPlayer/DVDCodecs/Video/DVDVideoCodecAmlogic.cpp').read_text()
    run(code)
    restore = gui_restore_code()
    run(restore)
    settings_check(xml_text)
    wiring_check(dv)
    codec_check(codec)
    if not args.negative_controls:
        return
    for label, old, new in [
        ('ignore missing knob', 'if (!aml_hdr10_seamless_transitions_supported())', 'if (false)'),
        ('invert setting', 'Set(1);\n    if', 'Set(0);\n    if'),
        ('BT.2020 SDR without bypass', 'CSysfsPath(AML_NATIVE_HDR_MUTE_BYPASS).Set(1);\n    if (sdrBt2020.Exists())\n      sdrBt2020.Set(1);',
         'if (sdrBt2020.Exists())\n      sdrBt2020.Set(1);\n    CSysfsPath(AML_NATIVE_HDR_MUTE_BYPASS).Set(1);'),
        ('disable leaves pre-arm', '    aml_hdr10_prearm_cancel();\n    if (sdrBt2020', '    if (sdrBt2020'),
        ('pre-arm ignores setting', '!settings()->GetBool', '!!settings()->GetBool'),
        ('pre-arm hides rejection', 'const bool armed = static_cast<bool>(file);', 'const bool armed = true;'),
        ('route ignores DV output', '  if (aml_is_dv_enable())\n    return false;\n', ''),
        ('route ignores VS10', '>=\n             DOLBY_VISION_OUTPUT_MODE_BYPASS &&\n         aml_vs10_by_setting', '!= 99u &&\n         aml_vs10_by_setting'),
        ('route ignores HDR10+ convert', '         !settings()->GetBool(CSettings::SETTING_COREELEC_AMLOGIC_DV_HDR10PLUS_CONVERT);', '         true;'),
        ('route admits DV stream', 'return hdrType == StreamHdrType::HDR_TYPE_HDR10 &&', 'return hdrType != StreamHdrType::HDR_TYPE_NONE &&'),
        ('old close cancels', 'if (s_hdrPrearmOwner.compare_exchange_strong(token, 0))', 'if (token)'),
        ('no takeover', '  if (!armed && !held)', '  if (!armed)'),
        ('arm outside lock', '  std::lock_guard<CheckedMutex> lock(s_hdrPrearmMutex);\n  FakeOut file(AML_HDR_PREARM);', '  FakeOut file(AML_HDR_PREARM);'),
        ('owner cancel outside lock', '    return;\n  std::lock_guard<CheckedMutex> lock(s_hdrPrearmMutex);\n', '    return;\n'),
        ('setting cancel outside lock', '{\n  std::lock_guard<CheckedMutex> lock(s_hdrPrearmMutex);\n  aml_hdr10_prearm_cancel_locked();', '{\n  aml_hdr10_prearm_cancel_locked();'),
        ('gate ignores missing node', 'if (sameModeGate.Exists())', 'if (true)'),
        ('enabled leaves gate off', 'sameModeGate.Set(1);', 'sameModeGate.Set(0);'),
        ('disabled leaves gate on', 'sameModeGate.Set(0);', 'sameModeGate.Set(1);'),
        ('gate enabled before bypass', 'CSysfsPath(AML_NATIVE_HDR_MUTE_BYPASS).Set(1);',
         'if (sameModeGate.Exists()) sameModeGate.Set(1);\n    CSysfsPath(AML_NATIVE_HDR_MUTE_BYPASS).Set(1);'),
        ('gate disabled after cancel', '    if (sameModeGate.Exists())\n      sameModeGate.Set(0);\n    aml_hdr10_prearm_cancel();',
         '    aml_hdr10_prearm_cancel();\n    if (sameModeGate.Exists())\n      sameModeGate.Set(0);'),
    ]:
        assert old in code, label
        run(code.replace(old, new), negative=True)
        print('REJECTED:', label)
    for label, old, new in [
        ('GUI restore skips DV-disabled', '(aml_dv_mode() != DV_MODE_ON || aml_dv_playback_active()',
         '(aml_dv_mode() != DV_MODE_ON || !aml_is_dv_enable() || aml_dv_playback_active()'),
        ('GUI restore during playback', ' || aml_dv_playback_active() ||', ' ||'),
        ('GUI restore while codec pending', ' || s_dvCodecsPending > 0)', ')'),
        ('pending release underflows', '  else if (s_dvCodecsPending > 0)\n', '  else\n'),
        ('pending not counted', '    ++s_dvCodecsPending;', '    s_dvCodecsPending = 1;'),
        ('DV-disabled not reported as switch', 'needs_mode_switch = !dv_enabled ||', 'needs_mode_switch = '),
        ('sticky bypass restores IPT', 'if (s_dvGuiSdrHold && !dv_enabled)', 'if (false)'),
        ('sticky hold suppresses enabled DV', 'if (s_dvGuiSdrHold && !dv_enabled)', 'if (s_dvGuiSdrHold)'),
    ]:
        assert old in restore, label
        run(restore.replace(old, new), negative=True)
        print('REJECTED:', label)
    for label, old, new in [
        ('HDR10+ armed', 'm_hints.hdrType == StreamHdrType::HDR_TYPE_HDR10 ||', 'm_hints.hdrType != StreamHdrType::HDR_TYPE_NONE ||'),
        ('DV route armed', ' &&\n      aml_hdr10_prearm_plain_route(m_hints.hdrType))', ')'),
        ('close cancels any owner', 'aml_hdr10_prearm_cancel(m_hdrPrearm);', 'aml_hdr10_prearm_cancel();'),
        ('HLG armed', 'AVCOL_TRC_SMPTE2084 &&', 'AVCOL_TRC_SMPTE2084 || true &&'),
        ('no close cancel', '    aml_hdr10_prearm_cancel(m_hdrPrearm);\n    m_hdrPrearm = 0;\n', '    m_hdrPrearm = 0;\n'),
        ('open not pending', '    aml_dv_codec_pending(true);\n', '\n'),
        ('decoder start keeps pending', '      m_opened = true;\n      if (m_dvCodecPending)', '      m_opened = true;\n      if (false)'),
        ('close keeps pending', '  CLog::Log(LOGINFO, "{}::{}", __MODULE_NAME__, __FUNCTION__);\n\n  if (m_dvCodecPending)', '  CLog::Log(LOGINFO, "{}::{}", __MODULE_NAME__, __FUNCTION__);\n\n  if (false)'),
    ]:
        assert old in codec, label
        try:
            codec_check(codec.replace(old, new, 1))
        except AssertionError:
            print('REJECTED:', label)
        else:
            raise AssertionError('mutant passed: ' + label)
    block = re.search(r'\s*<setting id="' + re.escape(SETTING) + r'".*?</setting>', xml_text, re.S).group(0)
    anchor = f'<setting id="{PREVIOUS}"'
    moved = xml_text.replace(block, '', 1).replace(anchor, block.strip() + '\n        ' + anchor, 1)
    for label, mutant in [
        ('moved before re-assert setting', moved),
        ('default off', xml_text.replace(block, block.replace('<default>true</default>', '<default>false</default>'), 1)),
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

#!/usr/bin/env python3
"""Exercise the production forced-shutdown loop with recording CPython API stubs.

This checks the caller contract, not a real interpreter or device shutdown.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def loop(source, marker):
    start = source.index(marker, source.index("bool CPythonInvoker::stop(bool abort)"))
    end = source.index("      // If a dialog entered its doModal()", start)
    return source[start:end]


HARNESS = r'''
#include <cassert>
#include <map>
#include <string>
#include <utility>
#include <vector>
struct Interpreter;
struct PyThreadState {
  Interpreter* interp;
  unsigned long thread_id;
  PyThreadState* next = nullptr;
  void* async_exc = nullptr;
};
struct Interpreter { PyThreadState* head = nullptr; };
int systemExit;
void* PyExc_SystemExit = &systemExit;
int LOGERROR = 1, errors = 0;
std::vector<std::pair<unsigned long, bool>> calls;
std::map<unsigned long, int> matches;
PyThreadState* PyInterpreterState_ThreadHead(Interpreter* interp) { return interp->head; }
PyThreadState* PyThreadState_Next(PyThreadState* state) { return state->next; }
int PyThreadState_SetAsyncExc(unsigned long id, void* exception) {
  assert(!exception || exception == PyExc_SystemExit);
  calls.emplace_back(id, exception != nullptr);
  return exception ? matches.at(id) : 1;
}
void Py_XDECREF(void*) {}
void Py_XINCREF(void*) {}
struct CLog {
  template<typename... Args> static void Log(int level, const char*, Args&&...) {
    assert(level == LOGERROR);
    ++errors;
  }
};
struct Invoker {
  PyThreadState* m_threadState;
  std::string m_sourceFile = "fixture";
  int GetId() const { return 1; }
  void Stop(PyThreadState* ts) {
@LOOP@
  }
};
int main() {
  Interpreter interp;
  PyThreadState first{&interp, 1}, current{&interp, 2}, vanished{&interp, 3}, duplicate{&interp, 4};
  first.next = &current; current.next = &vanished; vanished.next = &duplicate;
  interp.head = &first;
  matches = {{1, 1}, {2, 1}, {3, 0}, {4, 2}};
  Invoker invoker{&first};
  invoker.Stop(&current);
  const std::vector<std::pair<unsigned long, bool>> expected{
      {1, true}, {3, true}, {4, true}, {4, false}};
  assert(calls == expected && errors == 2);
  assert(!first.async_exc && !current.async_exc && !vanished.async_exc && !duplicate.async_exc);
  calls.clear(); errors = 0;
  interp.head = &current; current.next = nullptr;
  invoker.Stop(&current);
  assert(calls.empty() && errors == 0);
  interp.head = nullptr;
  invoker.Stop(&current);
  assert(calls.empty() && errors == 0);
}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--negative-control", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = (root / "xbmc/interfaces/python/PythonInvoker.cpp").read_text()
    actual = loop(source, "      std::vector<unsigned long> threadIds;")
    variants = [("actual", actual, True)]
    if args.negative_control:
        baseline = subprocess.check_output(
            ["git", "-C", str(root), "show",
             "fce97af5c969859be1d42fdebb19b727b6d4b5be:xbmc/interfaces/python/PythonInvoker.cpp"],
            text=True)
        variants.append(("old-direct-write",
                         loop(baseline, "      PyThreadState* state = PyInterpreterState_ThreadHead"), False))
        variants.append(("interrupt-current-thread",
                         actual.replace("if (state != ts)", "if (true)", 1), False))
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0")
    with tempfile.TemporaryDirectory(prefix="python-forced-shutdown-") as directory:
        directory = Path(directory)
        for name, fragment, passing in variants:
            cpp = directory / (name + ".cpp")
            binary = directory / name
            cpp.write_text(HARNESS.replace("@LOOP@", fragment))
            subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra",
                            "-Werror", "-Wno-unused-parameter", "-fsanitize=address,undefined",
                            "-fno-omit-frame-pointer", str(cpp), "-o", str(binary)], check=True)
            result = subprocess.run([str(binary)], env=env, capture_output=True, timeout=10)
            if passing:
                assert result.returncode == 0, result.stderr.decode()
                print("PASS production API dispatch, current-thread exclusion, empty family, error reporting and ambiguous-match rollback")
            else:
                assert result.returncode != 0 and b"Assertion" in result.stderr, result.stderr.decode()
                print("REJECTED", name)


if __name__ == "__main__":
    main()

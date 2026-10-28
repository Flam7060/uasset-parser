"""Правило 2 фазы 2: в arkre/live нет ничего, что пишет в процесс игры или управляет им."""
import ast
import os
import unittest

from tests.helpers import REPO

LIVE = os.path.join(REPO, "arkre", "live")

FORBIDDEN_NAMES = {
    "ptrace", "PTRACE_ATTACH", "PTRACE_SEIZE", "process_vm_writev", "process_vm_readv",
    "pwrite", "pwritev", "O_RDWR", "O_WRONLY", "O_APPEND", "O_CREAT", "O_TRUNC",
    "kill", "killpg", "signal", "raise_signal", "subprocess", "Popen", "system",
    "execv", "execve", "spawn", "fork", "WriteProcessMemory", "mmap",
}
FORBIDDEN_TEXT = ("wine", "proton", "compatdata", "/proc/self/mem")


def sources():
    for name in sorted(os.listdir(LIVE)):
        if name.endswith(".py"):
            path = os.path.join(LIVE, name)
            with open(path, encoding="utf-8") as fh:
                yield name, fh.read()


def docstring_nodes(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


class LiveSafetyTests(unittest.TestCase):
    def test_package_exists(self):
        self.assertTrue(list(sources()))

    def test_no_forbidden_identifiers_or_imports(self):
        for name, text in sources():
            tree = ast.parse(text)
            for node in ast.walk(tree):
                ident = None
                if isinstance(node, ast.Name):
                    ident = node.id
                elif isinstance(node, ast.Attribute):
                    ident = node.attr
                elif isinstance(node, ast.alias):
                    ident = node.name.split(".")[0]
                elif isinstance(node, ast.ImportFrom):
                    ident = (node.module or "").split(".")[0]
                self.assertNotIn(ident, FORBIDDEN_NAMES, f"{name}:{getattr(node, 'lineno', '?')}")

    def test_no_wine_or_proton_in_code_strings(self):
        for name, text in sources():
            tree = ast.parse(text)
            skip = docstring_nodes(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
                    low = node.value.lower()
                    for bad in FORBIDDEN_TEXT:
                        self.assertNotIn(bad, low, f"{name}:{node.lineno}")

    def test_every_os_open_is_read_only_on_proc(self):
        opens = 0
        for name, text in sources():
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr == "open" and isinstance(node.func.value, ast.Name) \
                        and node.func.value.id == "os":
                    opens += 1
                    flags = ast.unparse(node.args[1]) if len(node.args) > 1 else ""
                    self.assertEqual(flags, "os.O_RDONLY", f"{name}:{node.lineno}")
                    self.assertIn("/proc/", ast.unparse(node.args[0]), f"{name}:{node.lineno}")
        self.assertGreaterEqual(opens, 1)

    def test_builtin_open_only_reads_proc_or_writes_snapshots(self):
        for name, text in sources():
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                    mode = ast.unparse(node.args[1]) if len(node.args) > 1 else "'r'"
                    target = ast.unparse(node.args[0])
                    if "w" in mode:
                        # единственная запись — снимок live_snapshot во временный файл в .cache/live
                        self.assertEqual((name, "tmp"), (name, target), f"{name}:{node.lineno}")
                        self.assertEqual(name, "tools.py")
                    else:
                        self.assertTrue("proc" in target or "path" in target, f"{name}:{node.lineno} {target}")

    def test_reads_are_capped(self):
        from arkre.live import LiveError
        from arkre.live.proc import MAX_READ, Process
        self.assertEqual(MAX_READ, 1 << 20)
        p = Process.__new__(Process)
        with self.assertRaises(LiveError):
            p.rd(0x1000, MAX_READ + 1)


class NoGameTests(unittest.TestCase):
    def test_status_without_game_is_clear_error(self):
        from arkre.live import LiveError, proc, session, tools
        saved = (proc.find_pids, session.find_pids, dict(session._state))
        proc.find_pids = session.find_pids = lambda: []
        session._state.clear()
        try:
            with self.assertRaises(LiveError) as cm:
                tools.live_status()
            self.assertIn("не запущена", str(cm.exception))
        finally:
            proc.find_pids, session.find_pids = saved[0], saved[1]
            session._state.update(saved[2])

    def test_several_processes_are_not_guessed(self):
        from arkre.live import LiveError, proc
        saved = proc.find_pids
        proc.find_pids = lambda: [10, 20]
        try:
            with self.assertRaises(LiveError) as cm:
                proc.find_pid()
            self.assertIn("10", str(cm.exception))
        finally:
            proc.find_pids = saved


if __name__ == "__main__":
    unittest.main()

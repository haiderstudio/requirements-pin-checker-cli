"""Tests for requirements-pin-checker-cli. Standard library only."""

import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from requirements_pin_checker import (  # noqa: E402
    Entry,
    PinError,
    check_files,
    check_spec,
    format_json,
    format_text,
    parse_requirement,
    read_requirements,
    run,
    strip_comment,
)


def write_tmp(tmpdir, name, text):
    path = Path(tmpdir) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestCheckSpec(unittest.TestCase):
    def test_exact_pin(self):
        self.assertEqual(check_spec("==2.31.0"), (True, ""))

    def test_pin_with_spaces(self):
        self.assertEqual(check_spec("== 2.31.0"), (True, ""))
        self.assertEqual(check_spec(" ==2.31.0 "), (True, ""))

    def test_bare(self):
        pinned, reason = check_spec("")
        self.assertFalse(pinned)
        self.assertEqual(reason, "no version")

    def test_gte(self):
        pinned, reason = check_spec(">=2.0")
        self.assertFalse(pinned)
        self.assertIn(">=", reason)

    def test_compatible(self):
        pinned, reason = check_spec("~=2.31")
        self.assertFalse(pinned)
        self.assertIn("~=", reason)

    def test_not_equal(self):
        pinned, reason = check_spec("!=2.31.0")
        self.assertFalse(pinned)
        self.assertIn("!=", reason)

    def test_gt_lt_lte(self):
        for spec, op in ((">2.0", ">"), ("<3.0", "<"), ("<=3.0", "<=")):
            pinned, reason = check_spec(spec)
            self.assertFalse(pinned, spec)
            self.assertIn(op, reason)

    def test_triple_equal(self):
        pinned, reason = check_spec("===2.31.0")
        self.assertFalse(pinned)
        self.assertIn("===", reason)

    def test_wildcard_pin(self):
        pinned, reason = check_spec("==2.31.*")
        self.assertFalse(pinned)
        self.assertIn("wildcard", reason)

    def test_pin_plus_range(self):
        pinned, reason = check_spec("==2.31.0,>=2.0")
        self.assertFalse(pinned)
        self.assertIn("not an exact == pin", reason)

    def test_unreadable_spec(self):
        pinned, reason = check_spec("banana")
        self.assertFalse(pinned)
        self.assertIn("cannot read", reason)


class TestStripComment(unittest.TestCase):
    def test_full_line_comment(self):
        self.assertEqual(strip_comment("# hello"), "")

    def test_trailing_comment(self):
        self.assertEqual(strip_comment("requests==2.31.0  # pinned"), "requests==2.31.0  ")

    def test_hash_inside_word_kept(self):
        self.assertEqual(
            strip_comment("pkg @ https://example.com/x#egg=pkg"),
            "pkg @ https://example.com/x#egg=pkg",
        )


class TestParseRequirement(unittest.TestCase):
    def test_pinned(self):
        e = parse_requirement("requests==2.31.0", 3, "requirements.txt")
        self.assertTrue(e.pinned)
        self.assertEqual(e.name, "requests")
        self.assertEqual(e.line, 3)

    def test_pinned_with_extras(self):
        e = parse_requirement("requests[security]==2.31.0", 1, "r.txt")
        self.assertTrue(e.pinned)
        self.assertEqual(e.name, "requests")

    def test_pinned_with_marker(self):
        e = parse_requirement('requests==2.31.0; python_version < "3.9"', 1, "r.txt")
        self.assertTrue(e.pinned)

    def test_bare_name(self):
        e = parse_requirement("flask", 2, "r.txt")
        self.assertFalse(e.pinned)
        self.assertEqual(e.reason, "no version")

    def test_gte_reason(self):
        e = parse_requirement("django>=4.2", 1, "r.txt")
        self.assertFalse(e.pinned)
        self.assertIn(">=", e.reason)

    def test_direct_reference(self):
        e = parse_requirement("mypkg @ https://example.com/mypkg.tar.gz", 1, "r.txt")
        self.assertFalse(e.pinned)
        self.assertIn("direct reference", e.reason)

    def test_bad_name_raises(self):
        with self.assertRaises(PinError):
            parse_requirement("!!!", 1, "r.txt")


class TestReadRequirements(unittest.TestCase):
    def test_blank_and_comment_lines_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_tmp(d, "r.txt", "# comment\n\nrequests==2.31.0\n  # indented\n")
            entries = read_requirements(p)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].name, "requests")

    def test_line_numbers(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_tmp(d, "r.txt", "requests==2.31.0\nflask\ndjango>=4.2\n")
            entries = read_requirements(p)
            self.assertEqual([e.line for e in entries], [1, 2, 3])
            self.assertEqual([e.name for e in entries], ["requests", "flask", "django"])

    def test_option_lines_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_tmp(
                d,
                "r.txt",
                "--index-url https://pypi.org/simple\n-e .\n-c constraints.txt\nrequests==2.31.0\n",
            )
            entries = read_requirements(p)
            self.assertEqual([e.name for e in entries], ["requests"])

    def test_include_followed_relative(self):
        with tempfile.TemporaryDirectory() as d:
            write_tmp(d, "base.txt", "requests==2.31.0\nflask\n")
            p = write_tmp(d, "main.txt", "-r base.txt\ndjango==4.2\n")
            entries = read_requirements(p)
            self.assertEqual([e.name for e in entries], ["requests", "flask", "django"])
            self.assertEqual(entries[1].source, str(Path(d) / "base.txt"))
            self.assertEqual(entries[1].line, 2)

    def test_include_cycle_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            write_tmp(d, "a.txt", "-r b.txt\n")
            write_tmp(d, "b.txt", "-r a.txt\n")
            with self.assertRaises(PinError):
                read_requirements(Path(d) / "a.txt")

    def test_missing_file_is_error(self):
        with self.assertRaises(PinError):
            read_requirements(Path("/nonexistent/requirements.txt"))

    def test_non_utf8_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.txt"
            p.write_bytes(b"requests==2.31.0\n\xff\xfe\n")
            with self.assertRaises(PinError):
                read_requirements(p)

    def test_r_with_no_file_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_tmp(d, "r.txt", "-r\n")
            with self.assertRaises(PinError):
                read_requirements(p)


class TestCheckFiles(unittest.TestCase):
    def test_allow_skips_name(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_tmp(d, "r.txt", "flask\nrequests==2.31.0\n")
            entries, unpinned = check_files([p], {"flask"})
            self.assertEqual(len(entries), 2)
            self.assertEqual(unpinned, [])

    def test_multiple_files(self):
        with tempfile.TemporaryDirectory() as d:
            a = write_tmp(d, "a.txt", "flask\n")
            b = write_tmp(d, "b.txt", "requests==2.31.0\n")
            entries, unpinned = check_files([a, b], set())
            self.assertEqual(len(entries), 2)
            self.assertEqual([e.name for e in unpinned], ["flask"])


class TestRun(unittest.TestCase):
    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = run(argv)
        return code, out.getvalue(), err.getvalue()

    def test_exit_0_all_pinned(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_tmp(d, "r.txt", "requests==2.31.0\nflask==3.0.0\n")
            code, out, _ = self.run_cli([str(p)])
            self.assertEqual(code, 0)
            self.assertIn("OK", out)
            self.assertIn("2 entries pinned", out)

    def test_exit_1_unpinned(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_tmp(d, "r.txt", "requests==2.31.0\nflask\ndjango>=4.2\n")
            code, out, _ = self.run_cli([str(p)])
            self.assertEqual(code, 1)
            self.assertIn("UNPINNED", out)
            self.assertIn("r.txt:2: flask — no version", out)
            self.assertIn("r.txt:3: django — uses >= instead of ==", out)
            self.assertIn("3 checked, 2 unpinned", out)

    def test_exit_2_missing_file(self):
        code, _, err = self.run_cli(["/nonexistent/r.txt"])
        self.assertEqual(code, 2)
        self.assertIn("error", err)

    def test_exit_2_bad_flag(self):
        code, _, _ = self.run_cli(["--nope"])
        self.assertEqual(code, 2)

    def test_json_output(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_tmp(d, "r.txt", "requests==2.31.0\nflask\n")
            code, out, err = self.run_cli(["--json", str(p)])
            self.assertEqual(code, 1)
            data = json.loads(out)
            self.assertEqual(data["checked"], 2)
            self.assertEqual(data["unpinned"], 1)
            self.assertFalse(data["pass"])
            self.assertEqual(data["entries"][0]["name"], "flask")
            self.assertEqual(data["entries"][0]["line"], 2)
            self.assertIn("2 checked, 1 unpinned", err)

    def test_json_clean_run(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_tmp(d, "r.txt", "requests==2.31.0\n")
            code, out, err = self.run_cli(["--json", str(p)])
            self.assertEqual(code, 0)
            data = json.loads(out)
            self.assertTrue(data["pass"])
            self.assertEqual(data["entries"], [])
            self.assertIn("OK", err)

    def test_version_flag(self):
        code, out, _ = self.run_cli(["--version"])
        self.assertEqual(code, 0)
        self.assertIn("1.0.0", out)

    def test_default_file(self):
        with tempfile.TemporaryDirectory() as d:
            write_tmp(d, "requirements.txt", "flask\n")
            import os

            old = os.getcwd()
            os.chdir(d)
            try:
                code, out, _ = self.run_cli([])
            finally:
                os.chdir(old)
            self.assertEqual(code, 1)
            self.assertIn("flask", out)


class TestFormat(unittest.TestCase):
    def test_text_all_pinned(self):
        entries = [Entry("a", 1, "r.txt", True, "")]
        self.assertIn("OK", format_text(entries, []))

    def test_json_shape(self):
        entries = [Entry("a", 1, "r.txt", False, "no version")]
        data = json.loads(format_json(entries, entries))
        self.assertEqual(data["checked"], 1)
        self.assertEqual(data["entries"][0]["reason"], "no version")


class TestExamples(unittest.TestCase):
    def test_sample_output_matches_fresh_run(self):
        # Run from the repo root with a relative path, so the output
        # names files the same way the committed sample does.
        import os

        old = os.getcwd()
        os.chdir(ROOT)
        try:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = run(["examples/mixed-requirements.txt"])
        finally:
            os.chdir(old)
        self.assertEqual(code, 1)
        committed = (ROOT / "examples" / "sample-output.txt").read_text()
        self.assertEqual(out.getvalue(), committed)

    def test_pinned_example_passes(self):
        import os

        old = os.getcwd()
        os.chdir(ROOT)
        try:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = run(["examples/pinned-requirements.txt"])
        finally:
            os.chdir(old)
        self.assertEqual(code, 0)
        self.assertIn("OK", out.getvalue())

    def test_run_example_script(self):
        result = subprocess.run(
            ["sh", "examples/run-example.sh"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_readme_quotes_real_output(self):
        readme = (ROOT / "README.md").read_text()
        for line in (
            "UNPINNED  examples/mixed-requirements.txt:3: flask — no version",
            "UNPINNED  examples/included.txt:2: celery — uses >= instead of ==",
            "6 checked, 3 unpinned",
            "OK — all 5 entries pinned.",
        ):
            self.assertIn(line, readme, "README missing real output: %s" % line)


class TestRepoHygiene(unittest.TestCase):
    def test_stdlib_only(self):
        allowed = {
            "argparse", "contextlib", "io", "json", "os", "re", "subprocess",
            "sys", "tempfile", "unittest", "pathlib",
        }
        local_modules = {"requirements_pin_checker"}
        for path in list((ROOT / "src").rglob("*.py")) + list(
            (ROOT / "tests").rglob("*.py")
        ):
            for line in path.read_text().splitlines():
                line = line.strip()
                if line.startswith("import "):
                    for name in line[7:].split(","):
                        top = name.strip().split(".")[0].split(" ")[0]
                        self.assertIn(
                            top, allowed | local_modules, "%s in %s" % (top, path)
                        )
                elif line.startswith("from "):
                    top = line.split()[1].split(".")[0]
                    self.assertIn(
                        top, allowed | local_modules, "%s in %s" % (top, path)
                    )

    def test_no_placeholders_or_secrets(self):
        banned = (
            "TODO",
            "FIXME",
            "lorem",
            "coming soon",
            "API_KEY",
            "api_key",
            "password =",
        )
        # The tests directory is excluded from this scan: the test file
        # itself names the banned words in its own check list, and it
        # uses temp paths. The shipped content is what must be clean.
        # .git is excluded too: GitHub's checkout ships sample hooks
        # under .git/hooks/ that contain "TODO" (Day 21 CI lesson).
        shipped = [
            path
            for path in ROOT.rglob("*")
            if path.is_file()
            and "__pycache__" not in str(path)
            and ".git" not in path.parts
            and "tests" not in path.parts
        ]
        for path in shipped:
            text = path.read_text(errors="ignore")
            for word in banned:
                self.assertNotIn(word, text, "%s in %s" % (word, path))

    def test_no_absolute_paths(self):
        # Same exclusion as the placeholder scan: the test file itself
        # names temp paths in its own check list.
        shipped = [
            path
            for path in ROOT.rglob("*")
            if path.is_file()
            and "__pycache__" not in str(path)
            and ".git" not in path.parts
            and "tests" not in path.parts
        ]
        for path in shipped:
            text = path.read_text(errors="ignore")
            self.assertNotIn("/home/hatch", text, "absolute path in %s" % path)
            self.assertNotIn("/tmp/", text, "absolute path in %s" % path)

    def test_gitignore_lists_env_first(self):
        lines = (ROOT / ".gitignore").read_text().splitlines()
        self.assertEqual(lines[0], ".env")


if __name__ == "__main__":
    unittest.main()

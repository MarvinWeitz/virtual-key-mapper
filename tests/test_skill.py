"""The agent skill and plugin manifests: valid metadata, and only commands and options the CLI knows."""

import contextlib
import io
import json
import re
import shlex
import unittest
from pathlib import Path

from virtual_key_mapper import __version__
from virtual_key_mapper.cli import parser

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"


def frontmatter(text: str) -> dict[str, str]:
    """Reads the flat keys of a SKILL.md frontmatter, joining folded (>-) values."""
    lines = text.splitlines()
    end = lines.index("---", 1)
    data: dict[str, str] = {}
    key = None
    for line in lines[1:end]:
        if key and line.startswith(" "):
            data[key] = f"{data[key]} {line.strip()}".strip()
            continue
        match = re.match(r"^([a-z][\w-]*):\s*(.*)$", line)
        if match:
            key, value = match.groups()
            data[key] = "" if value in (">", ">-", "|") else value
    return data


def help_text(words: list[str]) -> str:
    """Returns the help of a command path, or fails when the CLI does not know it."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
        try:
            parser().parse_args([*words, "--help"])
        except SystemExit as exit:
            if exit.code != 0:
                raise AssertionError(f"'vkm {' '.join(words)}' is not a command") from None
    return output.getvalue()


class SkillTests(unittest.TestCase):
    def skill_files(self) -> list[Path]:
        files = sorted(SKILLS.glob("*/SKILL.md"))
        self.assertTrue(files, "skills/ needs at least one skills/<name>/SKILL.md")
        return files

    def test_each_skill_has_the_name_of_its_folder_and_a_description(self):
        for file in self.skill_files():
            with self.subTest(skill=file.parent.name):
                data = frontmatter(file.read_text(encoding="utf-8"))
                self.assertEqual(data.get("name"), file.parent.name)
                self.assertRegex(data["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
                self.assertTrue(0 < len(data.get("description", "")) <= 1024)

    def test_both_plugin_manifests_name_the_plugin_and_carry_the_package_version(self):
        for path in (ROOT / "plugin.json", ROOT / ".claude-plugin" / "plugin.json"):
            with self.subTest(manifest=str(path.relative_to(ROOT))):
                manifest = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(manifest["name"], "virtual-key-mapper")
                self.assertEqual(manifest["version"], __version__)
        schema = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))["$schema"]
        self.assertRegex(schema, r"^https://agent-plugins\.org/schemas/1\.\d+\.\d+/plugin\.schema\.json$")

    def test_the_commands_in_code_blocks_parse(self):
        for file in self.skill_files():
            blocks = re.findall(r"```bash\n(.*?)```", file.read_text(encoding="utf-8"), re.DOTALL)
            for line in (line.strip() for block in blocks for line in block.splitlines()):
                if not line.startswith("vkm "):
                    continue
                with self.subTest(command=line), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    try:
                        parser().parse_args(shlex.split(line)[1:])
                    except SystemExit as exit:
                        self.assertEqual(exit.code, 0, f"'{line}' does not parse")

    def test_the_commands_and_options_named_in_the_text_exist(self):
        for file in self.skill_files():
            for span in re.findall(r"`(vkm [^`]+)`", file.read_text(encoding="utf-8")):
                words = []
                for word in span.split()[1:]:
                    if not re.fullmatch(r"[a-z][a-z-]*", word):
                        break
                    words.append(word)
                with self.subTest(command=span):
                    usage = help_text(words)
                    for option in re.findall(r"--[a-z][a-z-]*", span):
                        self.assertIn(option, usage, f"'vkm {' '.join(words)}' has no {option}")


if __name__ == "__main__":
    unittest.main()

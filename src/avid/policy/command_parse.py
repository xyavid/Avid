"""Conservative shell segmentation for policy analysis (not an OS security boundary).

The bwrap mount/net namespaces remain the enforcement boundary. A shell grammar that we
cannot analyse must never be called safe; nested interpreters and substitutions are
examined recursively with a depth limit. The executor still receives the original text.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=.*$", re.S)
_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "\n"}
_READ_COMMANDS = {
    "ls",
    "pwd",
    "cat",
    "rg",
    "grep",
    "git",
    "find",
    "sed",
    "awk",
    "base64",
    "head",
    "tail",
    "wc",
    "stat",
    "echo",
    "printf",
    "true",
    "false",
    "test",
    "date",
    "whoami",
}
_NETWORK_COMMANDS = {"curl", "wget", "ssh", "scp", "sftp", "rsync", "nc", "ncat", "telnet", "ftp"}
_WRITE_COMMANDS = {
    "rm",
    "rmdir",
    "mv",
    "cp",
    "touch",
    "mkdir",
    "ln",
    "install",
    "chmod",
    "chown",
    "chgrp",
    "tee",
    "truncate",
    "dd",
}
_INTERPRETERS = {"bash", "sh", "zsh", "powershell", "pwsh"}


@dataclass(frozen=True)
class ShellFacts:
    segments: tuple[tuple[str, ...], ...]
    capabilities: frozenset[str]
    uncertain: bool = False


def _lex(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _nested_substitutions(command: str) -> tuple[list[str], bool]:
    """Extract command substitutions before shlex removes quoting.

    Ambiguous/unbalanced constructs are marked uncertain; this parser never executes text.
    """
    nested: list[str] = []
    uncertain = False
    i = 0
    while i < len(command):
        if command[i] == "`":
            end = command.find("`", i + 1)
            if end < 0:
                uncertain = True
                break
            nested.append(command[i + 1 : end])
            i = end + 1
        elif (
            command.startswith("$(", i)
            or command.startswith("<(", i)
            or command.startswith(">(", i)
        ):
            start = i + 2
            depth = 1
            j = start
            while j < len(command) and depth:
                if command[j] == "(":
                    depth += 1
                elif command[j] == ")":
                    depth -= 1
                j += 1
            if depth:
                uncertain = True
                break
            nested.append(command[start : j - 1])
            i = j
        else:
            i += 1
    return nested, uncertain


def parse_shell(command: str, *, depth: int = 0) -> ShellFacts:
    if depth > 6:
        return ShellFacts((), frozenset({"shell_execute"}), True)
    try:
        tokens = _lex(command)
    except ValueError:
        return ShellFacts((), frozenset({"shell_execute"}), True)
    segments: list[tuple[str, ...]] = []
    current: list[str] = []
    capabilities: set[str] = set()
    uncertain = False
    for token in tokens:
        if token in _SEPARATORS:
            if current:
                segments.append(tuple(current))
                current = []
        else:
            current.append(token)
    if current:
        segments.append(tuple(current))

    for segment in segments:
        words = list(segment)
        while words and _ASSIGNMENT.match(words[0]):
            words.pop(0)
        while words and words[0] in {"env", "command", "nohup", "time", "nice"}:
            words.pop(0)
            while words and _ASSIGNMENT.match(words[0]):
                words.pop(0)
        if words and words[0] in {"sudo", "doas", "pkexec"}:
            capabilities.add("privilege_escalation")
            words.pop(0)
            while words and words[0].startswith("-"):
                words.pop(0)
        if words and words[0] == "timeout":
            words.pop(0)
            while words and words[0].startswith("-"):
                words.pop(0)
            if words:
                words.pop(0)  # duration
        if not words:
            continue
        program = words[0].rsplit("/", 1)[-1].lower()
        args = words[1:]
        capabilities.add("process_spawn")
        if any(re.search(r"(?:^|/|\\).env(?:\..*)?$", word) for word in words):
            capabilities.add("secret_access")
        if any(
            any(part in word for part in ("/.ssh/", "/.aws/", "/.config/", "id_rsa", "credentials"))
            for word in words
        ):
            capabilities.add("credential_access")
        if any(re.search(r"(?:^|[=/])/dev/(?:sd|hd|vd|nvme|mmcblk)\w*", word) for word in words):
            capabilities.add("device_access")
        if program in {"nc", "ncat", "socat"} and any(
            word in {"-l", "--listen", "-lp"} for word in args
        ):
            capabilities.add("network_listen")
        if program in _READ_COMMANDS:
            capabilities.add("filesystem_read")
        if program in _WRITE_COMMANDS or any(word in {">", ">>", ">&", "<>"} for word in words):
            capabilities.add("filesystem_write")
        if program in {"rm", "rmdir"} or (program == "find" and "-delete" in args):
            capabilities.add("filesystem_delete")
        if program == "find" and any(word in args for word in ("-exec", "-execdir", "-ok")):
            capabilities.add("shell_execute")
        if program == "base64" and any(word in args for word in ("-o", "--output")):
            capabilities.add("filesystem_write")
        if program in _NETWORK_COMMANDS:
            capabilities.add("network_connect")
        if program in {"sudo", "su", "doas", "pkexec"}:
            capabilities.add("privilege_escalation")
        if program == "git":
            # Ignore git's global -C /path and --git-dir options before the subcommand.
            rest = args[:]
            while rest and rest[0] in {"-C", "--git-dir", "--work-tree"}:
                rest = rest[2:]
            verb = rest[0] if rest else ""
            if verb not in {
                "status",
                "diff",
                "log",
                "show",
                "rev-parse",
                "ls-files",
                "fetch",
                "pull",
                "push",
                "clone",
                "ls-remote",
                "add",
                "commit",
                "reset",
                "clean",
                "restore",
                "checkout",
            }:
                uncertain = True
            if verb in {"fetch", "pull", "push", "clone", "ls-remote"}:
                capabilities.add("network_connect")
            if verb in {"push", "commit", "reset", "clean", "restore", "checkout"}:
                capabilities.add("external_side_effect")
            if verb in {"add", "commit", "reset", "clean", "restore", "checkout"}:
                capabilities.add("filesystem_write")
            if verb in {"clean"}:
                capabilities.add("filesystem_delete")
        if program in {"npm", "pnpm", "yarn", "pip", "pip3", "uv", "cargo", "go"} and any(
            word in args for word in ("install", "add", "publish", "update", "get")
        ):
            capabilities.add("network_connect")
            capabilities.add("filesystem_write")
        if program in _INTERPRETERS:
            flag = next(
                (
                    i
                    for i, arg in enumerate(args)
                    if (arg.startswith("-") and "c" in arg[1:])
                    or arg.lower() in {"-command", "-encodedcommand"}
                ),
                None,
            )
            if flag is not None:
                capabilities.add("shell_execute")
                if flag + 1 >= len(args) or args[flag].lower() == "-encodedcommand":
                    uncertain = True
                else:
                    child = parse_shell(args[flag + 1], depth=depth + 1)
                    segments.extend(child.segments)
                    capabilities.update(child.capabilities)
                    uncertain |= child.uncertain
        if program in {"python", "python3", "node"} and any(
            arg in {"-c", "-e", "--eval"} for arg in args
        ):
            capabilities.add("shell_execute")
        if program not in _READ_COMMANDS | _WRITE_COMMANDS | _NETWORK_COMMANDS | _INTERPRETERS | {
            "git",
            "python",
            "python3",
            "node",
            "npm",
            "pnpm",
            "yarn",
            "pip",
            "pip3",
            "uv",
            "cargo",
            "go",
            "sudo",
            "su",
            "doas",
            "pkexec",
            "cd",
        }:
            uncertain = True

    nested, incomplete = _nested_substitutions(command)
    uncertain |= incomplete
    for child_text in nested:
        child = parse_shell(child_text, depth=depth + 1)
        segments.extend(child.segments)
        capabilities.update(child.capabilities)
        uncertain |= child.uncertain
        capabilities.add("shell_execute")
    return ShellFacts(tuple(segments), frozenset(capabilities), uncertain)

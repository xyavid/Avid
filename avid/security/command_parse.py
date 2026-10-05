"""Conservative shell segmentation for policy analysis; the policy ladder, not this parser, enforces.

分段按 POSIX 词法近似，PowerShell 命令靠动词表识别（cmdlet 名带连字符，与
POSIX 程序名不冲突）；两边都识别不了的程序本来就是 uncertain，走交人档。
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

# Matches a leading VAR=value assignment so it can be stripped before the program name is read.
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=.*$", re.S)
_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "\n"}
# Program families that grant a base capability; git and interpreters are refined further below.
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
# PowerShell 动词表：cmdlet 靠名字识别，语义与 POSIX 同名档对齐（读/写/删/网）。
_POWERSHELL_READ = {
    "get-childitem",
    "get-content",
    "get-item",
    "get-itemproperty",
    "get-location",
    "get-process",
    "get-service",
    "get-command",
    "get-member",
    "get-date",
    "get-random",
    "select-string",
    "measure-object",
    "test-path",
    "split-path",
    "join-path",
    "resolve-path",
    "compare-object",
    "write-output",
    "write-host",
    "out-string",
    "format-table",
    "format-list",
    "where-object",
    "foreach-object",
    "select-object",
    "sort-object",
    "convertto-json",
    "convertfrom-json",
}
_POWERSHELL_WRITE = {
    "set-content",
    "add-content",
    "new-item",
    "copy-item",
    "move-item",
    "rename-item",
    "out-file",
    "set-itemproperty",
    "new-itemproperty",
    "export-csv",
    "export-clixml",
}
_POWERSHELL_DELETE = {"remove-item", "clear-content", "clear-item"}
_POWERSHELL_NETWORK = {"invoke-webrequest", "invoke-restmethod", "send-mailmessage"}
# 只改导航不改状态：与 POSIX 的 cd 一样不带能力、也不算未知程序。
_POWERSHELL_NAV = {"set-location", "push-location", "pop-location"}


@dataclass(frozen=True)
class ShellFacts:
    """Facts from a command: its segments, the capabilities it implies, and parse uncertainty."""

    segments: tuple[tuple[str, ...], ...]
    capabilities: frozenset[str]
    uncertain: bool = False  # true when the parser cannot prove the construct safe


def _lex(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    # Comments stay disabled so a '#' inside the command is lexed as an ordinary word.
    lexer.commenters = ""
    return list(lexer)


def _nested_substitutions(command: str) -> tuple[list[str], bool]:
    """Extracts command substitutions before shlex strips quoting; unbalanced ones are uncertain."""
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
    """Splits a command into segments and derives capabilities, recursing into interpreters."""
    # Past the depth limit the command counts as an uncertain shell execution, never as safe.
    if depth > 6:
        return ShellFacts((), frozenset({"shell_execute"}), True)
    try:
        tokens = _lex(command)
    except ValueError:
        # Text shlex cannot tokenize is uncertain rather than silently empty and harmless.
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
        # Leading assignments and wrapper commands are stripped so the real program name is reached.
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
                words.pop(0)  # the duration argument is not the program
        if not words:
            continue
        # Only the basename is compared, so /usr/bin/rm is treated as rm; Windows paths
        # carry backslash separators, so they are stripped the same way.
        program = words[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
        args = words[1:]
        capabilities.add("process_spawn")
        # A word with $ expands to a value whose target cannot be proven ($VAR may name
        # anything), so the whole segment stays unproven instead of guessing.
        if any("$" in word for word in words):
            uncertain = True
        # Secret file names are matched anywhere in the word because the path may carry a prefix.
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
        if program in _POWERSHELL_READ:
            capabilities.add("filesystem_read")
        if program in _POWERSHELL_WRITE:
            capabilities.add("filesystem_write")
        if program in _POWERSHELL_DELETE:
            capabilities.add("filesystem_delete")
        if program in _POWERSHELL_NETWORK:
            capabilities.add("network_connect")
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
                # An unrecognized git verb makes the whole command unprovable.
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
                # Only -c and -Command expose the child text; -EncodedCommand and a missing
                # argument stay opaque and therefore uncertain.
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
            # An inline eval flag runs arbitrary code, so it counts as shell execution.
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
        } | _POWERSHELL_READ | _POWERSHELL_WRITE | _POWERSHELL_DELETE | _POWERSHELL_NETWORK | _POWERSHELL_NAV:
            # A program outside the known tables is unprovable, so it is reported uncertain.
            uncertain = True

    nested, incomplete = _nested_substitutions(command)
    uncertain |= incomplete
    # Commands hidden inside substitutions are parsed too and count as shell execution.
    for child_text in nested:
        child = parse_shell(child_text, depth=depth + 1)
        segments.extend(child.segments)
        capabilities.update(child.capabilities)
        uncertain |= child.uncertain
        capabilities.add("shell_execute")
    return ShellFacts(tuple(segments), frozenset(capabilities), uncertain)

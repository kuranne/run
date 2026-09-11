import re
from pathlib import Path
from typing import Optional, List, Union
from functools import lru_cache

@lru_cache(maxsize=512)
def compile_glob(pattern: str, case_sensitive: bool = True) -> re.Pattern:
    """
    Compile a gitignore-style glob pattern into a regular expression.
    Supports '*' within directory boundaries and '**' across directory boundaries.
    """
    clean_pat = pattern.strip()
    if clean_pat.startswith("/"):
        clean_pat = clean_pat[1:]
    if clean_pat.endswith("/"):
        clean_pat = clean_pat[:-1]

    # Collapse consecutive wildcards and redundant globstar tokens to prevent ReDoS
    clean_pat = re.sub(r"\*{3,}", "**", clean_pat)
    clean_pat = re.sub(r"(?:\*\*/)+", "**/", clean_pat)
    clean_pat = re.sub(r"(?:/\*\*)+", "/**", clean_pat)

    i, n = 0, len(clean_pat)
    res = []
    while i < n:
        c = clean_pat[i]
        i += 1
        if c == "*":
            if i < n and clean_pat[i] == "*":
                i += 1
                if i < n and clean_pat[i] == "/":
                    i += 1
                    res.append("(?:[^/]+/)*")
                else:
                    res.append(".*")
            else:
                res.append("[^/]*")
        elif c == "?":
            res.append("[^/]")
        elif c == "[":
            j = i
            if j < n and clean_pat[j] == "!":
                j += 1
            if j < n and clean_pat[j] == "]":
                j += 1
            while j < n and clean_pat[j] != "]":
                j += 1
            if j >= n:
                res.append(re.escape("["))
            else:
                stuff = clean_pat[i:j].replace("\\", "\\\\")
                i = j + 1
                if stuff[0] == "!":
                    stuff = "^" + stuff[1:]
                elif stuff[0] == "^":
                    stuff = "\\" + stuff
                res.append(f"[{stuff}]")
        else:
            res.append(re.escape(c))

    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(r"^" + "".join(res) + r"$", flags)


def has_glob_wildcard(pattern: str) -> bool:
    """
    Check if a pattern string contains glob wildcard characters.
    """
    return any(c in pattern for c in ("*", "?", "["))


def match_path(file_path: Union[Path, str], pattern: str, root_dir: Optional[Union[Path, str]] = None, case_sensitive: bool = True) -> bool:
    """
    Match a path against a glob pattern using gitignore semantics.
    Patterns without slashes match against filename only.
    Patterns with slashes match against relative path from root_dir.
    """
    path_obj = Path(file_path)
    clean_pat = pattern.strip()
    if not clean_pat:
        return False

    norm_pat = clean_pat.rstrip("/")
    if "/" in norm_pat:
        if root_dir is not None:
            try:
                rel_path = path_obj.resolve().relative_to(Path(root_dir).resolve()).as_posix()
            except ValueError:
                rel_path = path_obj.as_posix()
        else:
            rel_path = path_obj.as_posix()

        regex = compile_glob(clean_pat, case_sensitive)
        return bool(regex.match(rel_path))
    else:
        regex = compile_glob(clean_pat, case_sensitive)
        return bool(regex.match(path_obj.name))


def match_extension(ext: str, pattern: str, case_sensitive: bool = True) -> bool:
    """
    Match a file extension against an extension or glob pattern.
    Supports '.ext', 'ext', '*.ext', and wildcards like '*.tmp*'.
    """
    if not ext or not pattern:
        return False

    ext_norm = ext if ext.startswith(".") else f".{ext}"
    pat = pattern.strip()
    if not pat:
        return False

    if pat.startswith("*"):
        regex = compile_glob(pat, case_sensitive)
        return bool(regex.match(ext_norm))

    if not pat.startswith("."):
        pat = f".{pat}"

    regex = compile_glob(pat, case_sensitive)
    return bool(regex.match(ext_norm))


def find_matching_manifests(directory: Union[Path, str], pattern: str, case_sensitive: bool = True) -> List[Path]:
    """
    Find files directly within directory matching a pattern.
    Returns alphabetically sorted list of matching Paths.
    """
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return []

    matches = []
    try:
        for item in dir_path.iterdir():
            if item.is_file() and match_path(item, pattern, root_dir=dir_path, case_sensitive=case_sensitive):
                matches.append(item)
    except OSError:
        return []

    matches.sort(key=lambda p: p.name)
    return matches

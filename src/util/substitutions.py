import os
import re
from pathlib import Path
from typing import Dict, List, Optional

class VariableSubstitutor:
    """
    Substitutes dynamic variables in template strings and lists.
    Supported variables:
    - ${file}: full source file path
    - ${filename}: base filename with extension
    - ${name} / ${stem}: filename without extension
    - ${ext}: file extension with leading dot
    - ${dir} / ${parent}: parent directory of file
    - ${out} / ${executable}: resolved executable output path
    - ${out_dir}: output directory
    - ${env:VAR_NAME}: environment variable value
    """

    VAR_PATTERN = re.compile(r'\$\{([a-zA-Z0-9_:]+)\}')

    @classmethod
    def build_file_context(cls, file_path: Optional[Path] = None, out_path: Optional[Path] = None,
                           out_dir: Optional[str] = None) -> Dict[str, str]:
        """
        Build substitution dictionary for a given source and output path.

        Args:
            file_path (Optional[Path]): Source file path.
            out_path (Optional[Path]): Output binary path.
            out_dir (Optional[str]): Output directory string.

        Returns:
            Dict[str, str]: Key-value variable mapping.
        """
        ctx: Dict[str, str] = {}
        if file_path:
            ctx["file"] = str(file_path)
            ctx["filename"] = file_path.name
            ctx["name"] = file_path.stem
            ctx["stem"] = file_path.stem
            ctx["ext"] = file_path.suffix
            ctx["dir"] = str(file_path.parent)
            ctx["parent"] = str(file_path.parent)
        if out_path:
            ctx["out"] = str(out_path)
            ctx["executable"] = str(out_path)
        if out_dir:
            ctx["out_dir"] = out_dir
        return ctx

    @classmethod
    def substitute_string(cls, template: str, context: Dict[str, str]) -> str:
        """
        Replace variable placeholders in a single string.

        Args:
            template (str): Input string containing placeholders.
            context (Dict[str, str]): Variable replacements.

        Returns:
            str: String with variables replaced.
        """
        def replace(match):
            key = match.group(1)
            if key.startswith("env:"):
                env_var = key[4:]
                val = os.environ.get(env_var, "")
                import shlex
                return shlex.quote(val)
            return context.get(key, match.group(0))

        return cls.VAR_PATTERN.sub(replace, template)

    @classmethod
    def substitute_list(cls, templates: List[str], context: Dict[str, str]) -> List[str]:
        """
        Replace variable placeholders across a list of strings.

        Args:
            templates (List[str]): List of strings containing placeholders.
            context (Dict[str, str]): Variable replacements.

        Returns:
            List[str]: New list with variables replaced.
        """
        return [cls.substitute_string(item, context) for item in templates]

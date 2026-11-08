"""tools_files.py — 文件工具兼容层。

文件读写/编辑工具已拆分到 `gantry/tool_plugins/` 下的独立插件文件
（每个工具一个文件，便于维护）。本模块只保留：
- `_guard_printable`：向后兼容导出（旧测试/调用方仍可 import）；
- `FilesMixin`：兼容 shim，把 `registry.read_text(...)` 这类**直接方法调用**
  转发到插件注册到 `self._tools` 的真实实现。
"""
from __future__ import annotations

from typing import Any

from .tools import ToolError  # noqa: F401


def _guard_printable(text: str) -> None:
    """向后兼容：真正的实现在 tool_plugins/_files_utils.guard_printable。"""
    from .tool_plugins._files_utils import guard_printable
    return guard_printable(text)


class FilesMixin:
    """兼容 shim：文件工具的真实实现都在 gantry/tool_plugins/*.py。

    保留这些方法是为了让历史代码与单测可以继续写 `registry.read_text(...)`、
    `ToolRegistry(ws, {}).write_text(...)`，而不用改成 `_tools[...]` 调用。
    """

    # ── 旧版私有辅助方法：其它 mixin（skills/self/memory）仍在用 ──
    @staticmethod
    def _hash(path: Any) -> str | None:
        from .tool_plugins._files_utils import hash_file
        return hash_file(path)

    def _read_key(self, path: str) -> str:
        from .tool_plugins._files_utils import read_key
        return read_key(self, path)

    def _mark_read(self, path: str) -> None:
        from .tool_plugins._files_utils import mark_read
        return mark_read(self, path)

    def _require_read(self, path: str) -> None:
        from .tool_plugins._files_utils import require_read
        return require_read(self, path)

    @staticmethod
    def _diff_payload(path: str, before: str, after: str) -> dict[str, Any]:
        from .tool_plugins._files_utils import diff_payload
        return diff_payload(path, before, after)

    def _check_plain_code_size(self, path: str, content: str) -> None:
        from .tool_plugins._files_utils import check_plain_code_size
        return check_plain_code_size(self, path, content)

    @staticmethod
    def _decode_b64(content_b64: str) -> str:
        from .tool_plugins._files_utils import decode_b64
        return decode_b64(content_b64)

    @staticmethod
    def _nearest_hint(text: str, old: str) -> str:
        from .tool_plugins._files_utils import nearest_hint
        return nearest_hint(text, old)

    def _python_symbol_table(self, text: str) -> list[dict[str, Any]]:
        from .tool_plugins._files_utils import python_symbol_table
        return python_symbol_table(text)

    async def _file_tool(self, name: str, *args: Any, **kwargs: Any) -> Any:
        fn = self._tools.get(name)
        if fn is None:
            raise ToolError(f"文件工具未加载: {name}（检查 gantry/tool_plugins/ 与插件加载器）")
        return await fn(*args, **kwargs)

    async def list_directory(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("list_directory", *args, **kwargs)

    async def list_directory_tree(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("list_directory_tree", *args, **kwargs)

    async def find_files(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("find_files", *args, **kwargs)

    async def read_text(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("read_text", *args, **kwargs)

    async def python_symbols(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("python_symbols", *args, **kwargs)

    async def replace_python_symbol(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("replace_python_symbol", *args, **kwargs)

    async def write_text(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("write_text", *args, **kwargs)

    async def append_text(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("append_text", *args, **kwargs)

    async def write_text_b64(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("write_text_b64", *args, **kwargs)

    async def append_text_b64(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("append_text_b64", *args, **kwargs)

    async def replace_text(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("replace_text", *args, **kwargs)

    async def replace_lines(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("replace_lines", *args, **kwargs)

    async def apply_patch(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("apply_patch", *args, **kwargs)

    async def convert_indentation(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("convert_indentation", *args, **kwargs)

    async def indent_lines(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("indent_lines", *args, **kwargs)

    async def make_directory(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("make_directory", *args, **kwargs)

    async def copy_path(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("copy_path", *args, **kwargs)

    async def move_path(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("move_path", *args, **kwargs)

    async def delete_path(self, *args: Any, **kwargs: Any) -> Any:
        return await self._file_tool("delete_path", *args, **kwargs)

"""Windows file properties for the .exe, built from version.py.

Used by build.spec and build_onefile.spec. After building, right-click
EmailNameResolver.exe > Properties > Details shows the version, product
name, description and copyright from version.py.
"""

from __future__ import annotations

from typing import Any, Optional

import version as v


def build_version_info() -> Optional[Any]:
    """Return a PyInstaller ``VSVersionInfo`` for the .exe, or None off Windows.

    PyInstaller's version-resource module only imports on Windows (it needs
    pywin32/pefile), so other platforms simply build without it.
    """
    try:
        from PyInstaller.utils.win32.versioninfo import (
            FixedFileInfo,
            StringFileInfo,
            StringStruct,
            StringTable,
            VarFileInfo,
            VarStruct,
            VSVersionInfo,
        )
    except ImportError:
        return None

    numbers = v.version_tuple()
    strings = [
        StringStruct("CompanyName", v.DEVELOPER),
        StringStruct("FileDescription", v.APP_NAME),
        StringStruct("FileVersion", v.__version__),
        StringStruct("InternalName", v.APP_ID),
        StringStruct("LegalCopyright", v.copyright_line()),
        StringStruct("OriginalFilename", f"{v.APP_ID}.exe"),
        StringStruct("ProductName", v.APP_NAME),
        StringStruct("ProductVersion", v.__version__),
        StringStruct("Comments", f"{v.DESCRIPTION}. {v.REPO_URL}"),
    ]
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([StringTable("040904B0", strings)]),   # US English, Unicode
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )

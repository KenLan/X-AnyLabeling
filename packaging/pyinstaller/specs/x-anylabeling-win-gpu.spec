# -*- mode: python -*-
# vim: ft=python

import glob
import importlib.util
import os
import re
import sys

from PyInstaller.utils.hooks import collect_data_files

sys.setrecursionlimit(5000)  # required on Windows

def _resolve_root_dir():
    env_root = os.environ.get('X_ANYLABELING_ROOT')
    if env_root:
        return os.path.abspath(env_root)

    spec_path = globals().get('SPEC')
    if isinstance(spec_path, str) and spec_path:
        return os.path.abspath(os.path.join(os.path.dirname(spec_path), '..', '..', '..'))

    return os.path.abspath(os.getcwd())

ROOT_DIR = _resolve_root_dir()

def _p(*parts):
    return os.path.join(ROOT_DIR, *parts)

def _add_conda_dll_search_path():
    conda_library_bin = os.path.join(sys.base_prefix, 'Library', 'bin')
    if not os.path.isdir(conda_library_bin):
        return

    path_entries = [
        path for path in os.environ.get('PATH', '').split(os.pathsep) if path
    ]
    normalized_path = os.path.normcase(os.path.abspath(conda_library_bin))
    if any(
        os.path.normcase(os.path.abspath(path)) == normalized_path
        for path in path_entries
    ):
        return

    os.environ['PATH'] = os.pathsep.join([conda_library_bin] + path_entries)
    print("PyInstaller spec: added Conda DLL search path:", conda_library_bin)

def _load_version():
    app_info_path = _p('anylabeling', 'app_info.py')
    with open(app_info_path, 'r', encoding='utf-8') as f:
        content = f.read()
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
    if not match:
        raise RuntimeError(f"Failed to read __version__ from: {app_info_path}")
    return match.group(1)

__version__ = _load_version()

MSVC_RUNTIME_DLLS = {
    'msvcp140.dll',
    'msvcp140_1.dll',
    'msvcp140_2.dll',
    'msvcp140_atomic_wait.dll',
    'vcruntime140.dll',
    'vcruntime140_1.dll',
    'concrt140.dll',
    'vcomp140.dll',
}

def _entry_dll_names(entry):
    names = []
    if isinstance(entry, (tuple, list)):
        for value in entry[:2]:
            if isinstance(value, str):
                names.append(os.path.basename(value).lower())
    return names

def _collect_onnxruntime_dlls():
    ort_spec = importlib.util.find_spec('onnxruntime')
    if ort_spec is None or ort_spec.origin is None:
        return []

    ort_capi = os.path.join(os.path.dirname(ort_spec.origin), 'capi')
    if not os.path.isdir(ort_capi):
        return []

    ort_dlls = [
        os.path.join(ort_capi, filename)
        for filename in os.listdir(ort_capi)
        if filename.lower().endswith('.dll')
    ]
    root_copy_names = (
        'onnxruntime_providers_shared.dll',
        'onnxruntime.dll',
    )
    root_copy_dlls = [
        os.path.join(ort_capi, name)
        for name in root_copy_names
        if os.path.isfile(os.path.join(ort_capi, name))
    ]
    return (
        [(dll, 'onnxruntime/capi') for dll in ort_dlls]
        + [(dll, '.') for dll in root_copy_dlls]
    )

def _collect_nvidia_cuda_dlls():
    """Bundle CUDA/cuDNN DLLs shipped by the pip ``nvidia-*-cu12`` wheels.

    ONNX Runtime GPU resolves its CUDA 12 dependencies from these wheels when
    no system-wide CUDA toolkit is installed. The stock PyInstaller hooks only
    pick up the primary DLL of each wheel (e.g. the 0.3 MB cuDNN loader
    without its sub-libraries), so collect every DLL instead and copy them
    flat next to the payload; the ort_dll_bootstrap runtime hook already adds
    that directory to the DLL search path.
    """
    nvidia_spec = importlib.util.find_spec('nvidia')
    if nvidia_spec is None or not nvidia_spec.submodule_search_locations:
        return []

    site_packages = os.path.dirname(
        next(iter(nvidia_spec.submodule_search_locations))
    )
    binaries = []
    for dll_path in sorted(
        glob.glob(os.path.join(site_packages, 'nvidia', '*', 'bin', '*.dll'))
    ):
        binaries.append((dll_path, '.'))
    if binaries:
        print(
            "PyInstaller spec: bundled NVIDIA CUDA DLLs:",
            len(binaries), "files",
        )
    return binaries

def _collect_msvc_runtime_dlls():
    system_root = os.environ.get('SystemRoot') or os.environ.get('WINDIR')
    candidate_dirs = []
    if system_root:
        candidate_dirs.append(os.path.join(system_root, 'System32'))
    candidate_dirs.append(os.path.dirname(sys.executable))
    candidate_dirs.append(sys.base_prefix)
    candidate_dirs.extend(os.environ.get('PATH', '').split(os.pathsep))

    search_dirs = []
    seen_dirs = set()
    for directory in candidate_dirs:
        if not directory or not os.path.isdir(directory):
            continue
        normalized = os.path.normcase(os.path.abspath(directory))
        if normalized in seen_dirs:
            continue
        seen_dirs.add(normalized)
        search_dirs.append(directory)

    binaries = []
    collected = []
    for dll_name in sorted(MSVC_RUNTIME_DLLS):
        for directory in search_dirs:
            dll_path = os.path.join(directory, dll_name)
            if os.path.isfile(dll_path):
                binaries.append((dll_path, '.'))
                collected.append(dll_name)
                break
    if collected:
        print(
            "PyInstaller spec: bundled MSVC runtime DLLs:",
            ", ".join(sorted(set(collected))),
        )
    return binaries

def _to_binary_toc_entries(binaries):
    toc_entries = []
    for src_path, _ in binaries:
        toc_entries.append((os.path.basename(src_path), src_path, 'BINARY'))
    return toc_entries

def _strip_msvc_runtime_binaries(binaries):
    kept = []
    removed = []
    for entry in binaries:
        names = _entry_dll_names(entry)
        if any(name in MSVC_RUNTIME_DLLS for name in names):
            removed.extend(name for name in names if name in MSVC_RUNTIME_DLLS)
            continue
        kept.append(entry)
    if removed:
        print(
            "PyInstaller spec: excluded bundled MSVC runtime DLLs:",
            ", ".join(sorted(set(removed))),
        )
    return kept

def _strip_nvidia_package_binaries(binaries):
    """Drop the incomplete ``nvidia/<pkg>/bin`` hook copies.

    The flat root copies from _collect_nvidia_cuda_dlls replace them, so the
    partial hook-collected duplicates only bloat the bundle.
    """
    kept = []
    removed = 0
    for entry in binaries:
        dest = ''
        if isinstance(entry, (tuple, list)) and entry and isinstance(entry[0], str):
            dest = entry[0]
        if dest.replace('\\', '/').startswith('nvidia/'):
            removed += 1
            continue
        kept.append(entry)
    if removed:
        print(
            "PyInstaller spec: excluded duplicate NVIDIA package DLLs:",
            removed, "files",
        )
    return kept

_add_conda_dll_search_path()
onnxruntime_binaries = _collect_onnxruntime_dlls()
nvidia_cuda_binaries = _collect_nvidia_cuda_dlls()
msvc_runtime_binaries = _collect_msvc_runtime_dlls()
matplotlib_datas = collect_data_files('matplotlib')

# 云端配置只读凭据：构建时从仓库根注入 exe（本地 gitignored 文件，
# 严禁进入源码库；缺失时给出提示并跳过，程序仍可运行但无法云同步）。
oss_ro_env_path = _p('oss_ro.env')
oss_ro_env_datas = []
if os.path.isfile(oss_ro_env_path):
    oss_ro_env_datas.append((oss_ro_env_path, '.'))
    print("PyInstaller spec: bundled OSS read-only credentials (oss_ro.env)")
else:
    print(
        "PyInstaller spec: WARNING - oss_ro.env not found;"
        " the exe will not be able to sync cloud config"
    )

a = Analysis(
    [_p('anylabeling', 'app.py')],
    pathex=[_p('anylabeling')],
    binaries=onnxruntime_binaries + nvidia_cuda_binaries,
    datas=[
        (_p('anylabeling', 'services', 'auto_labeling', 'osam', 'clip', 'bpe_simple_vocab_16e6.txt.gz'), 'anylabeling/services/auto_labeling/osam/clip'),
        (_p('anylabeling', 'configs', 'auto_labeling', '*.yaml'), 'anylabeling/configs/auto_labeling'),
        (_p('anylabeling', 'configs', '*.yaml'), 'anylabeling/configs'),
        (_p('anylabeling', 'services', 'auto_training', 'ultralytics', 'worker.py'), 'anylabeling/services/auto_training/ultralytics'),
        (_p('anylabeling', 'views', 'labeling', 'widgets', 'auto_labeling', 'auto_labeling.ui'), 'anylabeling/views/labeling/widgets/auto_labeling'),
        (_p('anylabeling', 'services', 'auto_labeling', 'configs', 'bert', '*'), 'anylabeling/services/auto_labeling/configs/bert'),
        (_p('anylabeling', 'services', 'auto_labeling', 'configs', 'clip', '*'), 'anylabeling/services/auto_labeling/configs/clip'),
        (_p('anylabeling', 'services', 'auto_labeling', 'configs', 'ppocr', '*'), 'anylabeling/services/auto_labeling/configs/ppocr'),
        (_p('anylabeling', 'services', 'auto_labeling', 'configs', 'ram', '*'), 'anylabeling/services/auto_labeling/configs/ram')
    ] + oss_ro_env_datas + matplotlib_datas,
    hiddenimports=[
        'matplotlib',
        'matplotlib.backends.backend_agg',
        'matplotlib.font_manager',
        'matplotlib.mathtext',
        # OSS 云端配置同步（无官方 PyInstaller hook，需手动收集）
        'oss2',
        'crcmod',
    ],
    hookspath=[],
    runtime_hooks=[_p('packaging', 'pyinstaller', 'runtime_hooks', 'ort_dll_bootstrap.py')],
    excludes=[],
)
a.binaries = _strip_msvc_runtime_binaries(a.binaries)
a.binaries = _strip_nvidia_package_binaries(a.binaries)
if msvc_runtime_binaries:
    a.binaries += _to_binary_toc_entries(msvc_runtime_binaries)
pyz = PYZ(a.pure, a.zipped_data)
if os.environ.get('X_ANYLABELING_PACKAGE_MODE') == 'onedir':
    exe = EXE(
        pyz,
        a.scripts,
        exclude_binaries=True,
        contents_directory=__version__,
        name='X-AnyLabeling',
        debug=False,
        strip=False,
        upx=False,
        console=False,
        icon=_p('anylabeling', 'resources', 'images', 'icon.ico'),
    )
    app = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        name='X-AnyLabeling',
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        name=f'X-AnyLabeling-v{__version__}-GPU',
        debug=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=False,
        icon=_p('anylabeling', 'resources', 'images', 'icon.icns'),
    )
    app = BUNDLE(
        exe,
        name='X-AnyLabeling.app',
        icon=_p('anylabeling', 'resources', 'images', 'icon.icns'),
        bundle_identifier=None,
        info_plist={'NSHighResolutionCapable': 'True'},
    )

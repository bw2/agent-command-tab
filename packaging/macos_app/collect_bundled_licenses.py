"""Copies the licenses of the parts AgentCommandTab.app bundles into its Contents/Resources/licenses
folder, one folder per part: Python, the Python packages in the app, py2app (whose launcher and
startup script every app it builds holds), and the native libraries in Contents/Frameworks, found in
Homebrew, whose Python build_app.sh bundles. These licenses ask for their notices to come with every
copy. Stops with an error for a part whose license it can't find.

build_app.sh runs it with the build environment's Python, which has the packages py2app bundled.

Usage: python3 collect_bundled_licenses.py AgentCommandTab.app
"""

import glob
import importlib.metadata
import os
import platform
import shutil
import sys
import sysconfig
import zipfile

PACKAGING_FOLDER = os.path.dirname(os.path.abspath(__file__))
HOMEBREW_PREFIX = "/opt/homebrew"
LICENSE_FILE_NAME_STARTS = ("LICENSE", "COPYING", "COPYRIGHT", "NOTICE")


def list_top_level_names_in_app(app_path):
    """The names of the top-level Python modules and packages in the app, without file extensions."""
    library_folder = os.path.join(app_path, "Contents", "Resources", "lib")
    python_folder = os.path.join(library_folder, f"python{sys.version_info.major}.{sys.version_info.minor}")
    names = set(os.listdir(python_folder)) | set(os.listdir(os.path.join(python_folder, "lib-dynload")))
    with zipfile.ZipFile(os.path.join(library_folder, f"python{sys.version_info.major}{sys.version_info.minor}.zip")) as zip_file:
        names |= {zip_file_name.split("/")[0] for zip_file_name in zip_file.namelist()}
    return {name.split(".")[0] for name in names}


def list_top_level_names_of_package(distribution):
    """The names of the top-level modules and packages an installed package put in site-packages."""
    return {file_path.parts[0].split(".")[0] for file_path in distribution.files or []
            if not file_path.parts[0].endswith(".dist-info") and file_path.parts[0] not in ("..", "__pycache__")}


def find_license_files_of_package(distribution):
    """The paths of the license files in an installed package's .dist-info folder, or of the files
    that hold its license when its wheel has none."""
    license_file_paths = sorted(str(distribution.locate_file(file_path)) for file_path in distribution.files or []
                                if file_path.parts[0].endswith(".dist-info")
                                and (file_path.parts[1] == "licenses" or file_path.name.upper().startswith(LICENSE_FILE_NAME_STARTS)))
    if license_file_paths:
        return license_file_paths
    if distribution.name == "iterm2":
        return [os.path.join(PACKAGING_FOLDER, "GPL-2.0.txt")]  # GPL-2.0-or-later (NOTICE.txt)
    if distribution.name == "pyobjc-core":  # PyObjC's license, which its other packages' wheels hold
        return find_license_files_of_package(importlib.metadata.distribution("pyobjc-framework-Cocoa"))
    raise SystemExit(f"no license file found for the bundled package {distribution.name}")


def find_homebrew_package_folder_of_library(library_file_name):
    """The Homebrew package folder (Cellar/<name>/<version>) that holds a library of this file name."""
    package_folders = {os.path.dirname(os.path.dirname(os.path.realpath(library_path)))
                       for library_path in glob.glob(os.path.join(HOMEBREW_PREFIX, "opt", "*", "lib", library_file_name))}
    if len(package_folders) != 1:
        raise SystemExit(f"{len(package_folders)} Homebrew packages hold {library_file_name}, not 1: {sorted(package_folders)}")
    return package_folders.pop()


def list_license_files_by_bundled_part(app_path):
    """Maps a folder name per bundled part ("<name>-<version>") to the paths of its license files."""
    license_files_by_part = {f"Python-{platform.python_version()}": [os.path.join(sysconfig.get_path("stdlib"), "LICENSE.txt")]}
    names_in_app = list_top_level_names_in_app(app_path)
    distributions_by_name = {distribution.name: distribution for distribution in importlib.metadata.distributions()}
    for name, distribution in distributions_by_name.items():
        if name == "py2app" or list_top_level_names_of_package(distribution) & names_in_app:
            license_files_by_part[f"{name}-{distribution.version}"] = find_license_files_of_package(distribution)
    for folder_path, _, file_names in os.walk(os.path.join(app_path, "Contents", "Frameworks")):
        for file_name in file_names:
            if file_name.endswith(".dylib") and not os.path.islink(os.path.join(folder_path, file_name)):
                package_folder = find_homebrew_package_folder_of_library(file_name)
                license_file_paths = sorted(os.path.join(package_folder, name) for name in os.listdir(package_folder)
                                            if name.upper().startswith(LICENSE_FILE_NAME_STARTS))
                if not license_file_paths:
                    raise SystemExit(f"no license file found in {package_folder}, which holds {file_name}")
                license_files_by_part[f"{os.path.basename(os.path.dirname(package_folder))}-{os.path.basename(package_folder)}"] = license_file_paths
    return license_files_by_part


def copy_licenses_of_bundled_parts(app_path):
    """Copies each bundled part's license files into Contents/Resources/licenses/<name>-<version>/."""
    licenses_folder = os.path.join(app_path, "Contents", "Resources", "licenses")
    for part_name, license_file_paths in sorted(list_license_files_by_bundled_part(app_path).items()):
        part_folder = os.path.join(licenses_folder, part_name)
        os.makedirs(part_folder)
        for license_file_path in license_file_paths:
            copied_file_path = os.path.join(part_folder, os.path.basename(license_file_path))
            if os.path.exists(copied_file_path):
                raise SystemExit(f"two license files of {part_name} are named {os.path.basename(license_file_path)}")
            shutil.copyfile(license_file_path, copied_file_path)
        print(f"licenses/{part_name}: {', '.join(os.path.basename(path) for path in license_file_paths)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    copy_licenses_of_bundled_parts(sys.argv[1])

# Releasing

How to publish a version to PyPI, to the Homebrew tap, and as a signed and notarized app
download (with a Homebrew cask for it).

## Before the first release

- PyPI: the account ConfigArgParse is published from, whose API token is in the `[pypi]` section of `~/.pypirc`. The first upload creates the project, which only a token scoped to the whole account can do: if that token is limited to ConfigArgParse, create one for the account at https://pypi.org/manage/account/token/ (a token limited to this project can replace it after the first upload). TestPyPI (step 5) is a separate site with its own account and token: add a `[testpypi]` section to `~/.pypirc`, or skip that upload.
- Create the tap repository `bw2/homebrew-tap` on GitHub, with a `Formula/` and a `Casks/` folder.

### For the app download

- In the Apple Developer account (LongfellowLabs LLC), create a **Developer ID Application** certificate at https://developer.apple.com/account/resources/certificates/add, download it, and double-click it to put it in this Mac's login keychain. `security find-identity -v -p codesigning` then lists `Developer ID Application: <name> (<team ID>)`; that whole quoted text is the signing identity below.
- Store the notarization login once. Create an app-specific password at https://account.apple.com (Sign-In and Security, App-Specific Passwords), then run the following, which asks for that password and keeps it in the keychain:
  ```bash
  xcrun notarytool store-credentials agent-command-tab-notary --apple-id <the account's Apple ID email> --team-id <team ID>
  ```
- The app bundles Homebrew's Python 3.13 (`build_app.sh`'s default), so it runs on Apple silicon and macOS 15 or later only; Intel Macs and older macOS install with pipx or the formula. To also cover those with the app, Python 3.13 from python.org's universal2 installer (https://www.python.org/downloads/macos/) makes one that runs on Intel Macs and on macOS 10.13 or later: build with `PYTHON=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13`, change the cask's `depends_on` lines to match, and check the licenses of the native libraries that Python brings (`collect_bundled_licenses.py` looks for them in Homebrew only). Python callbacks were checked under the hardened runtime on Apple silicon only (`packaging/macos_app/entitlements.plist`).

## Each release

1. Set the version in `src/agent_command_tab/__init__.py` (`__version__`, for example `0.1.0`; `pyproject.toml` reads it from there) and commit.
2. Run the tests: `.venv/bin/python -m unittest discover -s tests -p '*_tests.py'`.
3. Build the source archive and the wheel into a clean `dist/`:
   ```bash
   rm -rf dist && python3 -m build
   ```
   (`python3 -m pip install build twine` once, if needed.)
4. Check them: `python3 -m twine check dist/*`. Optionally install the wheel into a fresh virtual environment and run `agent-command-tab --version`, which should print the new version.
5. Upload to TestPyPI first, then to PyPI:
   ```bash
   python3 -m twine upload --repository testpypi dist/*
   python3 -m twine upload dist/*
   ```
6. Tag the release: `git tag v0.1.0 && git push origin v0.1.0`.
7. Update the Homebrew formula, `packaging/homebrew/agent-command-tab.rb`:
   - `url`: the published source archive (`https://files.pythonhosted.org/packages/source/a/agent-command-tab/agent_command_tab-<version>.tar.gz`);
   - `sha256`: `curl -sL <that url> | shasum -a 256`;
   - the dependencies' `resource` blocks, after a dependency version changes: `brew update-python-resources --print-only agent-command-tab`.
8. Copy the formula into the tap's `Formula/` folder, then test it:
   ```bash
   brew install --build-from-source bw2/tap/agent-command-tab
   brew test agent-command-tab
   brew audit --strict --online agent-command-tab
   ```
   Building pyobjc from source takes a few minutes and needs the Xcode command line tools.
9. Build the app, unsigned, from a fresh build environment (about a minute). It lands in `packaging/macos_app/dist/`, next to the source archive of the `iterm2` package it bundles. The build copies the licenses of everything the app bundles into its `Contents/Resources/licenses` folder (`collect_bundled_licenses.py`, which stops if it can't find one), and takes out the list of trusted certificate authorities py2app copies from this Mac:
   ```bash
   packaging/macos_app/build_app.sh
   ```
10. Sign it, pack it into a disk image, have Apple notarize the disk image, and staple Apple's ticket to it. The script prints the disk image's SHA-256 at the end:
    ```bash
    packaging/macos_app/sign_and_notarize.sh "Developer ID Application: <name> (<team ID>)" agent-command-tab-notary
    ```
11. Try it: open `packaging/macos_app/dist/AgentCommandTab-<version>.dmg`, drag the app into Applications and open it. `spctl --assess --type execute --verbose /Applications/AgentCommandTab.app` should say `accepted` and `source=Notarized Developer ID`. Install the hooks from the list and check that a new Claude Code session reports to it (a background shell lights up).
12. Publish a GitHub release for the tag, with the disk image and the `iterm2` source archive attached. The app bundles `iterm2`, which is GPL-2.0-or-later, so the app is distributed under the GPL and its source has to be offered from the same place (`packaging/macos_app/NOTICE.txt`; REQUIREMENTS.md, Q9). GitHub adds this repository's source archives to the release itself:
    ```bash
    gh release create v<version> packaging/macos_app/dist/AgentCommandTab-<version>.dmg packaging/macos_app/dist/iterm2-<iterm2 version>.tar.gz --title "AgentCommandTab <version>"
    ```
13. Update the cask, `packaging/homebrew/agent-command-tab-app.rb` (`version` and the printed `sha256`), copy it into the tap's `Casks/` folder, and test it:
    ```bash
    brew install --cask bw2/tap/agent-command-tab-app
    brew audit --cask --strict --online bw2/tap/agent-command-tab-app
    ```
14. Push the tap repository.

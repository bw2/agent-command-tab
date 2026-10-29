# Releasing

How to publish a version to PyPI and to the Homebrew tap.

## Before the first release

- PyPI: the account ConfigArgParse is published from, whose API token is in the `[pypi]` section of `~/.pypirc`. The first upload creates the project, which only a token scoped to the whole account can do: if that token is limited to ConfigArgParse, create one for the account at https://pypi.org/manage/account/token/ (a token limited to this project can replace it after the first upload). TestPyPI (step 5) is a separate site with its own account and token: add a `[testpypi]` section to `~/.pypirc`, or skip that upload.
- Create the tap repository `bw2/homebrew-tap` on GitHub, with a `Formula/` folder.

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
9. Push the tap repository.

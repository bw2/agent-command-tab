# Releasing

How to publish a version to PyPI, from which pipx installs it.

## Before the first release

- PyPI: an account whose API token is in the `[pypi]` section of `~/.pypirc`. The first upload creates the project, which only a token scoped to the whole account can do: if your token is limited to another project, create one for the account at https://pypi.org/manage/account/token/ (a token limited to this project can replace it after the first upload). TestPyPI (step 5) is a separate site with its own account and token: add a `[testpypi]` section to `~/.pypirc`, or skip that upload.

## Each release

1. Set the version in `src/agent_command_tab/__init__.py` (`__version__`, for example `0.1.0`; `pyproject.toml` reads it from there) and commit.
2. Run the tests: `.venv/bin/python -m unittest discover -s tests -p '*_tests.py'`.
3. Build the source archive and the wheel into a clean `dist/`:
   ```bash
   rm -rf dist && .venv/bin/python -m build
   ```
   (`.venv/bin/pip install build twine` once, if needed: Homebrew's `python3` refuses `pip install`.)
4. Check them: `.venv/bin/python -m twine check dist/*`. Optionally install the wheel into a fresh virtual environment and run `agent-command-tab --version`, which should print the new version.
5. Upload to TestPyPI first, then to PyPI:
   ```bash
   .venv/bin/python -m twine upload --repository testpypi dist/*
   .venv/bin/python -m twine upload dist/*
   ```
6. Tag the release: `git tag v0.1.0 && git push origin v0.1.0`.

There is no Homebrew formula: pipx installs the app from PyPI (README, Install).

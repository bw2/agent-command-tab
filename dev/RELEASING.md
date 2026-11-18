# Releasing

How to publish a version to PyPI, from which pipx installs it.

## Before the first release

- PyPI: an account whose API token is in the `[pypi]` section of `~/.pypirc`. The first upload creates the project, which only a token scoped to the whole account can do: if your token is limited to another project, create one for the account at https://pypi.org/manage/account/token/ (a token limited to this project can replace it after the first upload). TestPyPI (step 5) is a separate site with its own account and token: add a `[testpypi]` section to `~/.pypirc`, or skip that upload.

## Each release

1. Set the version in `src/agent_command_tab/__init__.py` (`__version__`, for example `0.1.0`; `pyproject.toml` reads it from there), commit, and push the commit to `main` (`git push origin main`), so the release is on the branch and GitHub runs its checks on it. Then, in the terminal tab used for the steps below, name the release's tag after that version and check that no earlier release has it:
   ```bash
   release_tag="v$(.venv/bin/python -c 'import runpy; print(runpy.run_path("src/agent_command_tab/__init__.py")["__version__"])')"
   [ -z "$(git tag --list "$release_tag")" ] && echo "$release_tag is free" || echo "$release_tag exists: set a newer version"
   ```
2. Run the tests: `.venv/bin/python -m unittest discover -s tests -p '*_tests.py'`. Wait for the checks GitHub started on the push to pass too (https://github.com/bw2/agent-command-tab/actions): they run the tests on every supported Python version, and build the package and install it.
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
6. Tag the release, with the tag named in step 1: `git tag "$release_tag" && git push origin "$release_tag"`.

There is no Homebrew formula: pipx installs the app from PyPI (README, Install).

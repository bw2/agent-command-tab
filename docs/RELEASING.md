# Releasing

How to publish a version to PyPI and to the Homebrew tap. Nothing has been published yet.

## Before the first release

- Make the GitHub repository public (the Homebrew formula's homepage and the README's links point to it).
- Create a PyPI account with two-factor authentication, and an API token for the project (or set up a trusted publisher from GitHub Actions).
- Create the tap repository `bw2/homebrew-tap` on GitHub, with a `Formula/` folder.

## Each release

1. Set the version in `pyproject.toml` (for example `0.1.0`) and commit.
2. Run the tests: `.venv/bin/python -m unittest discover -s tests -p '*_tests.py'`.
3. Build the source archive and the wheel into a clean `dist/`:
   ```bash
   rm -rf dist && python3 -m build
   ```
   (`python3 -m pip install build twine` once, if needed.)
4. Check them: `python3 -m twine check dist/*`. Optionally install the wheel into a fresh virtual environment and run `llm-sidebar-dashboard --help`.
5. Upload to TestPyPI first, then to PyPI:
   ```bash
   python3 -m twine upload --repository testpypi dist/*
   python3 -m twine upload dist/*
   ```
6. Tag the release: `git tag v0.1.0 && git push origin v0.1.0`.
7. Update the Homebrew formula, `packaging/homebrew/llm-sidebar-dashboard.rb`:
   - `url`: the published source archive (`https://files.pythonhosted.org/packages/source/l/llm-sidebar-dashboard/llm_sidebar_dashboard-<version>.tar.gz`);
   - `sha256`: `curl -sL <that url> | shasum -a 256`;
   - the dependencies' `resource` blocks, after a dependency version changes: `brew update-python-resources --print-only llm-sidebar-dashboard`.
8. Copy the formula into the tap's `Formula/` folder, then test it:
   ```bash
   brew install --build-from-source bw2/tap/llm-sidebar-dashboard
   brew test llm-sidebar-dashboard
   brew audit --strict --online llm-sidebar-dashboard
   ```
   Building pyobjc from source takes a few minutes and needs the Xcode command line tools.
9. Push the tap repository.

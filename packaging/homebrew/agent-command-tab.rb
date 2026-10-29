# Homebrew formula for a personal tap (github.com/bw2/homebrew-tap), not homebrew-core.
# Not published yet: docs/RELEASING.md lists the steps, which fill in the release's url and
# sha256 below. Then: brew install bw2/tap/agent-command-tab
class AgentCommandTab < Formula
  include Language::Python::Virtualenv

  desc "Menu bar list, window or sidebar of the iTerm2 tabs where AI coding agents run"
  homepage "https://github.com/bw2/agent-command-tab"
  # The release's source archive on PyPI (filled in at release time; see docs/RELEASING.md).
  url "https://files.pythonhosted.org/packages/source/a/agent-command-tab/agent_command_tab-0.1.0.tar.gz"
  sha256 "REPLACE_WITH_THE_SHA256_OF_THE_PUBLISHED_SDIST"
  license "MIT"

  depends_on :macos
  depends_on "python@3.13"

  # The Python packages it needs, built from their source archives. Regenerate after a version
  # change with: brew update-python-resources agent-command-tab
  resource "iterm2" do
    url "https://files.pythonhosted.org/packages/36/a8/e88133c8b7418bd0905859a2f9dd0f5388c5a394434790e46e54cf15678c/iterm2-2.30.tar.gz"
    sha256 "eaa814b64f11d58984dfa02e790016983e7c4eb4f41cceae174ba361708200ec"
  end

  resource "protobuf" do
    url "https://files.pythonhosted.org/packages/d9/89/5b8517baa72f84a67b8a307ba953c91057af618bf40bf676f3c03551f8f0/protobuf-7.36.2.tar.gz"
    sha256 "497d0463ff3316681da6c0b9e8d06cb465d61abce00b613ab42226175644d1bb"
  end

  resource "websockets" do
    url "https://files.pythonhosted.org/packages/30/4b/4692be6d5bf739ed049b3813901c42091f8c054df89d5c089343823dea0d/websockets-17.3.tar.gz"
    sha256 "1c7dd93a1c9f89297471493674b1fc1d9467cafd45374f2172213046bdb3a329"
  end

  resource "pyobjc-core" do
    url "https://files.pythonhosted.org/packages/a5/78/abc4ce5920305780aeb36b4067a86253378b36e29ba96673a3deb02eb03a/pyobjc_core-12.2.2.tar.gz"
    sha256 "3906452339cd06a3bb07df103c2511d4cb0f7a22d8771c0b802eba15d9a642b6"
  end

  resource "pyobjc-framework-Cocoa" do
    url "https://files.pythonhosted.org/packages/75/76/49c6da2c6a831020b4854ba20079d5a1030474bffc776b7b73c2eeff8c15/pyobjc_framework_cocoa-12.2.2.tar.gz"
    sha256 "c96c0ef69a71afbbb0e6a7d594b455c5fe47d62e0db376ee7a2b4b828c16ace9"
  end

  def install
    virtualenv_install_with_resources
  end

  def caveats
    <<~EOS
      AgentCommandTab reads iTerm2 through iTerm2's Python API. Turn it on in
      iTerm2 > Settings > General > Magic > Enable Python API, then start the app:
        agent-command-tab
      From then on iTerm2 starts it whenever iTerm2 starts, through a script in
      iTerm2's AutoLaunch folder (Settings > Start with iTerm2 turns that off).

      That script and the hooks it adds to Claude Code's and Codex's settings stay
      after an uninstall. To take them out first, turn off Start with iTerm2, then:
        agent-command-tab remove-hooks
    EOS
  end

  test do
    assert_equal "agent-command-tab #{version}", shell_output("#{bin}/agent-command-tab --version").strip
  end
end

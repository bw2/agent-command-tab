# Homebrew cask for a personal tap (github.com/bw2/homebrew-tap), not homebrew-cask: the signed and
# notarized AgentCommandTab.app from the GitHub release's disk image. The formula
# agent-command-tab installs the same app as a command run by Homebrew's Python instead; this name
# keeps the two apart, as Homebrew's own wireshark-app cask does.
# Not published yet: docs/RELEASING.md lists the steps, which fill in the version, sha256 and the
# macOS and processor requirements below. Then: brew install --cask bw2/tap/agent-command-tab-app
cask "agent-command-tab-app" do
  version "0.1.0"
  sha256 "REPLACE_WITH_THE_SHA256_OF_THE_RELEASED_DISK_IMAGE"

  url "https://github.com/bw2/agent-command-tab/releases/download/v#{version}/AgentCommandTab-#{version}.dmg"
  name "AgentCommandTab"
  desc "Menu bar list, window or sidebar of the iTerm2 tabs where AI coding agents run"
  homepage "https://github.com/bw2/agent-command-tab"

  # For a build with Homebrew's Python; a build with python.org's universal2 Python drops the
  # processor requirement and lowers the macOS one (docs/RELEASING.md).
  depends_on arch: :arm64
  depends_on macos: ">= :sequoia"

  app "AgentCommandTab.app"

  zap trash: "~/Library/Application Support/agent-command-tab"

  caveats <<~EOS
    AgentCommandTab reads iTerm2 through iTerm2's Python API. Turn it on in
    iTerm2 > Settings > General > Magic > Enable Python API.

    The hooks it adds to Claude Code's and Codex's settings stay after an uninstall.
    To take them out first:
      #{appdir}/AgentCommandTab.app/Contents/MacOS/AgentCommandTab remove-hooks
  EOS
end

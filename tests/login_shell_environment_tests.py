import os
import tempfile
import unittest
import unittest.mock

from agent_command_tab.login_shell_environment import (ENVIRONMENT_OUTPUT_MARKER,
                                                       add_the_login_shells_environment_when_started_without_it,
                                                       build_environment_with_the_login_shells,
                                                       environment_lacks_the_login_shells, parse_environment_between_markers,
                                                       read_login_shell_environment)

BARE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"  # what iTerm2's AutoLaunch script gives the app


class LoginShellEnvironmentTests(unittest.TestCase):
    def test_only_a_path_without_a_shells_folders_lacks_the_login_shells(self):
        self.assertTrue(environment_lacks_the_login_shells({"PATH": BARE_PATH}))
        self.assertTrue(environment_lacks_the_login_shells({"PATH": "/usr/bin:/bin"}))
        self.assertTrue(environment_lacks_the_login_shells({}))
        self.assertFalse(environment_lacks_the_login_shells({"PATH": "/usr/local/bin:" + BARE_PATH}))

    def test_what_start_up_files_print_around_the_environment_is_left_out(self):
        marker = ENVIRONMENT_OUTPUT_MARKER.encode()
        shell_output = (b"Welcome!\n\x1b]1337;ShellIntegrationVersion=1\x07" + marker
                        + b"PATH=/opt/homebrew/bin:/usr/bin\0EQUATION=a=b\0NOTE=caf\xc3\xa9\0" + marker + b"bye\n")
        self.assertEqual(parse_environment_between_markers(shell_output),
                         {"PATH": "/opt/homebrew/bin:/usr/bin", "EQUATION": "a=b", "NOTE": "café"})
        self.assertIsNone(parse_environment_between_markers(b"no markers: the shell exited early"))
        self.assertIsNone(parse_environment_between_markers(marker + b"PATH=/usr/bin\0"))  # cut off

    def test_the_login_shells_path_comes_first_and_the_apps_own_variables_are_kept(self):
        environment = {"PATH": BARE_PATH, "HOME": "/Users/me", "CLAUDE_CONFIG_DIR": "/Users/me/.claude_app"}
        login_shell_environment = {"PATH": "/Users/me/.nvm/versions/node/v22.1.0/bin:/opt/homebrew/bin:/usr/bin:/bin",
                                   "CODEX_HOME": "/Users/me/.config/codex", "CLAUDE_CONFIG_DIR": "/Users/me/.claude_shell",
                                   "TMUX_TMPDIR": "", "EDITOR": "vim"}
        self.assertEqual(build_environment_with_the_login_shells(environment, login_shell_environment), {
            "PATH": "/Users/me/.nvm/versions/node/v22.1.0/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": "/Users/me", "CLAUDE_CONFIG_DIR": "/Users/me/.claude_app", "CODEX_HOME": "/Users/me/.config/codex"})
        self.assertEqual(environment["PATH"], BARE_PATH)  # its input unchanged

    def test_the_login_shell_is_asked_only_when_the_app_was_started_without_its_environment(self):
        login_shell_environment = {"PATH": "/opt/homebrew/bin:" + BARE_PATH, "TMUX_TMPDIR": "/Users/me/tmp"}
        environment = {"PATH": BARE_PATH}
        add_the_login_shells_environment_when_started_without_it(environment, lambda: login_shell_environment)
        self.assertEqual(environment, login_shell_environment)
        started_from_a_shell = {"PATH": "/usr/local/bin:" + BARE_PATH}
        add_the_login_shells_environment_when_started_without_it(started_from_a_shell, unittest.mock.Mock(side_effect=AssertionError))
        self.assertEqual(started_from_a_shell, {"PATH": "/usr/local/bin:" + BARE_PATH})
        shell_that_could_not_be_read = {"PATH": BARE_PATH}
        add_the_login_shells_environment_when_started_without_it(shell_that_could_not_be_read, lambda: None)
        self.assertEqual(shell_that_could_not_be_read, {"PATH": BARE_PATH})

    def test_a_real_login_shell_reads_its_start_up_files(self):
        with tempfile.TemporaryDirectory() as home_folder:
            with open(os.path.join(home_folder, ".profile"), "w") as profile_file:
                profile_file.write('echo "noise from .profile"\nexport CODEX_HOME="$HOME/codex home"\n'
                                   'PATH="$HOME/bin:$PATH"; export PATH\n')
            with unittest.mock.patch.dict(os.environ, {"HOME": home_folder, "PATH": BARE_PATH, "ENV": ""}):
                login_shell_environment = read_login_shell_environment("/bin/sh")
        self.assertEqual(login_shell_environment["CODEX_HOME"], os.path.join(home_folder, "codex home"))
        self.assertTrue(login_shell_environment["PATH"].startswith(os.path.join(home_folder, "bin") + ":"))

    def test_a_shell_whose_syntax_differs_is_not_asked(self):
        with unittest.mock.patch("subprocess.run", side_effect=AssertionError("run")):
            self.assertIsNone(read_login_shell_environment("/bin/tcsh"))


if __name__ == "__main__":
    unittest.main()

"""Records a short conversation with each agent tool installed on this Mac (Claude Code, with the
payloads of the hooks the app installs for it; Codex; agy) in a throwaway folder, and saves the
files they wrote, with what could identify the person who ran it removed, to
tests/recorded_agent_files/<tool>/<version>/, where tests/recorded_agent_files_tests.py reads them.
An agent tool that isn't installed is left out, with a note.

Run it when an agent tool's new version may write its files differently: each version gets a
folder of its own, and a recording is never edited by hand afterwards (see
tests/recorded_agent_files/README.md). A version that has a recording already is left out as
well, since the tests pin what its recording holds (its replies, times and token counts) and
another conversation would hold other ones; --replace records it again all the same. It uses
each agent tool's own sign-in and costs a few cents of its usage. A recording is not saved when
something personal is still found in one of its files (find_personal_texts_left): the script
then goes on to the other agent tools and ends with exit status 1, so check what it names before
running it again.

Usage:
    python3 dev/record_agent_conversations.py [--replace] [claude_code] [codex] [agy]
"""

import argparse
import datetime
import getpass
import glob
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile

REPO_FOLDER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_FOLDER, "src"))

from agent_command_tab.agent_adapters.agy import AGY_WHOLE_TRANSCRIPT_FILE_NAME, build_agy_transcript_path  # noqa: E402
from agent_command_tab.agent_adapters.codex import find_codex_home_folder  # noqa: E402
from agent_command_tab.context_window_usage import find_codex_conversation_file_by_id  # noqa: E402
from agent_command_tab.hooks_installer import HOOK_EVENTS_BY_AGENT_TOOL_NAME  # noqa: E402

RECORDED_AGENT_FILES_FOLDER = os.path.join(REPO_FOLDER, "tests", "recorded_agent_files")
# The run's throwaway folder and home folder, as the saved files name them.
RUN_FOLDER_STAND_IN = "/Users/me/project"
HOME_FOLDER_STAND_IN = "/Users/me"
ZERO_UUID = "00000000-0000-0000-0000-000000000000"
REMOVED_TEXT = "(removed by dev/record_agent_conversations.py)"
# Each agent writes a file, runs a shell command and replies, then (except Codex, recorded with one
# turn) runs another command in a second turn (Claude Code: also one in the background, which its
# hooks report).
FIRST_PROMPT = ("Write a file named notes.txt containing the line: merge the VCF files. Then run the shell "
                "command cat notes.txt, and finish by replying with exactly: Saved the merge note.")
SECOND_PROMPT = "Run ls -1 with the shell and reply with the number of files listed."
SECOND_PROMPT_FOR_CLAUDE_CODE = ("Run sleep 3 as a background shell command (run_in_background), then run ls -1 in "
                                 "the foreground and reply with the number of files listed.")
# Values that hold instructions written for the model: the agent tool's own, and the person's
# (their CLAUDE.md, AGENTS.md, installed skills and tools); and the account's plan and how much of it is used.
KEYS_WHOSE_VALUES_ARE_REMOVED = frozenset({"base_instructions", "developer_instructions", "user_instructions", "agents_md",
                                           "rate_limits"})
TIME_ZONE_PATTERN = re.compile(r"<timezone>[^<]*</timezone>")  # in the context Codex tells the model about
LOCAL_TIME_WITH_UTC_OFFSET_PATTERN = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?([+-])(\d\d):(\d\d)")
CLAUDE_CODE_RECORDS_OF_A_REQUEST_TO_THE_MODEL = frozenset({"api-request", "api-request-blob", "api-request-shape"})
SECONDS_TO_WAIT_FOR_AN_AGENT = 300


def remove_values_of_keys(value, keys):
    """Returns value (decoded JSON) with the value of each of these keys, at any depth, replaced by REMOVED_TEXT."""
    if isinstance(value, dict):
        return {key: REMOVED_TEXT if key in keys else remove_values_of_keys(item, keys) for key, item in value.items()}
    if isinstance(value, list):
        return [remove_values_of_keys(item, keys) for item in value]
    return value


def scrub_claude_code_record(record):
    """Returns a Claude Code transcript record without the requests sent to the model (the
    person's instructions, email address, skills and tools), the contents of the context it
    attached (the same, and the output of the person's hooks), the account's IDs, or the
    commands of the hooks that ran."""
    kind = record.get("type")
    if kind in CLAUDE_CODE_RECORDS_OF_A_REQUEST_TO_THE_MODEL:
        return {"type": kind, "sessionId": record.get("sessionId"), "removed": REMOVED_TEXT}
    if kind == "attachment":
        return dict({key: value for key, value in record.items() if key != "attachment" and not key.startswith("rendered")},
                    attachment={"type": (record.get("attachment") or {}).get("type")})
    if kind == "session-owner":
        return dict(record, accountUuid=ZERO_UUID, organizationUuid=ZERO_UUID)
    if kind == "system" and isinstance(record.get("hookInfos"), list):
        return dict(record, hookInfos=[dict(hook_info, command=REMOVED_TEXT) for hook_info in record["hookInfos"]])
    return record


def scrub_codex_record(record):
    """Returns a Codex rollout record without the instructions Codex and the person gave the model
    (its system prompt, their AGENTS.md, skills and tools), the account's IDs, plan and usage, or
    the time zone."""
    record = remove_values_of_keys(record, KEYS_WHOSE_VALUES_ARE_REMOVED)
    kind, payload = record.get("type"), record.get("payload")
    if not isinstance(payload, dict):
        return record
    payload_type = payload.get("type")
    if kind == "session_meta":
        payload = dict(payload, creator_user_id="user-0", creator_account_id=ZERO_UUID)
    elif kind == "world_state":
        payload = dict(payload, state={})
    elif kind == "response_item" and payload_type == "additional_tools":
        payload = dict(payload, tools=[])
    elif kind == "response_item" and payload_type == "message" and payload.get("role") in ("developer", "user"):
        payload = dict(payload, content=[
            dict(item, text=REMOVED_TEXT)
            if payload["role"] == "developer" or str(item.get("text", "")).startswith("# AGENTS.md instructions")
            else dict(item, text=TIME_ZONE_PATTERN.sub("<timezone>UTC</timezone>", item["text"])) if "text" in item else item
            for item in payload.get("content", [])])
    return dict(record, payload=payload)


def replace_folder_paths(text, run_folder, home_folder):
    """Returns text with the run's throwaway folder (as given, as resolved, and as Claude Code
    names its project folder) and the home folder replaced by stand-ins."""
    for run_folder_path in sorted({run_folder, os.path.realpath(run_folder)}, key=len, reverse=True):
        text = text.replace(run_folder_path, RUN_FOLDER_STAND_IN)
        text = text.replace(re.sub(r"[^A-Za-z0-9]", "-", run_folder_path), re.sub(r"[^A-Za-z0-9]", "-", RUN_FOLDER_STAND_IN))
    return text.replace(home_folder, HOME_FOLDER_STAND_IN)


def write_local_times_in_utc(text):
    """Returns text with each local time that has a UTC offset ("2026-11-18T00:05:20-05:00", as agy
    writes them) written as the same moment in UTC ("2026-11-18T05:05:20Z"). The offset tells the
    time zone of the person who recorded it, and so would a local clock time beside the UTC
    times of the same records."""
    def write_in_utc(match):
        local_time, fraction_of_a_second, sign, offset_hours, offset_minutes = match.groups()
        offset = datetime.timedelta(hours=int(offset_hours), minutes=int(offset_minutes)) * (1 if sign == "+" else -1)
        utc_time = datetime.datetime.strptime(local_time, "%Y-%m-%dT%H:%M:%S") - offset
        return utc_time.strftime("%Y-%m-%dT%H:%M:%S") + (fraction_of_a_second or "") + "Z"
    return LOCAL_TIME_WITH_UTC_OFFSET_PATTERN.sub(write_in_utc, text)


def scrub_jsonl_text(text, scrub_record, run_folder, home_folder):
    """Returns the JSON lines text with each record scrubbed, the folders replaced and the local
    times written in UTC; a line that isn't JSON (one the agent was still writing) is left out."""
    scrubbed_lines = []
    # Not splitlines(), which also splits at the line separators (such as U+2028 and U+0085) that
    # Claude Code writes as they are inside a record's texts, leaving pieces that aren't JSON.
    for line in text.split("\n"):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        scrubbed_lines.append(json.dumps(scrub_record(record), ensure_ascii=False, separators=(",", ":")))
    return write_local_times_in_utc(replace_folder_paths("\n".join(scrubbed_lines) + "\n", run_folder, home_folder))


def list_personal_texts():
    """Returns the texts that would identify the person running this: their user name, the Mac's
    name, their git email address, and each line of 40 characters or more of their own instructions
    to agents (a shorter line, such as a heading, could be anyone's)."""
    personal_texts = {getpass.getuser(), socket.gethostname().split(".")[0]}
    git_email = subprocess.run(["git", "config", "user.email"], capture_output=True, text=True).stdout.strip()
    if git_email:
        personal_texts.add(git_email)
    for instructions_path in ("~/.claude/CLAUDE.md", "~/.codex/AGENTS.md", "~/.gemini/GEMINI.md"):
        try:
            with open(os.path.expanduser(instructions_path)) as instructions_file:
                personal_texts.update(line.strip() for line in instructions_file if len(line.strip()) >= 40)
        except OSError:
            pass
    return personal_texts


def find_personal_texts_left(text, personal_texts):
    """Returns the personal texts, the email addresses and the API keys found in a file's text. A
    personal text is looked for as it is and as a JSON text holds it (its quotes and backslashes
    escaped), since the files are JSON lines."""
    found = [personal_text for personal_text in personal_texts
             if personal_text in text or json.dumps(personal_text, ensure_ascii=False)[1:-1] in text]
    found += re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text) + re.findall(r"\bsk-[A-Za-z0-9_-]{16,}", text)
    return found


def run_agent(command, run_folder):
    """Runs an agent tool's command in the run folder; returns its standard output, raising on a failure."""
    result = subprocess.run(command, cwd=run_folder, capture_output=True, text=True, timeout=SECONDS_TO_WAIT_FOR_AN_AGENT,
                            stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        raise RuntimeError(f"{command[0]} failed ({result.returncode}): {result.stderr[-2000:]}")
    return result.stdout


def read_version(program_name):
    """Returns the version an agent tool prints first, such as "2.1.327"."""
    return re.search(r"\d+\.\d+\.\d+", run_agent([program_name, "--version"], os.getcwd())).group(0)


def build_command_that_saves_a_hook_payload(hook_payloads_path):
    """Returns the shell command a recorded Claude Code's hook runs: this script, which appends the
    payload the hook was given to the file. Each part is quoted, since any of the paths may hold a space."""
    return shlex.join([sys.executable, os.path.abspath(__file__), "--save-hook-payload", hook_payloads_path])


def record_claude_code(run_folder):
    """Returns {file name: (its text, the function that scrubs each of its records)} for a Claude
    Code conversation: its transcript and its hooks' payloads."""
    with tempfile.TemporaryDirectory() as hook_payloads_folder:  # outside the run folder, which the agent lists
        hook_payloads_path = os.path.join(hook_payloads_folder, "hook_payloads.jsonl")
        save_hook_payload = {"type": "command", "command": build_command_that_saves_a_hook_payload(hook_payloads_path)}
        settings = {"hooks": {event_name: [dict({"hooks": [save_hook_payload]}, **({"matcher": matcher} if matcher else {}))]
                              for event_name, matcher in HOOK_EVENTS_BY_AGENT_TOOL_NAME["Claude Code"]}}
        # Only the hooks above: the person's own settings (and their hooks) aren't loaded.
        command = ["claude", "-p", "--model", "haiku", "--setting-sources", "project", "--strict-mcp-config",
                   "--dangerously-skip-permissions", "--settings", json.dumps(settings)]
        run_agent(command + [FIRST_PROMPT], run_folder)
        run_agent(command + ["--continue", SECOND_PROMPT_FOR_CLAUDE_CODE], run_folder)
        with open(hook_payloads_path) as hook_payloads_file:
            hook_payloads_text = hook_payloads_file.read()
    transcript_path = json.loads(hook_payloads_text.split("\n")[0])["transcript_path"]
    with open(transcript_path) as transcript_file:
        return {"transcript.jsonl": (transcript_file.read(), scrub_claude_code_record),
                "hook_payloads.jsonl": (hook_payloads_text, lambda record: record)}


def record_codex(run_folder):
    """Returns {file name: (its text, the function that scrubs each of its records)} for a Codex
    conversation of one turn: its rollout file."""
    output = run_agent(["codex", "exec", "--json", "--ignore-user-config", "--skip-git-repo-check", "-s", "workspace-write",
                        "-c", "model_reasoning_effort=low", FIRST_PROMPT], run_folder)
    thread_id = next(event["thread_id"] for event in map(json.loads, output.split("\n")) if event.get("thread_id"))
    rollout_path = find_codex_conversation_file_by_id(os.path.join(find_codex_home_folder(), "sessions"), thread_id)
    if rollout_path is None:
        raise RuntimeError(f"found no Codex conversation file for {thread_id}")
    with open(rollout_path) as rollout_file:
        return {"rollout.jsonl": (rollout_file.read(), scrub_codex_record)}


def list_agy_conversation_ids(agy_folder):
    """Returns the IDs of the conversations agy keeps a folder for."""
    return {os.path.basename(path) for path in glob.glob(os.path.join(glob.escape(agy_folder), "brain", "*"))}


def find_the_new_agy_conversation_that_holds_the_prompt(agy_folder, conversation_ids_from_before, prompt):
    """Returns the ID of the one agy conversation that isn't among those from before the recording
    began and whose transcript holds the prompt: the recording's own, whichever conversations the
    person's agy tabs started or wrote to meanwhile.

    Raises:
        ValueError: There is no such conversation, or more than one.
    """
    conversation_ids = []
    for conversation_id in sorted(list_agy_conversation_ids(agy_folder) - conversation_ids_from_before):
        try:
            with open(build_agy_transcript_path(agy_folder, conversation_id)) as transcript_file:
                if prompt in transcript_file.read():
                    conversation_ids.append(conversation_id)
        except OSError:  # a conversation without a message yet has no transcript
            pass
    if len(conversation_ids) != 1:
        raise ValueError(f"expected one new agy conversation that holds the prompt, found {conversation_ids}")
    return conversation_ids[0]


def record_agy(run_folder):
    """Returns {file name: (its text, the function that scrubs each of its records)} for an agy
    conversation: its transcript.jsonl, and the whole transcript agy keeps beside it
    (AGY_WHOLE_TRANSCRIPT_FILE_NAME, the one the app reads when there is one) if it wrote one."""
    agy_folder = os.path.expanduser("~/.gemini/antigravity-cli")
    conversation_ids_from_before = list_agy_conversation_ids(agy_folder)
    command = ["agy", "--dangerously-skip-permissions", "--effort", "low"]
    run_agent(command + ["-p", FIRST_PROMPT], run_folder)
    conversation_id = find_the_new_agy_conversation_that_holds_the_prompt(agy_folder, conversation_ids_from_before, FIRST_PROMPT)
    # Continued and read by its ID, not as the most recent one, which another agy tab's may be by now.
    run_agent(command + ["--conversation", conversation_id, "-p", SECOND_PROMPT], run_folder)
    return read_agy_transcripts(os.path.dirname(build_agy_transcript_path(agy_folder, conversation_id)))


def read_agy_transcripts(logs_folder):
    """Returns {file name: (its text, the function that scrubs each of its records)} for the
    transcripts in an agy conversation's logs folder: transcript.jsonl, and the whole transcript
    when agy wrote one. agy's records hold no instructions or account to remove."""
    recorded_files = {}
    for file_name in ("transcript.jsonl", AGY_WHOLE_TRANSCRIPT_FILE_NAME):
        transcript_path = os.path.join(logs_folder, file_name)
        if os.path.isfile(transcript_path):
            with open(transcript_path) as transcript_file:
                recorded_files[file_name] = (transcript_file.read(), lambda record: record)
    return recorded_files


RECORDERS_BY_FOLDER_NAME = {"claude_code": ("claude", record_claude_code), "codex": ("codex", record_codex),
                            "agy": ("agy", record_agy)}


def find_folder_for_a_new_recording(folder_name, replace_the_recording_of_this_version):
    """Returns (the folder that a recording of the agent tool's installed version goes to, None),
    or (None, why none is made): the agent tool isn't installed, or its version has a recording
    already (its folder holds a recorded file, which it does only once a whole recording is
    saved: see record_and_save). The tests pin what that recording holds, and another
    conversation would hold other replies, times and token counts, so it is replaced only when
    asked for.

    Args:
        folder_name: The agent tool's folder in RECORDED_AGENT_FILES_FOLDER, such as "claude_code".
        replace_the_recording_of_this_version: Whether a version that has a recording is recorded again.
    """
    program_name, _ = RECORDERS_BY_FOLDER_NAME[folder_name]
    if shutil.which(program_name) is None:
        return None, f"{program_name} isn't installed"
    output_folder = os.path.join(RECORDED_AGENT_FILES_FOLDER, folder_name, read_version(program_name))
    if glob.glob(os.path.join(glob.escape(output_folder), "*.jsonl")) and not replace_the_recording_of_this_version:
        return None, f"{output_folder} holds a recording of this version already (--replace records it again)"
    return output_folder, None


def record_and_save(folder_name, output_folder):
    """Records one agent tool's conversation and saves its scrubbed files as output_folder, in place
    of the recording that folder held, if any; returns whether it did, which it doesn't when
    something personal was left in them (which it prints)."""
    _, record = RECORDERS_BY_FOLDER_NAME[folder_name]
    home_folder = os.path.expanduser("~")
    with tempfile.TemporaryDirectory(prefix="agent-command-tab-recording.") as run_folder:
        recorded_files = record(run_folder)
        scrubbed_texts = {file_name: scrub_jsonl_text(text, scrub_record, run_folder, home_folder)
                          for file_name, (text, scrub_record) in recorded_files.items()}
    personal_texts = list_personal_texts()
    texts_left = {file_name: find_personal_texts_left(text, personal_texts) for file_name, text in scrubbed_texts.items()}
    if any(texts_left.values()):
        print(f"{folder_name}: not saved, since these are still in it: {texts_left}")
        return False
    # The files are written to a folder of their own, which is then renamed to output_folder: a save
    # cut short leaves no folder there for find_folder_for_a_new_recording to take for a recording.
    os.makedirs(os.path.dirname(output_folder), exist_ok=True)
    with tempfile.TemporaryDirectory(dir=os.path.dirname(output_folder)) as folder_to_write_in:
        new_recording_folder = os.path.join(folder_to_write_in, "new")
        os.mkdir(new_recording_folder)
        for file_name, text in scrubbed_texts.items():
            with open(os.path.join(new_recording_folder, file_name), "w") as output_file:
                output_file.write(text)
        if os.path.isdir(output_folder):  # the recording that --replace records again: moved out whole, removed with folder_to_write_in
            os.rename(output_folder, os.path.join(folder_to_write_in, "replaced"))
        os.rename(new_recording_folder, output_folder)
    return True


def main():
    """Records the agent tools named on the command line (or, for a recorded Claude Code's hook,
    saves the hook's payload). Returns the exit status: 1 when a recording wasn't saved because
    something personal was left in it, otherwise 0 (also when an agent tool is left out because
    it isn't installed or has a recording already). A name that isn't an agent tool's ends the
    run with a usage error before any agent tool is recorded."""
    if sys.argv[1:2] == ["--save-hook-payload"]:  # run by the recorded Claude Code's hooks
        with open(sys.argv[2], "a") as hook_payloads_file:
            hook_payloads_file.write(json.dumps(json.load(sys.stdin), ensure_ascii=False, separators=(",", ":")) + "\n")
        return 0
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--replace", action="store_true",
                        help="record a version again that has a recording already, replacing its files")
    parser.add_argument("folder_names", nargs="*", metavar="agent_tool",
                        help=f"the agent tools to record ({', '.join(RECORDERS_BY_FOLDER_NAME)}); all of them when none is named")
    arguments = parser.parse_args()
    # Not the parser's `choices`: Python 3.11 and earlier check them for a run that names no agent tool too, and reject it.
    unknown_names = [folder_name for folder_name in arguments.folder_names if folder_name not in RECORDERS_BY_FOLDER_NAME]
    if unknown_names:
        parser.error(f"unknown agent tool: {', '.join(unknown_names)} (choose from {', '.join(RECORDERS_BY_FOLDER_NAME)})")
    exit_status = 0
    for folder_name in arguments.folder_names or list(RECORDERS_BY_FOLDER_NAME):
        output_folder, reason_not_to_record = find_folder_for_a_new_recording(folder_name, arguments.replace)
        if output_folder is None:
            print(f"{folder_name}: not recorded, since {reason_not_to_record}")
        elif record_and_save(folder_name, output_folder):
            print(f"{folder_name}: saved to {output_folder}")
        else:
            exit_status = 1  # reported once the other agent tools are recorded too
    return exit_status


if __name__ == "__main__":
    sys.exit(main())

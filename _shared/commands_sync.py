import json, os, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

catalog_path = sys.argv[1]
claude_dir   = sys.argv[2]
mode         = sys.argv[3]
target       = sys.argv[4]

with open(catalog_path, encoding="utf-8") as catalog_fh:
    data = json.load(catalog_fh)

expected = {}
for skill in data.get("skills", []):
    expected[skill["name"]] = skill["path"]
expected.setdefault("visibility", "_shared/agent_visibility_cli.py")

ok_c   = "\033[0;32m[OK]\033[0m"
warn_c = "\033[0;33m[WARN]\033[0m"
info_c = "\033[0;36m[INFO]\033[0m"

exit_code = 0

def sync_platform(commands_dir, ext, make_content, label):
    """Generic sync logic for one platform."""
    global exit_code
    os.makedirs(commands_dir, exist_ok=True)

    existing = set()
    for entry in os.listdir(commands_dir):
        if entry.endswith(ext):
            existing.add(entry[:-len(ext)])

    missing = set(expected.keys()) - existing
    orphan  = existing - set(expected.keys())
    drift   = []

    for name in sorted(existing & set(expected.keys())):
        cmd_file = os.path.join(commands_dir, f"{name}{ext}")
        exp = make_content(name, expected[name])
        with open(cmd_file, encoding="utf-8") as cf:
            actual = cf.read()
        if actual.rstrip("\n") != exp.rstrip("\n"):
            drift.append(name)

    if not missing and not orphan and not drift:
        print(f"  {ok_c} {len(expected)} {label} commands are in sync")
        return

    if missing:
        print(f"  {warn_c} {label} missing: {', '.join(sorted(missing))}")
    if orphan:
        print(f"  {warn_c} {label} orphan: {', '.join(sorted(orphan))}")
    if drift:
        print(f"  {warn_c} {label} drift: {', '.join(sorted(drift))}")

    if mode == "check":
        if missing or drift:
            print(f"  {info_c} Run 'bash install.sh --sync-commands' to fix")
            exit_code = 1
        return

    created = fixed = 0
    for name in missing:
        content = make_content(name, expected[name])
        with open(os.path.join(commands_dir, f"{name}{ext}"), "w", encoding="utf-8") as wf:
            wf.write(content)
        created += 1
    for name in drift:
        content = make_content(name, expected[name])
        with open(os.path.join(commands_dir, f"{name}{ext}"), "w", encoding="utf-8") as wf:
            wf.write(content)
        fixed += 1

    parts = []
    if created: parts.append(f"{created} created")
    if fixed:   parts.append(f"{fixed} fixed")
    if orphan:  parts.append(f"{len(orphan)} orphan(s) kept")
    print(f"  {ok_c} {label} synced: {', '.join(parts)}")


def claude_content(name, path):
    if name == "visibility":
        return "\n".join([
            "Change the Ghost-ALICE agent visibility profile for the current runtime.",
            "",
            "Run the local CLI with the provided argument:",
            "",
            "```bash",
            "python3 _shared/agent_visibility_cli.py $ARGUMENTS",
            "```",
            "",
            "If no argument is provided, run:",
            "",
            "```bash",
            "python3 _shared/agent_visibility_cli.py show",
            "```",
            "",
            "Allowed profiles: `strict`, `dynamic`, `minimal`.",
            "",
        ])
    return f"@{path}\n\n$ARGUMENTS\n"

if target in ("all", "claude"):
    print(f"  {info_c} Claude Code .claude/commands/")
    sync_platform(claude_dir, ".md", claude_content, ".claude/commands/")

if target in ("all", "codex"):
    print(f"  {ok_c} Codex commands are not needed because skills are directly discoverable")

sys.exit(exit_code)

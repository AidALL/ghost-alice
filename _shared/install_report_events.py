import json


def read_event_counts(event_file):
    total = current = updated = new = 0
    with open(event_file, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("type") != "platform-result":
                continue
            total += int(event.get("total_targets", 0))
            current += int(event.get("current", 0))
            updated += int(event.get("updated", 0))
            new += int(event.get("new", 0))
    print(f"{total}|{current}|{updated}|{new}")


def read_platform_target_progress(event_file, platform):
    targets = set()
    with open(event_file, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("type") != "target-result" or event.get("platform") != platform:
                continue
            target_id = event.get("target_id")
            target_kind = event.get("target_kind")
            if target_id and target_kind:
                targets.add((target_kind, target_id))
    print(len(targets))


def read_all_common_target_progress(event_file, platform_count):
    platform_count = int(platform_count)
    targets = {}
    with open(event_file, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("type") != "target-result":
                continue
            platform = event.get("platform")
            target_id = event.get("target_id")
            target_kind = event.get("target_kind")
            if platform and target_id and target_kind:
                targets.setdefault((target_kind, target_id), set()).add(platform)
    print(sum(1 for platforms in targets.values() if len(platforms) >= platform_count))


def read_weighted_common_target_progress(event_file, platform_count, total_count):
    platform_count = int(platform_count)
    total_count = int(total_count)
    pairs = set()
    with open(event_file, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("type") != "target-result":
                continue
            platform = event.get("platform")
            target_id = event.get("target_id")
            target_kind = event.get("target_kind")
            if platform and target_id and target_kind:
                pairs.add((platform, target_kind, target_id))
    if platform_count <= 0:
        print(0)
        return
    print(min(total_count, len(pairs) // platform_count))


def read_target_operation_progress(event_file):
    operations = set()
    with open(event_file, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("type") != "target-result":
                continue
            platform = event.get("platform")
            target_id = event.get("target_id")
            target_kind = event.get("target_kind")
            if platform and target_id and target_kind:
                operations.add((platform, target_kind, target_id))
    print(len(operations))


if __name__ == "__main__":
    import sys
    mode = sys.argv[1]
    if mode == "event-counts":
        read_event_counts(sys.argv[2])
    elif mode == "platform-target-progress":
        read_platform_target_progress(sys.argv[2], sys.argv[3])
    elif mode == "all-common-target-progress":
        read_all_common_target_progress(sys.argv[2], sys.argv[3])
    elif mode == "weighted-common-target-progress":
        read_weighted_common_target_progress(sys.argv[2], sys.argv[3], sys.argv[4])
    elif mode == "target-operation-progress":
        read_target_operation_progress(sys.argv[2])
    else:
        raise SystemExit(f"unknown mode: {mode}")

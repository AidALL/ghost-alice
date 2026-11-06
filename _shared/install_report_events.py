import json


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

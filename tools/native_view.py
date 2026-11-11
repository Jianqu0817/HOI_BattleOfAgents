"""Set the recording HUD and province camera through the native main-thread API."""
import argparse
import json
from pathlib import Path

from native_game import NativeGame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--request-id", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    hud = commands.add_parser("hud")
    hud.add_argument("expected_hidden", choices=("0", "1"))
    hud.add_argument("desired_hidden", choices=("0", "1"))
    lock = commands.add_parser("lock_camera")
    lock.add_argument("expected", choices=("0", "1"))
    lock.add_argument("desired", choices=("0", "1"))
    center = commands.add_parser("center_province")
    center.add_argument("province", type=int)
    frame = commands.add_parser("frame_province")
    frame.add_argument("province", type=int)
    frame.add_argument("height", type=int)
    args = parser.parse_args()
    payload = args.command
    if args.command == "hud":
        payload += f" {args.expected_hidden} {args.desired_hidden}"
    elif args.command == "lock_camera":
        payload += f" {args.expected} {args.desired}"
    elif args.command == "center_province":
        payload += f" {args.province}"
    elif args.command == "frame_province":
        payload += f" {args.province} {args.height}"
    game = NativeGame(args.pid, args.journal)
    print(json.dumps(game.call("native_view_v3", payload, request_id=args.request_id), ensure_ascii=False))


if __name__ == "__main__":
    main()

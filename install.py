#!/usr/bin/env python3
"""
Hermes QQ Channel Installer

Copies QQ channel adapter files into an existing Hermes Agent installation.
Safe to run multiple times — only copies missing/outdated files.

Usage:
    python install.py [--hermes-dir PATH]

Options:
    --hermes-dir PATH  Path to Hermes Agent root (default: ~/.hermes/hermes-agent)
"""

import argparse
import os
import shutil
import sys
from pathlib import Path


def get_hermes_dir(arg_path: str | None) -> Path:
    if arg_path:
        return Path(arg_path).expanduser().resolve()
    return Path.home() / ".hermes" / "hermes-agent"


def main():
    parser = argparse.ArgumentParser(description="Install Hermes QQ Channel adapter")
    parser.add_argument(
        "--hermes-dir",
        help="Path to Hermes Agent root (default: ~/.hermes/hermes-agent)",
    )
    args = parser.parse_args()

    hermes_dir = get_hermes_dir(args.hermes_dir)
    qq_dest = hermes_dir / "gateway" / "platforms" / "qq"

    # Resolve source directory (this script is at qq-channel/install.py)
    source_dir = Path(__file__).parent.resolve()
    qq_source = source_dir / "src" / "qq_channel"

    if not qq_source.exists():
        print(f"ERROR: Source QQ channel not found at {qq_source}")
        print("Make sure you are running install.py from the hermes-qq-channel directory.")
        sys.exit(1)

    if not hermes_dir.exists():
        print(f"ERROR: Hermes Agent not found at {hermes_dir}")
        print("Please install Hermes Agent first, or specify --hermes-dir")
        sys.exit(1)

    # Also register Platform.QQ in gateway/config.py
    config_file = hermes_dir / "gateway" / "config.py"
    if not config_file.exists():
        print(f"WARNING: {config_file} not found — skipping Platform.QQ enum registration")
        register_qq = False
    else:
        register_qq = True

    print(f"Installing Hermes QQ Channel to {qq_dest}")
    print(f"Source: {qq_source}")

    # Copy files
    qq_dest.mkdir(parents=True, exist_ok=True)
    files_copied = 0
    for src_file in qq_source.glob("*.py"):
        dst_file = qq_dest / src_file.name
        shutil.copy2(src_file, dst_file)
        files_copied += 1
        print(f"  copied: {src_file.name}")

    print(f"\n{files_copied} files copied.")

    # Register Platform.QQ if not already registered
    if register_qq:
        _register_platform_qq(config_file)

    print("\n✓ Hermes QQ Channel installed successfully!")
    print(f"\nNext steps:")
    print(f"  1. Add to ~/.hermes/config.yaml:")
    print(f"       platforms:")
    print(f"         qq:")
    print(f"           enabled: true")
    print(f"           extra:")
    print(f"             app_id: 'YOUR_APP_ID'")
    print(f"             client_secret: 'YOUR_APP_SECRET'")
    print(f"  2. Set environment variables (recommended):")
    print(f"       export QQ_BOT_APP_ID='YOUR_APP_ID'")
    print(f"       export QQ_BOT_CLIENT_SECRET='YOUR_APP_SECRET'")
    print(f"  3. Restart Hermes Agent: hermes run")


def _register_platform_qq(config_file: Path) -> None:
    """Add QQ to the Platform enum and get_connected_platforms if not already present."""
    content = config_file.read_text(encoding="utf-8")

    # 1. Add QQ to Platform enum
    if '    QQ = "qq"' not in content:
        # Find a good insertion point before API_SERVER
        if '    API_SERVER = "api_server"' in content:
            content = content.replace(
                '    API_SERVER = "api_server"',
                '    QQ = "qq"\n    API_SERVER = "api_server"',
            )
            print("  registered Platform.QQ in config.py")
        else:
            print("  WARNING: Could not find Platform enum insertion point — manual registration needed")
            return
    else:
        print("  Platform.QQ already registered")

    # 2. Add QQ to get_connected_platforms
    if "config.extra.get(\"app_id\")" not in content:
        # Find insertion point after bluebubbles check
        marker = '            # BlueBubbles uses extra dict for local server config\n        elif platform == Platform.BLUEBUBBLES and config.extra.get("server_url") and config.extra.get("password"):\n            connected.append(platform)'
        insert = (
            '            # QQ uses extra dict for app credentials\n'
            '            elif platform == Platform.QQ and config.extra.get("app_id"):\n'
            '                connected.append(platform)\n'
        )
        if marker in content:
            content = content.replace(marker, marker + "\n" + insert)
            print("  registered QQ in get_connected_platforms()")
        else:
            print("  WARNING: Could not find get_connected_platforms insertion point — manual registration needed")
    else:
        print("  QQ already in get_connected_platforms()")

    config_file.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()

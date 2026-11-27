#!/bin/bash
# Claude Code бұлт ортасын баптау скрипті
apt-get update -qq && apt-get install -y -qq ffmpeg >/dev/null || true
python3 .claude/skills/montazh-kz/scripts/montazh.py setup || true
python3 .claude/skills/montazh-pro/scripts/pro.py setup || true
python3 -c "import sys; sys.path.insert(0, '.claude/skills/matin-video/scripts'); import matin; matin.piper_paths()" || true

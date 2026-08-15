# FPL Intelligence System — Autonomous Controller Starter
# Follows: spec/AUTONOMOUS_BUILD_SPEC.md, AGENTS.md, planning/BUILD_PLAN.md

import subprocess
import sys
import os
from pathlib import Path
from typing import Optional, Dict

class AutonomousControllerStarter:
    def __init__(self, controller_script: str = "scripts/autonomous_build.py"):
        self.controller_script = Path(controller_script)

    def start_autonomous_controller(self, dry_run: bool = False) -> Dict:
        """Start the autonomous controller process."""
        if not self.controller_script.exists():
            raise FileNotFoundError(f"Controller script not found: {self.controller_script}")

        # Prepare the command
        command = [
            "uv",
            "run",
            "python",
            str(self.controller_script)
        ]

        if dry_run:
            command.append("--dry-run")

        # Start the controller process
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        return {
            'pid': process.pid,
            'command': ' '.join(command),
            'status': 'started',
            'dry_run': dry_run
        }

# Singleton instance for application use
autonomous_controller_starter = AutonomousControllerStarter()

# Convenience function for direct use
start_autonomous_controller = autonomous_controller_starter.start_autonomous_controller
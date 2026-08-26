from dataclasses import dataclass
from typing import Optional
import asyncio
from pathlib import Path
import sys

TASK_FOLDER = Path(__file__).parent.parent / "prototype" / "tasks"


@dataclass
class Runner:
    output_stream: str = ""
    process: Optional[asyncio.subprocess.Process] = None
    return_code: Optional[int] = None
    healthy: bool = True

    async def run(self, program_name: str, input_stream: str | list[str]) -> bool:

        program_path = TASK_FOLDER / program_name
        if not program_path.is_file():
            raise ValueError("Invalid program name given.")

        arguments = [input_stream] if isinstance(input_stream, str) else input_stream

        self.output_stream = ""
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(program_path),
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        output, error = await self.process.communicate()

        self.output_stream = output.decode()
        self.error_stream = error.decode()
        self.return_code = self.process.returncode

        return self.return_code == 0
    def get_health(self):
        return self.healthy

    def interrupt(self):
        if self.process is not None:
            self.process.terminate()
            

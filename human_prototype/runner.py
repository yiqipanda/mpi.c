from dataclasses import dataclass
from typing import Optional
import asyncio
from pathlib import Path
import sys

TASK_FOLDER = Path(__file__).parent.parent / "prototype" / "tasks"


@dataclass
class Runner:
    id: str = ""
    output_stream: str = ""
    error_stream: str = ""
    process: Optional[asyncio.subprocess.Process] = None
    return_code: Optional[int] = None
    healthy: bool = True

    async def run(self, program_name: str, input_stream: str | list[str]) -> bool:
        print(f"[INFO] runner: {self.id} running {program_name} with given input: {input_stream}")
        program_path = TASK_FOLDER / program_name

        if not program_path.is_file():
            self.error_stream = "Invalid program name given."
            return False

        arguments = (
            [input_stream]
            if isinstance(input_stream, str)
            else input_stream
        )

        self.output_stream = ""
        self.error_stream = ""
        self.return_code = None

        try:
            self.process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(program_path),
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            print("")
            output, error = await self.process.communicate()
        except OSError as error:
            print(f"[ERROR] runner: {self.id} ran into OS Error.")
            self.error_stream = str(error)
            return False
        except asyncio.CancelledError:
            self.interrupt()
            raise
        

        print(f"[INFO] runner: {self.id} finished running {program_name} with given output: {output.decode()}")
        self.output_stream = output.decode()
        self.error_stream = error.decode()
        self.return_code = self.process.returncode
        
        return self.return_code == 0

    def get_health(self) -> bool:
      
        return self.healthy

    def interrupt(self) -> None:
        if self.process is not None:
            print("[INFO] interrupting runner: {self.id}")
            self.process.terminate()

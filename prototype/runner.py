import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

TASK_FOLDER = Path(__file__).parent / "tasks"

#INFO: IN FUTURE ADD API CHANNELS FOR COMPUTER TALKS ETC. 

#Runner class talks with a computer
@dataclass
class Runner:
    id: str = ""
    output_stream: str = ""
    error_stream: str = ""
    process: asyncio.subprocess.Process | None = None
    return_code: int | None = None
    healthy: bool = True
    program_name: str = ""


    #Current version runs via subprocess
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
        self.program_name = program_name

        try:
            self.process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(program_path),
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            print()
            output, error = await self.process.communicate()
        except OSError as error:
            print(f"[ERROR] runner: {self.id} ran into OS Error.")
            self.error_stream = str(error)
            return False
        except asyncio.CancelledError:
            self.interrupt()
            if self.process is not None:
                await self.process.wait()
                self.return_code = self.process.returncode
            raise
        

        print(f"[INFO] runner: {self.id} finished running {program_name} with given output: {output.decode()}")
        self.output_stream = output.decode()
        self.error_stream = error.decode()
        self.return_code = self.process.returncode
        
        return self.return_code == 0

    def get_health(self) -> bool:
      
        return self.healthy

    def interrupt(self) -> None:
        if self.process is not None and self.process.returncode is None:
            print(f"[INFO] interrupting runner: {self.id}")
            self.process.terminate()

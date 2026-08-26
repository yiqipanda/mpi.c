from dataclasses import dataclass
from typing import Optional, Final
from task import Task
from runner import Runner
import asyncio

POLL_INTERVAL: Final[float] = 0.1
MAX_RETRIES: Final[int] = 3
@dataclass
class Worker:
    worker_id: Final[str] = ""
    assigned_program_name: str = ""
    assigned_task: Optional[Task] = None
    current_runner: Optional[Runner] = None
    available: bool = True
    reset_signal: bool = False

    def is_available(self):
        return self.available

    def reset(self):

        if self.current_runner is not None:
            self.current_runner.interrupt()

        self.assigned_program_name = ""
        self.assigned_task = None
        self.available = True
        
    def get_program_name(self):
        return self.assigned_program_name

    def get_runner(self):
        return Runner()

    def assign(self, program_name: str, task: Task) -> None:
        if not self.available or self.assigned_task is not None:
            raise RuntimeError(f"worker {self.worker_id} is not available")
        self.assigned_program_name = program_name
        self.assigned_task = task
        self.available = False

    async def run(self):
        
        if self.assigned_task is None:
            return

        self.available = False
        input_stream = self.assigned_task.get_input_stream()

        for _ in range(MAX_RETRIES):

            if self.assigned_task.get_intermediate_stream():
                break

            runner = Runner()
            self.current_runner = runner
            runner_monitor = asyncio.create_task(
                runner.run(
                    self.assigned_task.assigned_program_name,
                    input_stream,
                )
            )

            while not runner_monitor.done() and runner.healthy:

                await asyncio.sleep(POLL_INTERVAL)

                if self.reset_signal:
                    await asyncio.gather(
                        runner_monitor,
                        return_exceptions=True,
                    )
                    self.current_runner = None
                    self.reset_signal = False
                    return False

            if not runner.healthy:
                runner.interrupt()
                await asyncio.gather(
                    runner_monitor,
                    return_exceptions=True,
                )
                continue

            if await runner_monitor:
                self.assigned_task.set_main_output(runner.output_stream)
                break
        
        
        await self.assigned_task.update_intermediate_stream()

        # Leaf tasks do not need a second orchestration program. Their main
        # subprocess output is already the final task output.
        if not self.assigned_task.orchestration_program_name:
            self.assigned_task.set_output_stream(self.assigned_task.main_output)
            self.assigned_task.completed = True
            self.current_runner = None
            self.available = True
            return True

        for _ in range(MAX_RETRIES):
            if(len(self.assigned_task.get_output_stream()))>1:
                self.available = True
                self.assigned_task.completed = True
                return True
            
            runner = Runner()
            self.current_runner = runner
            runner_monitor = asyncio.create_task(
                runner.run(
                    self.assigned_task.orchestration_program_name,
                    self.assigned_task.get_intermediate_stream(),
                )
            )

            while not runner_monitor.done() and runner.healthy:
                await asyncio.sleep(POLL_INTERVAL)
                if self.reset_signal:
                    await asyncio.gather(
                        runner_monitor,
                        return_exceptions=True,
                    )
                    self.current_runner = None
                    self.reset_signal = False

                    return False

            if not runner.healthy:
                runner.interrupt()
                await asyncio.gather(
                    runner_monitor,
                    return_exceptions=True,
                )
                continue

            if await runner_monitor:
                self.assigned_task.set_output_stream(self.current_runner.output_stream)
                self.available = True
                self.assigned_task.completed = True
                print(self.assigned_task.get_output_stream())
                return True


            
        self.current_runner = None
        self.available = True
        return False


            
        
    

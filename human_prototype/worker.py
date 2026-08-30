from dataclasses import dataclass
from typing import Optional, Final
from task import Task
from runner import Runner
from runnerPool import RunnerPool
import asyncio

POLL_INTERVAL: Final[float] = 0.1
MAX_RETRIES: Final[int] = 3
@dataclass
class Worker:
    runner_pool: RunnerPool 
    worker_id: Final[str] = ""
    assigned_program_name: str = ""
    assigned_task: Optional[Task] = None
    current_runner: Optional[Runner] = None
    available: bool = True
    reset_signal: bool = False
    

    def is_available(self):
        return self.available

    
        
    def get_program_name(self):
        return self.assigned_program_name

    async def change_runner(self):
        previous_runner = self.current_runner
        self.current_runner = None
        if self.current_runner is not None:
            await self.runner_pool.put_runner(self.previous_runner)
        print(f"[INFO] worker: {self.worker_id} requesting a runner")
        runner = await self.runner_pool.get_runner()
        print(
            f"[INFO] worker: {self.worker_id} received runner: "
            f"{runner.id}"
        )
        return runner
    
    async def release_runner(self):
        
        if self.current_runner is not None:
            print(
                f"[INFO] worker: {self.worker_id} returning runner: "
                f"{self.current_runner.id}"
            )
            await self.runner_pool.put_runner(self.current_runner)
         

    def assign(self, program_name: str, task: Task) -> None:
        print(f"[INFO] assigning program: {program_name} to worker: {self.worker_id}")
        if not self.available or self.assigned_task is not None:
            raise RuntimeError(f"worker {self.worker_id} is not available")
        self.reset_signal = False
        self.assigned_program_name = program_name
        self.assigned_task = task
        self.available = False

    async def run(self):
        
        if self.assigned_task is None:
            print(
                f"[ERROR] worker: {self.worker_id} cannot run without "
                "an assigned task"
            )
            return

        self.available = False
        input_stream = self.assigned_task.get_input_stream()
        print(
            f"[INFO] worker: {self.worker_id} starting assigned program: "
            f"{self.assigned_task.assigned_program_name}"
        )
        
        for _ in range(MAX_RETRIES):

            if self.assigned_task.get_intermediate_stream():
                break

            runner = await self.change_runner()
            self.current_runner = runner

            runner_monitor = asyncio.create_task(
                runner.run(
                    self.assigned_task.assigned_program_name,
                    input_stream,
                )
            )

            while not runner_monitor.done() and runner.get_health():

                await asyncio.sleep(POLL_INTERVAL)

                if self.reset_signal:
                    await asyncio.gather(
                        runner_monitor,
                        return_exceptions=True,
                    )
                    await self.release_runner()
                    self.current_runner = None
                    self.reset_signal = False
                    self.assigned_task = None
                    return False

            if not runner.get_health():
                print(
                    f"[INFO] worker: {self.worker_id} detected unhealthy "
                    f"runner: {runner.id}; retrying assigned program"
                )
                runner.interrupt()
                await asyncio.gather(
                    runner_monitor,
                    return_exceptions=True,
                )
                await self.release_runner()
                self.current_runner = None
                continue

            if await runner_monitor:
                self.assigned_task.set_main_output(runner.output_stream)
                print(
                    f"[INFO] worker: {self.worker_id} finished assigned "
                    f"program: {self.assigned_task.assigned_program_name}"
                )
                break
        
        if len(self.assigned_task.main_output)==0:
            print(
                f"[ERROR] worker: {self.worker_id} could not complete "
                f"assigned program: {self.assigned_task.assigned_program_name}"
            )
            await self.release_runner()
            self.current_runner = None
            self.available = True
            self.assigned_task = None
            return False

        await self.assigned_task.update_intermediate_stream()

        # Leaf tasks do not need a second orchestration program. Their main
        # subprocess output is already the final task output.
        if not self.assigned_task.orchestration_program_name:
            self.assigned_task.set_output_stream(self.assigned_task.main_output)
            self.assigned_task.completed = True
            print(
                f"[INFO] worker: {self.worker_id} completed leaf program: "
                f"{self.assigned_task.assigned_program_name} with output: "
                f"{self.assigned_task.get_output_stream()}"
            )
            await self.release_runner()
            self.current_runner = None
            self.available = True
            self.assigned_task = None
            return True

        for _ in range(MAX_RETRIES):
            if(self.assigned_task.completed):
                self.available = True
                self.assigned_task.completed = True
                await self.release_runner()
                self.current_runner = None
                self.assigned_task = None
                return True
            
            runner = await self.change_runner()
            self.current_runner = runner
            print(
                f"[INFO] worker: {self.worker_id} starting orchestration "
                f"program: {self.assigned_task.orchestration_program_name}"
            )
            runner_monitor = asyncio.create_task(
                runner.run(
                    self.assigned_task.orchestration_program_name,
                    self.assigned_task.get_intermediate_stream(),
                )
            )

            while not runner_monitor.done() and runner.get_health():
                await asyncio.sleep(POLL_INTERVAL)
                if self.reset_signal:
                    await asyncio.gather(
                        runner_monitor,
                        return_exceptions=True,
                    )
                    await self.release_runner()
                    self.current_runner = None
                    self.reset_signal = False
                    self.assigned_task = None
                    return False

            if not runner.get_health():
                print(
                    f"[INFO] worker: {self.worker_id} detected unhealthy "
                    f"runner: {runner.id}; retrying orchestration program"
                )
                runner.interrupt()
                await asyncio.gather(
                    runner_monitor,
                    return_exceptions=True,
                )
                await self.release_runner()
                self.current_runner = None
                continue

            if await runner_monitor:
                self.assigned_task.set_output_stream(self.current_runner.output_stream)
                self.available = True
                self.assigned_task.completed = True
                await self.release_runner()
                self.current_runner = None
                print(
                    f"[INFO] worker: {self.worker_id} completed orchestration "
                    f"program: {self.assigned_task.orchestration_program_name} "
                    f"with output: {self.assigned_task.get_output_stream()}"
                )
                self.assigned_task = None
                return True


        await self.release_runner()
        self.current_runner = None
        self.available = True
        print(
            f"[ERROR] worker: {self.worker_id} could not complete "
            "orchestration program after retries"
        )
        self.assigned_task = None
        return False


            
        
    

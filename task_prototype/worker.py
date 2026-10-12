import asyncio
from dataclasses import dataclass, field
from typing import Final

from runner import Runner
from runnerPool import RunnerPool
from task import Task

POLL_INTERVAL: Final[float] = 0.1
MAX_RETRIES: Final[int] = 3

#Worker is responsible for task completions and task orchestrations

@dataclass
class Worker:
    runner_pool: RunnerPool 
    worker_id: Final[str] = ""
    assigned_program_name: str = ""
    assigned_task: Task | None = None
    current_runner: Runner | None = None
    available: bool = True
    reset_signal: bool = False
    reset_event: asyncio.Event = field(default_factory=asyncio.Event)
    

    def is_available(self):
        return self.available

    
        
    def get_program_name(self):
        return self.assigned_program_name


    #Spreads reset signal across other async processes in regards to Worker
    def reset(self):
        """Request cancellation of this worker's current assignment."""
        if self.assigned_task is None:
            self.reset_signal = False
            self.reset_event.clear()
            self.assigned_program_name = ""
            self.available = True
            return
        self.reset_signal = True
        self.reset_event.set()
        if self.current_runner is not None:
            self.current_runner.interrupt()


    #wait and get a new healthy runner 
    async def change_runner(self):
        previous_runner = self.current_runner
        self.current_runner = None
        if previous_runner is not None:
            await self.runner_pool.put_runner(previous_runner)
        print(f"[INFO] worker: {self.worker_id} requesting a runner")
        runner_request = asyncio.create_task(self.runner_pool.get_runner())
        reset_request = asyncio.create_task(self.reset_event.wait())
        done, pending = await asyncio.wait(
            {runner_request, reset_request},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for pending_task in pending:
            pending_task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if reset_request in done and reset_request.result():
            if runner_request in done:
                await self.runner_pool.put_runner(runner_request.result())
            return None
        runner = runner_request.result()
        print(
            f"[INFO] worker: {self.worker_id} received runner: "
            f"{runner.id}"
        )
        return runner
    
    async def release_runner(self):
        runner = self.current_runner
        self.current_runner = None
        if runner is not None:
            print(
                f"[INFO] worker: {self.worker_id} returning runner: "
                f"{runner.id}"
            )
            await self.runner_pool.put_runner(runner)
         

    def assign(self, program_name: str, task: Task) -> None:
        print(f"[INFO] assigning program: {program_name} to worker: {self.worker_id}")
        if not self.available or self.assigned_task is not None:
            raise RuntimeError(f"worker {self.worker_id} is not available")
        self.reset_signal = False
        self.reset_event.clear()
        self.assigned_program_name = program_name
        self.assigned_task = task
        self.available = False

# Runs either assigned task or orchestration task
# Checks for all cases: reset signal and runner unhealthy situations
# For every unsuccessful task completion attempt for loop till MAX_RETRIES
# Too async too cool
    async def run(self):
        if self.assigned_task is None:
            print(
                f"[ERROR] worker: {self.worker_id} cannot run without "
                "an assigned task"
            )
            return False

        self.available = False
        assigned_task = self.assigned_task
        input_stream = assigned_task.get_input_stream()
        print(
            f"[INFO] worker: {self.worker_id} starting assigned program: "
            f"{assigned_task.assigned_program_name}"
        )
        try:
            main_output = await self._run_stage(
                assigned_task.assigned_program_name,
                input_stream,
                "assigned program",
            )
            if main_output is None:
                print(
                    f"[ERROR] worker: {self.worker_id} could not complete "
                    f"assigned program: {assigned_task.assigned_program_name}"
                )
                return False

            assigned_task.set_main_output(main_output)
            print(
                f"[INFO] worker: {self.worker_id} finished assigned "
                f"program: {assigned_task.assigned_program_name}"
            )
            await self.release_runner()

            if not assigned_task.orchestration_program_name:
                assigned_task.set_output_stream(main_output)
                assigned_task.completed = True
                return True

            while not all(task.completed for task in assigned_task.list_subtasks):
                if self.reset_event.is_set():
                    return False
                await asyncio.sleep(POLL_INTERVAL)

            await assigned_task.update_intermediate_stream()
            orchestration_output = await self._run_stage(
                assigned_task.orchestration_program_name,
                assigned_task.get_intermediate_stream(),
                "orchestration program",
            )
            if orchestration_output is None:
                print(
                    f"[ERROR] worker: {self.worker_id} could not complete "
                    "orchestration program after retries"
                )
                return False
            assigned_task.set_output_stream(orchestration_output)
            assigned_task.completed = True
            print(
                f"[INFO] worker: {self.worker_id} completed orchestration "
                f"program: {assigned_task.orchestration_program_name} "
                f"with output: {assigned_task.get_output_stream()}"
            )
            return True
        except asyncio.CancelledError:
            if self.current_runner is not None:
                self.current_runner.interrupt()
            raise
        finally:
            await self.release_runner()
            self.available = True
            self.assigned_task = None
            self.reset_signal = False
            self.reset_event.clear()


# Helper method for run
    async def _run_stage(self, program_name, input_stream, phase):
        for _ in range(MAX_RETRIES):
            if self.reset_event.is_set():
                return None
            runner = await self.change_runner()
            if runner is None:
                return None
            self.current_runner = runner
            runner_monitor = asyncio.create_task(
                runner.run(program_name, input_stream)
            )

            while not runner_monitor.done():
                if self.reset_event.is_set():
                    runner_monitor.cancel()
                    await asyncio.gather(runner_monitor, return_exceptions=True)
                    await self.release_runner()
                    return None
                if not runner.get_health():
                    print(
                        f"[INFO] worker: {self.worker_id} detected unhealthy "
                        f"runner: {runner.id}; retrying {phase}"
                    )
                    runner_monitor.cancel()
                    await asyncio.gather(runner_monitor, return_exceptions=True)
                    await self.release_runner()
                    break
                await asyncio.sleep(POLL_INTERVAL)
            else:
                if await runner_monitor:
                    return runner.output_stream
                await self.release_runner()
                continue
        return None


            
        
    

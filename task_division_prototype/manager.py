import asyncio
import uuid
from dataclasses import dataclass, field

from runnerPool import RunnerPool
from task import Task
from worker import Worker

# INFO: MANAGER CLASS RESPONSIBILITY MAY DIVERGE IN FUTURE

#Manager is responsible for partitioning of tasks and assigning them to workers and program-scope operations
@dataclass()
class Manager:
    list_workers: list[Worker] = field(default_factory=list)
    program_dict: dict[str, Task] = field(default_factory=dict)
    background_tasks: set[asyncio.Task[bool]] = field(default_factory=set)
    runner_pool: RunnerPool = None
    stale_monitor: asyncio.Task | None = None

    
    #stub for now
    def create_task(self, subprogram_name: str):
        #put subprogram_name in task
        return None
    

    #Sends reset signal to every worker, doesn't guarantee ultimate reset upon finishing 
    def kill_program(self, program_name: str):
        for worker in self.list_workers:
            if worker.get_program_name() == program_name:
                worker.reset()
        self.program_dict.pop(program_name, None)
    
    #Tracing of a program status thru the workers
    def get_program_status(self, program_name: str) -> str:
        return self.program_dict[program_name].recursive_status()
    

    #Adds worker to pool with id
    def add_workers(self, n: int = 4):
        for _ in range(n):
            self.list_workers.append(Worker(worker_id=str(uuid.uuid4()),runner_pool=self.runner_pool))
    

    #Runs every worker associated with the program and returns their background task involved
    def run_program(self, program_name: str):
        jobs = []
        for worker in self.list_workers:
            if worker.get_program_name() == program_name:
                background_task = asyncio.create_task(worker.run())
                self.background_tasks.add(background_task)
                background_task.add_done_callback(self.background_tasks.discard)
                jobs.append(background_task)
        return jobs

  
                
    #demo code doesn't represent final behaviour
    def partition_program(self, program_name: str):
        root_task = self.demo_partition()
        self.program_dict[program_name] = root_task
        tasks = [root_task, *root_task.list_subtasks]
        available_workers = self.get_available_workers()
        if len(available_workers) < len(tasks):
            raise RuntimeError(
                f"program {program_name!r} needs {len(tasks)} available workers"
            )
        for worker, task in zip(available_workers, tasks):
            worker.assign(program_name, task)

    #demo code doesn't represent final behaviour
    def demo_partition(self) -> Task:
        task_2 = Task(assigned_program_name="demo/program1_t2.py", input_stream="7")
        task_3 = Task(assigned_program_name="demo/program1_t3.py", input_stream="7")
        return Task(
            assigned_program_name="demo/program1_t1.py",
            input_stream="7",
            orchestration_program_name="demo/program1_t1_or.py",
            list_subtasks=[task_2, task_3],
        )

    #for demo only
    @classmethod
    async def demo(cls, *, setup_only: bool = False) -> "Manager":
        manager = cls()
        runnerPool = RunnerPool()
        manager.runner_pool = runnerPool
        manager.stale_monitor = asyncio.create_task(
            runnerPool.poll_stales(interval=0.1)
        )
        print("[INFO] Initializing environment")
        manager.add_workers(4)
        manager.runner_pool.add_runner(6)
        manager.partition_program("demo/program1")
        if setup_only:
            return manager
        print("[INFO] Running program1")
        manager.run_program("demo/program1")
        for _ in range(200):
            await asyncio.sleep(0.1)
        if manager.stale_monitor is not None:
            manager.stale_monitor.cancel()
            await asyncio.gather(
                manager.stale_monitor,
                return_exceptions=True,
            )
            manager.stale_monitor = None
        return manager

    #Effective cleanup after ultimate use of manager
    async def shutdown(self):
        for worker in self.list_workers:
            if worker.assigned_task is not None:
                worker.reset()

        active_tasks = list(self.background_tasks)
        if active_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*active_tasks, return_exceptions=True),
                    timeout=2,
                )
            except TimeoutError:
                for task in active_tasks:
                    task.cancel()
                await asyncio.gather(*active_tasks, return_exceptions=True)

        if self.stale_monitor is not None:
            self.stale_monitor.cancel()
            await asyncio.gather(self.stale_monitor, return_exceptions=True)
            self.stale_monitor = None

        for runner in self.runner_pool.list_runners:
            if runner.process is not None and runner.process.returncode is None:
                runner.interrupt()
                await runner.process.wait()

   

    def get_available_workers(self) -> list[Worker]:
        available_workers: list[Worker] = []
        for worker in self.list_workers:
            if worker.is_available():
                available_workers.append(worker)
        return available_workers
    
    
    
    def get_worker_by_id(self, worker_id: str)->Worker:
        for worker in self.list_workers:
            if worker.worker_id==worker_id:
                return worker

if __name__=="__main__":
    manager = asyncio.run(Manager.demo())
    
    


    

    
